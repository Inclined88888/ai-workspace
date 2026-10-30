//! Ask from anywhere (feat/v02-anywhere-voice): a global shortcut opens a small floating ask bar over any app.
//!
//! Default shortcut: Option+Space on a Mac, Alt+Space on Windows and Linux; changeable in Settings → Ask from anywhere
//! (saved in `<data dir>/ask-anywhere.json`). When it is pressed, *before* the ask bar takes focus, the shell asks the
//! backend to grab the selected text (and, if switched on, a screenshot of the window) from the app in front:
//! `POST /api/anywhere/native-grab` with the per-launch token. Then it shows the `askbar` window on `/ask-bar`.

use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::sidecars::{self, Supervisor};

pub const DEFAULT_SHORTCUT: &str = "Alt+Space"; // Alt is the Option key on a Mac
const LABEL: &str = "askbar";

#[derive(Default)]
pub struct Anywhere {
    current: Mutex<Option<(String, Shortcut)>>,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct AnywhereSettings {
    pub shortcut: String,
    pub enabled: bool,
}

impl Default for AnywhereSettings {
    fn default() -> Self {
        Self { shortcut: DEFAULT_SHORTCUT.into(), enabled: true }
    }
}

fn settings_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    sidecars::data_dir(app).ok().map(|d| d.join("ask-anywhere.json"))
}

fn load(app: &AppHandle) -> AnywhereSettings {
    settings_path(app)
        .and_then(|p| fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save(app: &AppHandle, s: &AnywhereSettings) {
    if let Some(p) = settings_path(app) {
        let _ = fs::write(p, serde_json::to_string_pretty(s).unwrap_or_default());
    }
}

/// The plugin, with the one handler every registered shortcut goes to.
pub fn plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, _shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                let app = app.clone();
                std::thread::spawn(move || {
                    native_grab(&app);
                    let _ = show_ask_bar(&app);
                });
            }
        })
        .build()
}

/// Register the saved shortcut at start-up. A clash with another app is logged, not fatal: Settings can pick another.
pub fn register_saved(app: &AppHandle) {
    let s = load(app);
    if !s.enabled {
        return;
    }
    if let Err(e) = apply(app, &s.shortcut) {
        log::warn!("ask shortcut {} could not be registered: {e}", s.shortcut);
    }
}

fn apply(app: &AppHandle, accel: &str) -> Result<(), String> {
    let shortcut: Shortcut = accel
        .parse()
        .map_err(|e| format!("\"{accel}\" isn't a shortcut I can use ({e})"))?;
    let state = app.state::<Anywhere>();
    let mut cur = state.current.lock().unwrap();
    let gs = app.global_shortcut();
    if let Some((_, old)) = cur.take() {
        let _ = gs.unregister(old);
    }
    gs.register(shortcut)
        .map_err(|e| format!("{accel} is already used by another app or the system ({e})"))?;
    *cur = Some((accel.to_string(), shortcut));
    Ok(())
}

/// One plain HTTP/1.0 POST to the local backend (no HTTP client crate), with a short timeout.
fn native_grab(app: &AppHandle) {
    let sup = app.state::<Supervisor>();
    let (Some(url), Some(token)) = (sup.workbench_url(), sup.workbench_token()) else {
        return;
    };
    let hostport = url.trim_start_matches("http://").to_string();
    let Ok(addr) = hostport.parse::<std::net::SocketAddr>() else {
        return;
    };
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(6)));
    let req = format!(
        "POST /api/anywhere/native-grab HTTP/1.0\r\nHost: {hostport}\r\nX-Workbench: 1\r\nX-Desktop-Token: {token}\r\nContent-Length: 0\r\n\r\n"
    );
    if s.write_all(req.as_bytes()).is_ok() {
        let mut buf = Vec::new();
        let _ = s.read_to_end(&mut buf); // wait for the grab to finish before the ask bar steals focus
    }
}

fn show_ask_bar(app: &AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(LABEL) {
        let _ = w.eval("window.__askBarOpen && window.__askBarOpen()");
        let _ = w.center();
        w.show().map_err(|e| e.to_string())?;
        let _ = w.set_focus();
        return Ok(());
    }
    let Some(base) = app.state::<Supervisor>().workbench_url() else {
        return Err("still starting".into());
    };
    let url = format!("{base}/ask-bar").parse().map_err(|e| format!("{e}"))?;
    let app2 = app.clone();
    // Windows must be built off the shortcut thread on some platforms; the main thread is always safe.
    app.run_on_main_thread(move || {
        let built = WebviewWindowBuilder::new(&app2, LABEL, WebviewUrl::External(url))
            .title("Ask")
            .inner_size(720.0, 480.0)
            .min_inner_size(420.0, 160.0)
            .decorations(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .resizable(true)
            .center()
            .focused(true)
            .build();
        if let Err(e) = built {
            log::error!("ask bar could not open: {e}");
        }
    })
    .map_err(|e| e.to_string())
}

#[derive(Serialize)]
pub struct ShortcutInfo {
    shortcut: String,
    default: &'static str,
    enabled: bool,
    registered: bool,
}

#[tauri::command]
pub fn get_ask_shortcut(app: AppHandle) -> ShortcutInfo {
    let s = load(&app);
    let registered = app.state::<Anywhere>().current.lock().unwrap().is_some();
    ShortcutInfo { shortcut: s.shortcut, default: DEFAULT_SHORTCUT, enabled: s.enabled, registered }
}

/// Change (or switch off) the shortcut. A shortcut that can't be registered is refused and the old one kept.
#[tauri::command]
pub fn set_ask_shortcut(app: AppHandle, shortcut: String, enabled: bool) -> Result<ShortcutInfo, String> {
    let old = load(&app);
    let accel = if shortcut.trim().is_empty() { DEFAULT_SHORTCUT.to_string() } else { shortcut.trim().to_string() };
    if enabled {
        if let Err(e) = apply(&app, &accel) {
            if old.enabled {
                let _ = apply(&app, &old.shortcut);
            }
            return Err(e);
        }
    } else if let Some((_, cur)) = app.state::<Anywhere>().current.lock().unwrap().take() {
        let _ = app.global_shortcut().unregister(cur);
    }
    save(&app, &AnywhereSettings { shortcut: accel, enabled });
    Ok(get_ask_shortcut(app))
}

/// Close the ask bar. With `restore_focus`, hand focus back to the app the question came from (for Paste).
#[tauri::command]
pub fn hide_ask_bar(app: AppHandle, restore_focus: bool) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(LABEL) {
        w.hide().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    if restore_focus {
        let main_visible = app
            .get_webview_window("main")
            .and_then(|w| w.is_focused().ok())
            .unwrap_or(false);
        if !main_visible {
            let _ = app.hide(); // macOS gives focus back to the previous app
        }
    }
    #[cfg(not(target_os = "macos"))]
    let _ = restore_focus; // Windows and Linux return focus to the previous window when ours hides
    Ok(())
}

/// "Do it in Chat": bring the main window forward and hand it the question (it opens Chat with the draft filled in).
#[tauri::command]
pub fn show_main_window(app: AppHandle, draft: Option<String>) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(LABEL) {
        let _ = w.hide();
    }
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        w.show().map_err(|e| e.to_string())?;
        let _ = w.set_focus();
        if let Some(d) = draft {
            let js = serde_json::to_string(&d).map_err(|e| e.to_string())?;
            let _ = w.eval(&format!("window.__wbHandoff && window.__wbHandoff({js})"));
        }
    }
    Ok(())
}
