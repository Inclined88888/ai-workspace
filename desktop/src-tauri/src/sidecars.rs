//! Sidecar processes: the Workbench backend now, and the agents later phases add.
//!
//! Every long-running helper the app ships is a frozen executable listed in `tauri.conf.json` →
//! `bundle.externalBin` and described in [`SIDECARS`]. The [`Supervisor`] owns the running children, kills them on
//! exit, and reports their state to the UI (`list_sidecars`). Each child also exits by itself if this shell dies,
//! because it watches its stdin (`--exit-with-parent`).

use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// A helper process the app can run. `binary` is the externalBin name without the target-triple suffix.
#[derive(Clone, Copy, Serialize)]
pub struct SidecarSpec {
    pub name: &'static str,
    pub binary: &'static str,
    pub description: &'static str,
    /// Roadmap phase that ships it (1 = this release).
    pub phase: u8,
    pub enabled: bool,
}

/// Registry of sidecars. Later phases flip `enabled`, add the binary to `bundle.externalBin`, and start it the same
/// way as Workbench (see [`start_workbench`]); the UI already lists them so users see what is coming.
pub const SIDECARS: &[SidecarSpec] = &[
    SidecarSpec {
        name: "workbench",
        binary: "workbench-server",
        description: "Workbench backend: chat, run log, approvals, budgets, MCP server",
        phase: 1,
        enabled: true,
    },
    SidecarSpec {
        name: "terminal-agent",
        binary: "terminal-agent",
        description: "Terminal coding agent that works with any model, including local ones",
        phase: 2,
        enabled: false,
    },
    SidecarSpec {
        name: "browser-agent",
        binary: "browser-agent",
        description: "AI browser agent (the repo's browser-agent/, frozen with its own Chromium)",
        phase: 3,
        enabled: false,
    },
    SidecarSpec {
        name: "computer-use",
        binary: "computer-use",
        description: "Computer-use and MCP bridge to apps on this machine (every action goes through approvals)",
        phase: 4,
        enabled: false,
    },
];

#[derive(Default)]
struct State {
    children: HashMap<String, CommandChild>,
    status: HashMap<String, String>,
    workbench_url: Option<String>,
}

#[derive(Default)]
pub struct Supervisor {
    state: Mutex<State>,
}

impl Supervisor {
    fn set_status(&self, name: &str, status: &str) {
        self.state
            .lock()
            .unwrap()
            .status
            .insert(name.into(), status.into());
    }

    pub fn workbench_url(&self) -> Option<String> {
        self.state.lock().unwrap().workbench_url.clone()
    }

    pub fn stop_all(&self) {
        let mut st = self.state.lock().unwrap();
        for (name, child) in st.children.drain() {
            log::info!("stopping sidecar {name}");
            let _ = child.kill();
        }
        for v in st.status.values_mut() {
            *v = "stopped".into();
        }
    }
}

#[derive(Serialize)]
pub struct SidecarOut {
    #[serde(flatten)]
    spec: SidecarSpec,
    status: String,
}

#[tauri::command]
pub fn list_sidecars(sup: tauri::State<'_, Supervisor>) -> Vec<SidecarOut> {
    let st = sup.state.lock().unwrap();
    SIDECARS
        .iter()
        .map(|s| SidecarOut {
            spec: *s,
            status: st.status.get(s.name).cloned().unwrap_or_else(|| {
                if s.enabled {
                    "not started"
                } else {
                    "coming soon"
                }
                .into()
            }),
        })
        .collect()
}

pub fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    Ok(dir)
}

pub fn log_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app.path().app_log_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    Ok(dir)
}

fn free_port() -> std::io::Result<u16> {
    Ok(
        TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))?
            .local_addr()?
            .port(),
    )
}

fn random_token() -> String {
    let mut buf = [0u8; 32];
    getrandom::getrandom(&mut buf).expect("OS random source");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// One plain HTTP/1.0 GET so the shell needs no HTTP client crate.
fn healthy(port: u16) -> bool {
    let addr = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut s) = TcpStream::connect_timeout(&addr.into(), Duration::from_millis(500)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    if s.write_all(b"GET /healthz HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut head = [0u8; 16];
    matches!(s.read(&mut head), Ok(n) if n >= 12 && &head[9..12] == b"200")
}

/// Tell the splash page what is happening (it defines `window.bootStatus`).
fn splash(app: &AppHandle, text: &str, error: bool) {
    if let Some(w) = app.get_webview_window("main") {
        let js = format!(
            "window.bootStatus && window.bootStatus({}, {})",
            serde_json::to_string(text).unwrap(),
            error
        );
        let _ = w.eval(&js);
    }
}

/// Start the Workbench backend and point the window at it. Runs in the background so the splash paints at once.
pub fn start_workbench(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        if let Err(e) = boot(&app).await {
            log::error!("workbench failed to start: {e}");
            app.state::<Supervisor>().set_status("workbench", "failed");
            let logs = log_dir(&app)
                .map(|d| d.join("workbench.log").display().to_string())
                .unwrap_or_default();
            splash(
                &app,
                &format!("Workbench could not start: {e}. Details: {logs}"),
                true,
            );
        }
    });
}

async fn boot(app: &AppHandle) -> Result<(), String> {
    let sup = app.state::<Supervisor>();
    sup.set_status("workbench", "starting");
    let data = data_dir(app)?;
    let logs = log_dir(app)?;
    let port = free_port().map_err(|e| e.to_string())?;
    let token = random_token();
    let args = [
        "--port".to_string(),
        port.to_string(),
        "--data-dir".to_string(),
        data.display().to_string(),
        "--exit-with-parent".to_string(),
    ];

    // Development: WORKBENCH_SIDECAR_CMD="python3 desktop/sidecar/workbench_server.py" runs the unfrozen backend.
    let cmd = match std::env::var("WORKBENCH_SIDECAR_CMD") {
        Ok(dev) if !dev.trim().is_empty() => {
            let mut parts = dev.split_whitespace();
            let program = parts.next().unwrap().to_string();
            let pre: Vec<String> = parts.map(str::to_string).collect();
            app.shell().command(program).args(pre).args(args)
        }
        _ => app
            .shell()
            .sidecar("workbench-server")
            .map_err(|e| e.to_string())?
            .args(args),
    }
    .env("WORKBENCH_DESKTOP_TOKEN", &token)
    .env("PYTHONUNBUFFERED", "1");

    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("cannot run the backend: {e}"))?;
    sup.state
        .lock()
        .unwrap()
        .children
        .insert("workbench".into(), child);

    // Backend output goes to <log dir>/workbench.log (started fresh each launch, previous one kept as .1).
    let log_path = logs.join("workbench.log");
    let _ = fs::rename(&log_path, logs.join("workbench.log.1"));
    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .ok();
        while let Some(ev) = rx.recv().await {
            match ev {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    if let Some(f) = file.as_mut() {
                        let _ = f.write_all(&line);
                        if !line.ends_with(b"\n") {
                            let _ = f.write_all(b"\n");
                        }
                    }
                }
                CommandEvent::Terminated(p) => {
                    let sup = app2.state::<Supervisor>();
                    sup.state.lock().unwrap().children.remove("workbench");
                    sup.set_status("workbench", "stopped");
                    let _ = app2.emit("sidecar-exited", ("workbench", p.code));
                    log::warn!("workbench exited with {:?}", p.code);
                }
                CommandEvent::Error(e) => log::error!("workbench: {e}"),
                _ => {}
            }
        }
    });

    splash(app, "Starting your workspace…", false);
    // A one-file PyInstaller build unpacks itself on first launch, which can take a while on slow disks.
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if healthy(port) {
            break;
        }
        if !sup.state.lock().unwrap().children.contains_key("workbench") {
            return Err("the backend exited during start-up".into());
        }
        if Instant::now() > deadline {
            return Err("the backend did not answer within 2 minutes".into());
        }
        tokio_sleep(Duration::from_millis(250)).await;
    }
    let base = format!("http://127.0.0.1:{port}");
    sup.state.lock().unwrap().workbench_url = Some(base.clone());
    sup.set_status("workbench", "running");
    let url = format!("{base}/api/desktop/session?token={token}");
    if let Some(w) = app.get_webview_window("main") {
        w.navigate(url.parse().map_err(|e| format!("{e}"))?)
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

async fn tokio_sleep(d: Duration) {
    // tauri's async runtime is tokio; spawn_blocking keeps us off a tokio feature flag.
    let _ = tauri::async_runtime::spawn_blocking(move || std::thread::sleep(d)).await;
}
