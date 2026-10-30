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
mod sidecars;
mod updater;

use tauri::{Manager, RunEvent};

pub fn run() {
    let builder = tauri::Builder::default()
        // Must be first: a second launch focuses the running window instead of starting another backend.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(anywhere::plugin()); // feat/v02-anywhere-voice

    updater::register(builder)
        .manage(sidecars::Supervisor::default())
        .manage(anywhere::Anywhere::default())
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
        ])
        .setup(|app| {
            sidecars::start_workbench(app.handle().clone());
            updater::check_on_start(app.handle().clone());
            anywhere::register_saved(app.handle()); // feat/v02-anywhere-voice
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the AI Workspace app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                app.state::<sidecars::Supervisor>().stop_all();
            }
        });
}
