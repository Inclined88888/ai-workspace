//! Native desktop integration: OS notifications, the tray icon (close to tray) and `aiworkspace://` links.
//!
//! - Notifications: the Workbench page calls `notify` when a reply finishes or an approval is held while the window
//!   is in the background. In a plain browser the page uses the Web Notification API instead (desktop.ts).
//! - Tray: Open, New chat, Automations, Quit. Closing the main window hides it to the tray; Quit really quits.
//! - Links: `aiworkspace://chat?q=…` opens a new chat with the text pre-filled (never sent: a link from a web page
//!   or another app must not be able to make the assistant act), `aiworkspace://new` opens an empty chat and
//!   `aiworkspace://open/<route>` opens a page, e.g. `aiworkspace://open/automations`.
//!   A link that arrives before the page has loaded is kept and handed over by `desktop_ready`.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Url, WebviewWindow, WindowEvent};
use tauri_plugin_notification::NotificationExt;

pub const SCHEME: &str = "aiworkspace";
const MAX_DRAFT: usize = 4000;
const MAX_TITLE: usize = 120;
const MAX_BODY: usize = 400;

/// Where the UI should go. `draft` pre-fills the chat composer; it is never sent on its own.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Link {
    pub path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub draft: Option<String>,
}

#[derive(Default)]
pub struct Native {
    quitting: AtomicBool,
    page_ready: AtomicBool,
    pending: Mutex<Option<Link>>,
}

fn clip(s: &str, max: usize) -> String {
    s.chars().take(max).collect()
}

fn safe_segment(s: &str) -> bool {
    !s.is_empty() && s.len() <= 64 && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Turn an `aiworkspace://` URL into a page route. Anything unexpected is refused (None), so a crafted link
/// can't reach the API, leave the app's own pages, or smuggle a query string through.
pub fn parse_link(url: &Url) -> Option<Link> {
    if url.scheme() != SCHEME {
        return None;
    }
    let host = url.host_str().unwrap_or("");
    let segments: Vec<&str> = url.path().split('/').filter(|s| !s.is_empty()).collect();
    match host {
        "chat" | "new" if segments.is_empty() => {
            let draft = url
                .query_pairs()
                .find(|(k, _)| k == "q")
                .map(|(_, v)| clip(v.trim(), MAX_DRAFT))
                .filter(|v| !v.is_empty());
            Some(Link { path: "/assistant".into(), draft: if host == "chat" { draft } else { None } })
        }
        "open" => {
            if segments.len() > 4 || !segments.iter().all(|s| safe_segment(s)) {
                return None;
            }
            if segments.first().map(|s| s.eq_ignore_ascii_case("api")).unwrap_or(false) {
                return None;
            }
            Some(Link { path: format!("/{}", segments.join("/")), draft: None })
        }
        _ => None,
    }
}

pub fn show_main(app: &AppHandle) -> Option<WebviewWindow> {
    let w = app.get_webview_window("main")?;
    let _ = w.unminimize();
    let _ = w.show();
    let _ = w.set_focus();
    Some(w)
}

/// Bring the window up and send the page to `link`, or keep it until the page says it is ready.
pub fn route(app: &AppHandle, link: Link) {
    let state = app.state::<Native>();
    let Some(w) = show_main(app) else {
        *state.pending.lock().unwrap() = Some(link);
        return;
    };
    if !state.page_ready.load(Ordering::SeqCst) {
        *state.pending.lock().unwrap() = Some(link);
        return;
    }
    let json = serde_json::to_string(&link).unwrap_or_else(|_| "null".into());
    // The page registers window.__aiwRoute (DesktopBridge.tsx). If it is gone (reload), keep the link for it.
    if w.eval(&format!("window.__aiwRoute ? window.__aiwRoute({json}) : 0")).is_err() {
        *state.pending.lock().unwrap() = Some(link);
    }
}

pub fn open_urls(app: &AppHandle, urls: &[Url]) {
    for u in urls {
        match parse_link(u) {
            Some(link) => route(app, link),
            None => log::warn!("ignored link {u}"),
        }
    }
}

/// Close to tray: hide the main window instead of closing it, unless the user chose Quit.
pub fn on_window_event(window: &tauri::Window, event: &WindowEvent) {
    if let WindowEvent::CloseRequested { api, .. } = event {
        if window.label() == "main" && !window.app_handle().state::<Native>().quitting.load(Ordering::SeqCst) {
            api.prevent_close();
            let _ = window.hide();
        }
    }
}

pub fn quit(app: &AppHandle) {
    app.state::<Native>().quitting.store(true, Ordering::SeqCst);
    app.exit(0);
}

pub fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open AI Workspace", true, None::<&str>)?;
    let new_chat = MenuItem::with_id(app, "new_chat", "New chat", true, None::<&str>)?;
    let automations = MenuItem::with_id(app, "automations", "Automations", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit AI Workspace", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &new_chat, &automations, &sep, &quit_item])?;
    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("AI Workspace")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                show_main(app);
            }
            "new_chat" => route(app, Link { path: "/assistant".into(), draft: None }),
            "automations" => route(app, Link { path: "/automations".into(), draft: None }),
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

/// Show an OS notification. Text is clipped; the page decides when (only while the window is in the background).
#[tauri::command]
pub fn notify(app: AppHandle, title: String, body: Option<String>) -> Result<(), String> {
    let mut n = app.notification().builder().title(clip(title.trim(), MAX_TITLE));
    if let Some(b) = body.as_deref().map(str::trim).filter(|b| !b.is_empty()) {
        n = n.body(clip(b, MAX_BODY));
    }
    n.show().map_err(|e| e.to_string())
}

/// The page has loaded and registered its router hook: from now on links go straight to it. Returns a link that
/// arrived before that (e.g. the one that launched the app).
#[tauri::command]
pub fn desktop_ready(app: AppHandle) -> Option<Link> {
    let state = app.state::<Native>();
    state.page_ready.store(true, Ordering::SeqCst);
    let pending = state.pending.lock().unwrap().take();
    pending
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(s: &str) -> Option<Link> {
        parse_link(&Url::parse(s).unwrap())
    }

    #[test]
    fn chat_prefills_but_only_from_q() {
        assert_eq!(p("aiworkspace://chat?q=hello%20there"), Some(Link { path: "/assistant".into(), draft: Some("hello there".into()) }));
        assert_eq!(p("aiworkspace://chat"), Some(Link { path: "/assistant".into(), draft: None }));
        assert_eq!(p("aiworkspace://chat?q=%20%20"), Some(Link { path: "/assistant".into(), draft: None }));
        assert_eq!(p("aiworkspace://new?q=x"), Some(Link { path: "/assistant".into(), draft: None }));
        assert_eq!(p("aiworkspace://chat/extra?q=x"), None);
    }

    #[test]
    fn draft_is_clipped() {
        let long = "a".repeat(MAX_DRAFT + 50);
        let l = p(&format!("aiworkspace://chat?q={long}")).unwrap();
        assert_eq!(l.draft.unwrap().chars().count(), MAX_DRAFT);
    }

    #[test]
    fn open_routes_are_plain_app_pages() {
        assert_eq!(p("aiworkspace://open/automations"), Some(Link { path: "/automations".into(), draft: None }));
        assert_eq!(p("aiworkspace://open/assistant/12"), Some(Link { path: "/assistant/12".into(), draft: None }));
        assert_eq!(p("aiworkspace://open/"), Some(Link { path: "/".into(), draft: None }));
        // query strings are dropped, not passed through
        assert_eq!(p("aiworkspace://open/settings?x=1"), Some(Link { path: "/settings".into(), draft: None }));
    }

    #[test]
    fn refuses_anything_else() {
        assert_eq!(p("aiworkspace://open/api/settings"), None);
        assert_eq!(p("aiworkspace://open/API/x"), None);
        assert_eq!(p("aiworkspace://open/a%2F..%2Fb"), None);
        assert_eq!(p("aiworkspace://open/<script>"), None);
        assert_eq!(p("aiworkspace://open/a/b/c/d/e"), None);
        assert_eq!(p("aiworkspace://evil/x"), None);
        assert_eq!(p("https://open/automations"), None);
    }
}
