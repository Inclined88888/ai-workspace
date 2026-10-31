import * as vscode from "vscode";
import { ApiError, buildMessage, chat, describe, EXPLAIN } from "./client";

const TOKEN_KEY = "aiWorkspace.token";
const CONV_KEY = "aiWorkspace.conversationId";

export function activate(context: vscode.ExtensionContext): void {
  const out = vscode.window.createOutputChannel("AI Workspace");
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "aiWorkspace.ask";
  status.text = "$(comment-discussion) AI Workspace";
  status.tooltip = "Ask your AI Workspace";
  status.show();
  let running: AbortController | undefined;

  const cfg = () => vscode.workspace.getConfiguration("aiWorkspace");
  const url = () => cfg().get<string>("url", "http://127.0.0.1:3100");

  async function token(): Promise<string | undefined> {
    const t = await context.secrets.get(TOKEN_KEY);
    if (t) return t;
    const pick = await vscode.window.showWarningMessage(
      "AI Workspace needs a terminal access token (create one in the app: Assistant, Terminal access).", "Set token", "Open AI Workspace");
    if (pick === "Set token") await vscode.commands.executeCommand("aiWorkspace.setToken");
    if (pick === "Open AI Workspace") await vscode.commands.executeCommand("aiWorkspace.openApp");
    return context.secrets.get(TOKEN_KEY);
  }

  async function ask(question: string, withSelection: boolean): Promise<void> {
    const tok = await token();
    if (!tok) return;
    const ed = vscode.window.activeTextEditor;
    const sel = withSelection && ed && !ed.selection.isEmpty ? ed.document.getText(ed.selection) : undefined;
    const message = buildMessage(question, {
      selection: sel, fileName: ed ? vscode.workspace.asRelativePath(ed.document.uri) : undefined, languageId: ed?.document.languageId,
      maxChars: cfg().get<number>("maxSelectionChars", 20000),
    });
    running?.abort();
    running = new AbortController();
    const signal = running.signal;
    out.show(true);
    out.appendLine(`\n> ${question}${sel ? "  [with selection]" : ""}`);
    status.text = "$(sync~spin) AI Workspace";
    try {
      const r = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Window, title: "AI Workspace is answering" },
        () => chat({
          url: url(), token: tok, message, signal, model: cfg().get<string>("model") || undefined,
          conversationId: context.workspaceState.get<number>(CONV_KEY),
          onEvent: (e) => { const line = describe(e, url()); if (line) out.appendLine(line); },
        }));
      if (r.conversationId) await context.workspaceState.update(CONV_KEY, r.conversationId);
      out.appendLine("");
      out.appendLine(r.answer || "(no answer)");
    } catch (e) {
      if (signal.aborted) { out.appendLine("(stopped)"); return; }
      if (e instanceof ApiError && e.status === 404) {
        await context.workspaceState.update(CONV_KEY, undefined);
        out.appendLine("That conversation was deleted in the app; starting a new one next time.");
        return;
      }
      const msg = e instanceof Error ? e.message : String(e);
      out.appendLine(`Error: ${msg}`);
      const isConn = /fetch failed|ECONNREFUSED|ENOTFOUND/.test(msg);
      vscode.window.showErrorMessage(isConn ? `Can't reach AI Workspace at ${url()}. Is it running? (Setting: aiWorkspace.url)` : msg);
    } finally {
      status.text = "$(comment-discussion) AI Workspace";
    }
  }

  context.subscriptions.push(
    out, status,
    vscode.commands.registerCommand("aiWorkspace.ask", async () => {
      const q = await vscode.window.showInputBox({ prompt: "Ask AI Workspace", placeHolder: "What should I do next on this file?" });
      if (q?.trim()) await ask(q, true);
    }),
    vscode.commands.registerCommand("aiWorkspace.askAboutSelection", async () => {
      const q = await vscode.window.showInputBox({ prompt: "Ask about the selected code" });
      if (q?.trim()) await ask(q, true);
    }),
    vscode.commands.registerCommand("aiWorkspace.explainSelection", () => ask(EXPLAIN, true)),
    vscode.commands.registerCommand("aiWorkspace.newConversation", async () => {
      await context.workspaceState.update(CONV_KEY, undefined);
      vscode.window.showInformationMessage("AI Workspace: the next question starts a new conversation.");
    }),
    vscode.commands.registerCommand("aiWorkspace.setToken", async () => {
      const t = await vscode.window.showInputBox({ prompt: "Terminal access token from AI Workspace (Assistant, Terminal access)", password: true, ignoreFocusOut: true });
      if (t === undefined) return;
      if (t.trim()) await context.secrets.store(TOKEN_KEY, t.trim()); else await context.secrets.delete(TOKEN_KEY);
      vscode.window.showInformationMessage(t.trim() ? "AI Workspace token saved in VS Code's secret storage." : "AI Workspace token removed.");
    }),
    vscode.commands.registerCommand("aiWorkspace.openApp", () => vscode.env.openExternal(vscode.Uri.parse(url()))),
    { dispose: () => running?.abort() },
  );
}

export function deactivate(): void { /* subscriptions clean up */ }
