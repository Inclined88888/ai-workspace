//! Approval prompts: the one surface where the app asks "may I do this?".
//!
//! Workbench already holds risky tool calls for approval in its run log (held → approved / denied). This adds a
//! native prompt any part of the desktop app can raise: the Workbench page through `invoke("request_approval")`,
//! and later phases' sidecars (terminal agent, browser agent, computer use) through the same Rust function.
//! Every prompt is also emitted as an `approval-requested` event so an in-app approval centre can mirror it.

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

#[derive(Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    /// Short question, e.g. "Send email to sam@example.com?"
    pub title: String,
    /// What exactly will happen, shown in full.
    pub detail: String,
    /// "low" | "medium" | "high"; high risk shows a warning icon.
    #[serde(default)]
    pub risk: Option<String>,
    /// Which part of the app asks: "workbench", "terminal-agent", "browser-agent", "computer-use".
    #[serde(default)]
    pub source: Option<String>,
}

/// Ask the user, natively, and wait for the answer. Returns true only on an explicit "Approve".
pub async fn ask(app: &AppHandle, req: ApprovalRequest) -> bool {
    let _ = app.emit("approval-requested", &req);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    let kind = match req.risk.as_deref() {
        Some("high") => MessageDialogKind::Warning,
        _ => MessageDialogKind::Info,
    };
    let source = req.source.clone().unwrap_or_else(|| "workbench".into());
    let dialog = app
        .dialog()
        .message(format!("{}\n\nRequested by: {source}", req.detail))
        .title(req.title.clone())
        .kind(kind)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Approve".into(),
            "Deny".into(),
        ));
    let approved = tauri::async_runtime::spawn_blocking(move || dialog.blocking_show())
        .await
        .unwrap_or(false);
    let _ = app.emit("approval-answered", (&req.title, approved));
    approved
}

#[tauri::command]
pub async fn request_approval(app: AppHandle, request: ApprovalRequest) -> Result<bool, String> {
    Ok(ask(&app, request).await)
}
