//! AI Workspace desktop shell.
//!
//! The window first shows `ui/index.html` (a splash), while [`sidecars::start_workbench`] starts the frozen
//! Workbench backend on a free 127.0.0.1 port with a per-user data directory. Once `/healthz` answers, the window
//! navigates to `/api/desktop/session?token=…`, which signs the window in and lands on the welcome flow or chat.
//!
//! Extension points for later phases:
//! - [`sidecars::SIDECARS`]: the terminal coding agent, AI browser agent and computer-use / MCP layer register here.
//! - [`approvals::request_approval`]: native approve / deny prompt any part of the app can raise.
//! - [`updater`]: in-app updates, compiled in only with `--features updater` once signing keys exist.

mod anywhere; // feat/v02-anywhere-voice: Ask from anywhere
mod approvals;
mod commands;
mod native; // notifications, tray (close to tray), aiworkspace:// links
mod sidecars;
mod updater;

use tauri::{Manager, RunEvent};
use tauri_plugin_deep_link::DeepLinkExt;

pub fn run() {
    let builder = tauri::Builder::default()
        // Must be first: a second launch focuses the running window instead of starting another backend.
        // With its deep-link feature, a link that launches a second copy is forwarded to deep_link().on_open_url.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            native::show_main(app);
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(anywhere::plugin()); // feat/v02-anywhere-voice

    updater::register(builder)
        .manage(sidecars::Supervisor::default())
        .manage(anywhere::Anywhere::default())
        .manage(native::Native::default())
        .on_window_event(native::on_window_event)
        .invoke_handler(tauri::generate_handler![
            commands::app_info,
            commands::open_data_dir,
            commands::open_logs,
            sidecars::list_sidecars,
            approvals::request_approval,
            // feat/v02-anywhere-voice
            anywhere::get_ask_shortcut,
            anywhere::set_ask_shortcut,
            anywhere::hide_ask_bar,
            anywhere::show_main_window,
            native::notify,
            native::desktop_ready,
        ])
        .setup(|app| {
            sidecars::start_workbench(app.handle().clone());
            updater::check_on_start(app.handle().clone());
            anywhere::register_saved(app.handle()); // feat/v02-anywhere-voice
            if let Err(e) = native::setup_tray(app.handle()) {
                log::warn!("tray icon unavailable: {e}"); // e.g. a Linux desktop without a status area
            }
            let handle = app.handle().clone();
            app.deep_link().on_open_url(move |event| native::open_urls(&handle, &event.urls()));
            if let Ok(Some(urls)) = app.deep_link().get_current() {
                native::open_urls(app.handle(), &urls); // the link that launched the app
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the AI Workspace app")
        .run(|app, event| match event {
            RunEvent::Exit => app.state::<sidecars::Supervisor>().stop_all(),
            // macOS: clicking the Dock icon after closing to tray brings the window back
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => {
                native::show_main(app);
            }
            _ => {}
        });
}
