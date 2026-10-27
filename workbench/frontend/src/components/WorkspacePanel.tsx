import { CircleAlert, Folder, FileText, Loader2, ShieldCheck, TerminalSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../util";

type Runner = { configured: boolean; reachable: boolean; mode?: string; workspace?: string; warnings?: string[]; error?: string; max_timeout?: number; internet?: boolean | null };
type Status = { runner: Runner; settings: { allow: string[]; default_timeout: number }; risky: string[]; terminal_mode: string; files_mode: string };
type Entry = { name: string; type: string; size: number | null };
const MODE: Record<string, string> = { on: "runs without asking (risky commands still ask)", ask: "asks before each command", off: "off" };

/** The assistant's terminal and workspace: where it runs, how isolated it is, what runs without asking. */
export function WorkspacePanel() {
  const [st, setSt] = useState<Status | null>(null);
  const [allow, setAllow] = useState("");
  const [timeout, setTimeout_] = useState(120);
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [file, setFile] = useState<{ path: string; content: string; more: boolean } | null>(null);
  const toast = useToast();
  const load = () => api<Status>("/workspace").then((s) => { setSt(s); setAllow(s.settings.allow.join("\n")); setTimeout_(s.settings.default_timeout); }).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const browse = async (p: string) => {
    try { setEntries((await api<{ entries: Entry[] }>(`/workspace/files?path=${encodeURIComponent(p)}`)).entries); setPath(p); setFile(null); }
    catch (e) { toast.err(e); }
  };
  const open = async (p: string) => {
    try { setFile(await api(`/workspace/file?path=${encodeURIComponent(p)}`)); } catch (e) { toast.err(e); }
  };
  const save = async () => {
    try {
      await api("/workspace/settings", { method: "PUT", body: { allow: allow.split("\n").map((l) => l.trim()).filter(Boolean), default_timeout: timeout } });
      toast.ok("Saved"); load();
    } catch (e) { toast.err(e); }
  };
  if (!st) return <div className="card"><Loader2 size={14} className="spin" /> Loading…</div>;
  const r = st.runner;
  const isolated = r.reachable && r.mode === "container" && !(r.warnings ?? []).length;
  return (
    <div className="card col" id="workspace">
      <div className="row"><TerminalSquare size={16} aria-hidden="true" /><h3 className="grow">Workspace and terminal</h3>
        <span className={`badge ${r.reachable ? (isolated ? "ok" : "warn") : ""}`}>{!r.configured ? "Not set up" : !r.reachable ? "Runner not reachable" : isolated ? "Isolated" : "Connected"}</span></div>
      <p className="muted small" style={{ margin: 0 }}>The assistant can run commands and edit files in its workspace. Both happen in a separate runner that has only the workspace folder: no ROOKS data, no API keys.</p>
      {!r.configured && <div className="alert">Run <span className="mono">./setup.sh</span> to add the runner (it creates RUNNER_TOKEN and starts the runner service). Until then the assistant has no terminal.</div>}
      {r.configured && !r.reachable && <div className="alert">The runner did not answer ({r.error}). Check it with <span className="mono">docker compose ps runner</span>.</div>}
      {r.reachable && <div className="small">Workspace: <span className="mono">{r.workspace}</span> · {r.mode === "container" ? "own container" : "same machine (development mode, not isolated)"}
        {" · "}Internet: <strong>{r.internet === false ? "off" : r.internet ? "on" : "not checked"}</strong>
        {r.internet !== false && <span className="muted"> (turn it off with <span className="mono">./setup.sh --runner-offline</span>)</span>}</div>}
      {(r.warnings ?? []).map((w) => <div key={w} className="alert"><CircleAlert size={13} /> {w}</div>)}
      <div className="small">Terminal: <strong>{MODE[st.terminal_mode] ?? st.terminal_mode}</strong> · Files: <strong>{MODE[st.files_mode] ?? st.files_mode}</strong> · <a href="#permissions">change in Permissions</a></div>
      <details className="small"><summary style={{ cursor: "pointer" }}><ShieldCheck size={12} /> Always asks first, whatever you set</summary>
        <p className="muted" style={{ margin: "6px 0" }}>Commands that {st.risky.join("; ")}. After outside content (web, email, files) is read in a conversation, every command and file change asks. Runs without you watching (schedules, Telegram, agents) always ask.</p></details>
      <label className="field">Commands that run without asking when Terminal is set to ask (one per line; a command matches if it starts with one of these and has no ; | &amp; or redirection)
        <textarea className="input mono" rows={5} value={allow} onChange={(e) => setAllow(e.target.value)} /></label>
      <label className="field">Default time limit for a command (seconds)
        <input className="input" type="number" min={5} max={900} style={{ width: 120 }} value={timeout} onChange={(e) => setTimeout_(+e.target.value)} /></label>
      <div><button className="btn primary sm" onClick={save}>Save</button></div>
      {r.reachable && (
        <details className="small" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !entries) browse(""); }}>
          <summary style={{ cursor: "pointer" }}>Browse the workspace (read only)</summary>
          <div className="col" style={{ gap: 4, marginTop: 6 }}>
            <div className="mono">{path || "."}{path && <button className="btn ghost sm" onClick={() => browse(path.split("/").slice(0, -1).join("/"))}>up</button>}</div>
            {entries?.length === 0 && <div className="muted">Empty.</div>}
            {entries?.map((e) => (
              <button key={e.name} type="button" className="btn ghost sm" style={{ justifyContent: "flex-start" }}
                onClick={() => (e.type === "dir" ? browse(path ? `${path}/${e.name}` : e.name) : open(path ? `${path}/${e.name}` : e.name))}>
                {e.type === "dir" ? <Folder size={12} /> : <FileText size={12} />} {e.name}{e.type === "file" && e.size !== null ? <span className="faint"> {e.size.toLocaleString()} bytes</span> : null}
              </button>
            ))}
            {file && <pre className="terminal" aria-label={`Contents of ${file.path}`}>{file.content}{file.more ? "\n…" : ""}</pre>}
          </div>
        </details>
      )}
      {toast.node}
    </div>
  );
}
