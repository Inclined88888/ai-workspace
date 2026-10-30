fn main() {
    // App commands must be listed so capabilities can grant them to the Workbench page, which the window loads
    // from http://127.0.0.1:<port> (a "remote" origin in Tauri's terms). See capabilities/default.json.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "app_info",
            "list_sidecars",
            "request_approval",
            "open_data_dir",
            "open_logs",
            // feat/v02-anywhere-voice
            "get_ask_shortcut",
            "set_ask_shortcut",
            "hide_ask_bar",
            "show_main_window",
        ]),
    ))
    .expect("failed to run tauri-build");
}
