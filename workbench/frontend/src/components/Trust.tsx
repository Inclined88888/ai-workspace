import { Activity, Check, CircleAlert, CircleDashed, CircleX, KeyRound, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { relTime, useToast } from "../util";

type Group = { id: string; label: string; about: string; mode: "on" | "ask" | "off"; tools: { name: string; risk: string }[] };
type Conn = { id: string; kind: string; name: string; status: "ok" | "warn" | "error" | "off"; detail: string; settings?: string; permission?: string };
type Entry = { id: number; at: string; actor: string; action: string; target: string; detail: string; ok: number };

const MODE_LABEL: Record<string, string> = { on: "Allowed", ask: "Ask me first", off: "Off" };

/** Per group: let the assistant act, make it wait for approval, or hide the tools entirely. Enforced by the server. */
export function PermissionsPanel() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const toast = useToast();
  useEffect(() => { api<{ groups: Group[] }>("/permissions").then((r) => setGroups(r.groups)).catch(toast.err); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const change = async (g: Group, mode: Group["mode"]) => {
    try { const r = await api<{ groups: Group[] }>("/permissions", { method: "PUT", body: { groups: { [g.id]: mode } } }); setGroups(r.groups); toast.ok(`${g.label}: ${MODE_LABEL[mode]}`); }
    catch (e) { toast.err(e); }
  };

  return (
    <div className="card col gap-lg" id="permissions" style={{ scrollMarginTop: 16 }}>
      <div className="row"><KeyRound size={16} aria-hidden="true" /><h2 className="grow">What the assistant may do</h2></div>
      <p className="muted small" style={{ margin: 0 }}>Applies everywhere: chat, Telegram, schedules and company agents. With "Ask me first", looking things up still works, but anything that saves or changes something waits for your approval in the Assistant. "Off" removes the tools completely.</p>
      {!groups ? <div className="muted small">Loading…</div> : (
        <div className="col" style={{ gap: 10 }}>
          {groups.map((g) => (
            <div key={g.id} className="row wrap" style={{ gap: 12, alignItems: "center" }}>
              <div className="grow" style={{ minWidth: 200 }}>
                <strong>{g.label}</strong>{g.tools.some((t) => t.risk === "consequential") && <span className="badge warn" style={{ marginLeft: 6 }}>Acts on its own</span>}
                <div className="muted small">{g.about}</div>
              </div>
              <div className="segmented" role="radiogroup" aria-label={`${g.label} permission`}>
                {(["on", "ask", "off"] as const).map((m) => <button key={m} type="button" role="radio" aria-checked={g.mode === m} onClick={() => g.mode !== m && change(g, m)}>{MODE_LABEL[m]}</button>)}
              </div>
            </div>
          ))}
        </div>
      )}
      {toast.node}
    </div>
  );
}

const ICON = { ok: <Check size={14} />, warn: <CircleAlert size={14} />, error: <CircleX size={14} />, off: <CircleDashed size={14} /> };
const BADGE = { ok: "ok", warn: "warn", error: "danger", off: "" };
const WORD = { ok: "Working", warn: "Needs setup", error: "Problem", off: "Off" };

/** One list of everything ROOKS is connected to, from stored state (nothing is contacted to draw it). */
export function ConnectionsHealth() {
  const [items, setItems] = useState<Conn[] | null>(null);
  const load = () => api<Conn[]>("/connections").then(setItems).catch(() => setItems([]));
  useEffect(() => { load(); }, []);
  return (
    <div className="card col" id="connections" style={{ scrollMarginTop: 16 }}>
      <div className="row"><Activity size={16} aria-hidden="true" /><h2 className="grow">Connection health</h2>
        <button type="button" className="btn ghost sm" onClick={load} aria-label="Refresh connection health"><RefreshCw size={13} /> Refresh</button></div>
      {!items ? <div className="muted small">Loading…</div> : (
        <ul className="col" style={{ listStyle: "none", margin: 0, padding: 0, gap: 8 }}>
          {items.map((c) => (
            <li key={c.id} className="row wrap" style={{ gap: 10 }}>
              <span className={`badge ${BADGE[c.status]}`} style={{ minWidth: 104 }}>{ICON[c.status]} {WORD[c.status]}</span>
              <span className="grow" style={{ minWidth: 180 }}><strong>{c.name}</strong> <span className="muted small">{c.kind}</span><br /><span className="muted small">{c.detail}</span></span>
              {c.permission && c.permission !== "on" && <a className="badge" href="#permissions">{MODE_LABEL[c.permission]}</a>}
              {c.settings && (c.settings.startsWith("#") ? <a className="btn ghost sm" href={c.settings}>Settings</a> : <Link className="btn ghost sm" to={c.settings}>Open</Link>)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Sign-ins, key and permission changes, approvals and the assistant's actions, newest first. */
export function ActivityLog() {
  const [rows, setRows] = useState<Entry[] | null>(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { api<Entry[]>(`/audit?limit=100${filter ? `&action=${encodeURIComponent(filter)}` : ""}`).then(setRows).catch(() => setRows([])); }, [filter]);
  return (
    <div className="card col" id="activity" style={{ scrollMarginTop: 16 }}>
      <div className="row wrap"><Activity size={16} aria-hidden="true" /><h2 className="grow">Activity log</h2>
        <select className="input" style={{ width: "auto" }} value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Show">
          <option value="">Everything</option><option value="tool">Assistant actions</option><option value="approve">Approvals</option>
          <option value="permission">Permission changes</option><option value="sign in">Sign-ins</option>
        </select></div>
      <p className="muted small" style={{ margin: 0 }}>Keys and passwords are never written here.</p>
      {!rows ? <div className="muted small">Loading…</div> : rows.length === 0 ? <div className="muted small">Nothing recorded yet.</div> : (
        <div tabIndex={0} role="region" aria-label="Activity entries" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="table small" style={{ width: "100%" }}>
            <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Details</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id}>
                <td title={r.at} style={{ whiteSpace: "nowrap" }}>{relTime(r.at)}</td>
                <td>{r.actor}</td>
                <td>{!r.ok && <span className="badge danger" style={{ marginRight: 4 }}>failed</span>}{r.action} <span className="mono">{r.target}</span></td>
                <td className="muted" style={{ wordBreak: "break-word" }}>{r.detail}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type GitHubState = { connected: boolean; ok?: boolean; login?: string; error?: string };

/** Optional GitHub connection: a fine-grained token, tested before it is trusted. */
export function GitHubPanel() {
  const [st, setSt] = useState<GitHubState | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const load = () => api<{ github: GitHubState }>("/assistant/settings").then((r) => setSt(r.github)).catch(() => undefined);
  useEffect(() => { load(); }, []);
  const save = async (value: string) => {
    setBusy(true);
    try {
      await api("/assistant/settings", { method: "PUT", body: { github_token: value } });
      setToken("");
      if (value) { const r = await api<GitHubState>("/github/test", { method: "POST" }); r.ok ? toast.ok(`Connected as @${r.login}`) : toast.err(r.error ?? "GitHub rejected the token"); }
      else toast.ok("GitHub disconnected");
      await load();
    } catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  return (
    <div className="card col" id="github" style={{ scrollMarginTop: 16 }}>
      <div className="row"><h2 className="grow">GitHub</h2>{st?.connected && (st.ok ? <span className="badge ok"><Check size={11} /> @{st.login}</span> : <span className="badge warn">{st.error ? "Problem" : "Not checked"}</span>)}</div>
      <p className="muted small" style={{ margin: 0 }}>Optional. Lets the assistant search and read issues and pull requests. Opening an issue or commenting always waits for your approval. Use a fine-grained token limited to the repositories you want (github.com, Settings, Developer settings).</p>
      {st?.error && <div className="alert err">{st.error}</div>}
      <form className="row wrap" onSubmit={(e) => { e.preventDefault(); save(token.trim()); }}>
        <input className="input grow mono" type="password" autoComplete="off" aria-label="GitHub token" placeholder={st?.connected ? "Paste a new token to replace it" : "github_pat_…"} value={token} onChange={(e) => setToken(e.target.value)} style={{ minWidth: 220 }} />
        <button className="btn" disabled={busy || !token.trim()}>{busy ? "Checking…" : "Save and test"}</button>
        {st?.connected && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => { if (confirm("Disconnect GitHub?")) save(""); }}>Disconnect</button>}
      </form>
      {toast.node}
    </div>
  );
}

type ToolInfo = { name: string; description: string; group_label: string; risk: string; timeout: number; permission: string; always_asks: boolean; available: boolean; in_chat: boolean };
type ToolRun = { id: number; at: string; tool: string; actor: string; ok: number; ms: number; error: string };

/** What the assistant can use, how risky each tool is, and what failed recently. */
export function ToolsPanel() {
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [fails, setFails] = useState<ToolRun[]>([]);
  useEffect(() => {
    api<ToolInfo[]>("/tools").then(setTools).catch(() => setTools([]));
    api<ToolRun[]>("/tool-runs?failed=true&limit=15").then(setFails).catch(() => undefined);
  }, []);
  return (
    <details className="card" id="tools" style={{ scrollMarginTop: 16 }}>
      <summary style={{ cursor: "pointer" }}><strong>Tools</strong> <span className="muted small">{tools ? `${tools.filter((t) => t.in_chat).length} of ${tools.length} usable now` : ""}</span></summary>
      {tools && (
        <div tabIndex={0} role="region" aria-label="Activity log table" style={{ overflowX: "auto", marginTop: 8 }}>
          <table className="table small" style={{ width: "100%" }}>
            <thead><tr><th scope="col">Tool</th><th scope="col">Area</th><th scope="col">Kind</th><th scope="col">Status</th><th scope="col">Time limit</th></tr></thead>
            <tbody>{tools.map((t) => (
              <tr key={t.name}>
                <td><span className="mono">{t.name}</span><br /><span className="muted">{t.description}</span></td>
                <td>{t.group_label}</td>
                <td>{t.risk === "read" ? "reads" : t.risk === "write" ? "changes your data" : "acts outside ROOKS"}</td>
                <td>{!t.available ? "not set up" : t.permission === "off" ? "off" : t.always_asks || t.permission === "ask" && t.risk !== "read" ? "asks first" : "allowed"}</td>
                <td>{t.timeout}s</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <h3 style={{ marginTop: 12 }}>Recent failures</h3>
      {fails.length === 0 ? <div className="muted small">None.</div> : (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{fails.map((f) => <li key={f.id}><span className="mono">{f.tool}</span> ({f.actor}, {relTime(f.at)}): {f.error}</li>)}</ul>
      )}
    </details>
  );
}
