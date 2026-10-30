//! In-app updates. Prepared but OFF until signing keys exist.
//!
//! Turning it on (docs/desktop-release.md):
//! 1. `npx tauri signer generate` → keep the private key + password as the TAURI_SIGNING_PRIVATE_KEY and
//!    TAURI_SIGNING_PRIVATE_KEY_PASSWORD repository secrets, and put the public key in `tauri.updater.conf.json`.
//! 2. The release workflow then builds with `--features updater --config src-tauri/tauri.updater.conf.json`, which
//!    signs update bundles (`.sig`). Generating and uploading `latest.json` is the remaining step
//!    (see docs/desktop-release.md).
//!
//! Without the feature this module compiles to no-ops, so no update request is ever made.

use tauri::{AppHandle, Builder, Runtime};

#[cfg(feature = "updater")]
pub const ENABLED: bool = true;
#[cfg(not(feature = "updater"))]
pub const ENABLED: bool = false;

#[cfg(feature = "updater")]
pub fn register<R: Runtime>(b: Builder<R>) -> Builder<R> {
    b.plugin(tauri_plugin_updater::Builder::new().build())
}

#[cfg(not(feature = "updater"))]
pub fn register<R: Runtime>(b: Builder<R>) -> Builder<R> {
    b
}

/// Look for an update once at start-up and ask before installing it.
#[cfg(feature = "updater")]
pub fn check_on_start<R: Runtime>(app: AppHandle<R>) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
    use tauri_plugin_updater::UpdaterExt;

    tauri::async_runtime::spawn(async move {
        let Ok(updater) = app.updater() else { return };
        let Ok(Some(update)) = updater.check().await else {
            return;
        };
        let version = update.version.clone();
        let dialog = app
            .dialog()
            .message(format!(
                "AI Workspace {version} is available. Install it now? The app restarts afterwards."
            ))
            .title("Update available")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Install".into(),
                "Later".into(),
            ));
        let yes = tauri::async_runtime::spawn_blocking(move || dialog.blocking_show())
            .await
            .unwrap_or(false);
        if yes && update.download_and_install(|_, _| {}, || {}).await.is_ok() {
            app.restart();
        }
    });
}

#[cfg(not(feature = "updater"))]
pub fn check_on_start<R: Runtime>(_app: AppHandle<R>) {}
