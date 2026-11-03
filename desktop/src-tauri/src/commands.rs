//! Small commands the Workbench page calls when it runs inside the desktop app (see workbench/frontend/src/desktop.ts).

use serde::Serialize;
use tauri::AppHandle;
use tauri_plugin_opener::OpenerExt;

use crate::sidecars::{self, Supervisor};

#[derive(Serialize)]
pub struct AppInfo {
    version: String,
    platform: &'static str,
    arch: &'static str,
    data_dir: String,
    log_dir: String,
    workbench_url: Option<String>,
    updater: bool,
}

#[tauri::command]
pub fn app_info(app: AppHandle, sup: tauri::State<'_, Supervisor>) -> Result<AppInfo, String> {
    Ok(AppInfo {
        version: app.package_info().version.to_string(),
        platform: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        data_dir: sidecars::data_dir(&app)?.display().to_string(),
        log_dir: sidecars::log_dir(&app)?.display().to_string(),
        workbench_url: sup.workbench_url(),
        updater: crate::updater::ENABLED,
    })
}

#[tauri::command]
pub fn open_data_dir(app: AppHandle) -> Result<(), String> {
    let dir = sidecars::data_dir(&app)?;
    app.opener()
        .open_path(dir.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn open_logs(app: AppHandle) -> Result<(), String> {
    let dir = sidecars::log_dir(&app)?;
    app.opener()
        .open_path(dir.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}
