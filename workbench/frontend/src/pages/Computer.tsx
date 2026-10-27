import { Ban, Check, CircleAlert, Copy, Eye, Hand, History, Laptop, Loader2, MonitorPlay, OctagonX, Play, Plus, ShieldCheck, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ModelSelect, relTime, useModels, useToast } from "../util";

type Device = { id: number; name: string; os: string; online: boolean; last_seen: string; version: string; capabilities: string[];
  permissions: { accessibility?: boolean | null; screen_recording?: boolean | null; notes?: string }; folders: string[]; blocked_apps: string[];
  read_only: boolean; platform_note: string };
type Settings = { enabled: boolean; mode: "read_only" | "ask" | "allowed"; app_rules: Record<string, string>; stopped: boolean; paused: boolean; screenshots: boolean; coordinates: boolean };
type Approval = { id: number; tool: string; args: string; reason: string };
type Vision = { model: string; mode: "off" | "ask" | "session"; local_only: boolean; session_until: string;
  consent: { destination?: string; at?: string }; check: { ok?: boolean; at?: string; error?: string };
  destination: { label?: string; local?: boolean } };
type Status = { vision: Vision; screen_shared: { app?: string; destination?: string }; settings: Settings; devices: Device[]; grants: { id: number; app: string; expires_at: string }[]; approvals: Approval[];
  current: { id: number; kind: string; status: string; summary: string } | null; hard_blocked: string[] };
type Hist = { id: number; kind: string; risk: string; status: string; error: string; summary: string; created_at: string; mission_id: number | null; conversation_id: number | null };

const MODES: [Settings["mode"], string, string][] = [
  ["read_only", "Look only", "ROOKS can see apps and pages but change nothing."],
  ["ask", "Ask before changes", "Every click, keystroke or file change waits for you (or a short app grant)."],
  ["allowed", "Allowed", "ROOKS works on its own. Sending, buying, deleting and similar still ask every time."],
];
const STATUS_TONE: Record<string, string> = { done: "ok", failed: "danger", refused: "danger", cancelled: "", uncertain: "warn", dispatched: "accent", queued: "" };

function Perm({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  if (ok === null || ok === undefined) return <span className="badge">{label}: not needed here</span>;
  return ok ? <span className="badge ok"><Check size={11} /> {label}</span> : <span className="badge warn"><CircleAlert size={11} /> {label} off</span>;
}

/** Visual targeting: off by default; needs a model that passed the image test and, for a cloud model, your consent. */
function VisionCard({ v, onChange }: { v: Vision; onChange: () => void }) {
  const { models } = useModels();
  const [busy, setBusy] = useState(false);
  const [agree, setAgree] = useState(false);
  const toast = useToast();
  const put = async (body: Record<string, unknown>) => {
    try { await api("/computer/vision", { method: "PUT", body }); onChange(); } catch (e) { toast.err(e); }
  };
  const test = async () => {
    setBusy(true);
    try { const r = await api<{ check: { ok: boolean; error: string } }>("/computer/vision/check", { method: "POST" });
      r.check.ok ? toast.ok("It passed the image test") : toast.err(new Error(r.check.error)); onChange(); }
    catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  const cloud = v.model && v.destination.local === false;
  const sessionLeft = v.mode === "session" && v.session_until ? new Date(v.session_until) : null;
  return (
    <div className="card col" role="region" aria-label="Visual targeting">
      <div className="row"><Eye size={16} aria-hidden="true" /><h3 className="grow">Visual targeting</h3>
        <span className={`badge ${v.mode === "off" ? "" : "warn"}`}>{v.mode === "off" ? "Off" : v.mode === "ask" ? "Asks each time" : "Session"}</span></div>
      <p className="muted small" style={{ margin: 0 }}>When an app does not expose a button to accessibility, ROOKS can look at a screenshot of that one window.
        Password fields are blacked out on your computer first. Screenshots are not stored or logged, and the chat model never sees them.</p>
      <label className="small">Vision model
        <ModelSelect value={v.model} onChange={(ref) => put({ model: ref })} models={models} defaultLabel="None chosen" /></label>
      {v.model && <div className="small">Screenshots would go to: <strong>{v.destination.label}</strong></div>}
      {v.model && (v.check.ok ? <div className="badge ok"><Check size={11} /> Passed the image test {v.check.at ? relTime(v.check.at) : ""}</div>
        : <div className="row wrap small"><span className="badge warn">{v.check.error || "Not tested yet"}</span>
          <button className="btn sm" disabled={busy} onClick={test}>{busy ? <Loader2 size={13} className="spin" /> : null} Run the image test</button></div>)}
      <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={v.local_only} onChange={(e) => put({ local_only: e.target.checked })} />
        Local models only (refuse any model outside this computer or your network)</label>
      {v.model && v.check.ok && (
        <>
          {cloud && !v.consent.destination && (
            <label className="row small alert" style={{ gap: 6 }}><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              I agree that screenshots of the app ROOKS is working in are sent to {v.destination.label}.</label>)}
          {v.consent.destination && <div className="small muted">You agreed to send screenshots to {v.consent.destination}{v.consent.at ? ` (${relTime(v.consent.at)})` : ""}.</div>}
          <div className="row wrap">
            <button className="btn sm" aria-pressed={v.mode === "off"} onClick={() => put({ mode: "off" })}>Off</button>
            <button className="btn sm" aria-pressed={v.mode === "ask"} disabled={!!cloud && !v.consent.destination && !agree}
              onClick={() => put({ mode: "ask", consent: agree })}>Ask before each screenshot</button>
            <button className="btn sm" aria-pressed={v.mode === "session"} disabled={!!cloud && !v.consent.destination && !agree}
              onClick={() => put({ mode: "session", consent: agree, session_minutes: 60 })}>Allow for 1 hour</button>
          </div>
          {sessionLeft && <div className="small">Screenshots allowed without asking until {sessionLeft.toLocaleTimeString()}.</div>}
        </>
      )}
      {toast.node}
    </div>
  );
}

type Change = { id: string; at: string; kind: string; path?: string; src?: string; dst?: string; summary: string; undone?: boolean };

/** Changes ROOKS made to files on your computer, with the versions it kept. Undo restores one; it refuses if the file changed since. */
function FileHistory() {
  const [items, setItems] = useState<Change[] | null>(null);
  const [error, setError] = useState("");
  const toast = useToast();
  const load = async () => {
    try { setItems((await api<{ entries: Change[] }>("/computer/files/history")).entries.slice().reverse()); setError(""); }
    catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { load(); }, []);
  const undo = async (c: Change) => {
    if (!confirm(`Undo "${c.summary}"? The version from before this change comes back; the current one is kept in the versions folder.`)) return;
    try { await api("/computer/files/undo", { body: { change: c.id } }); toast.ok("Undone"); load(); } catch (e) { toast.err(e); }
  };
  return (
    <div className="card col" role="region" aria-label="File changes">
      <div className="row"><History size={16} aria-hidden="true" /><h3 className="grow">File changes</h3><button className="btn ghost sm" onClick={load}>Refresh</button></div>
      <p className="muted small" style={{ margin: 0 }}>Only changes ROOKS made through its file tools, in folders you allowed. Changes made by clicking in other apps are not covered by Undo.</p>
      {error ? <div className="muted small">{error}</div> : !items ? <Loader2 size={14} className="spin" /> : items.length === 0 ? <div className="muted small">No file changes yet.</div> : (
        <ul className="col small" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
          {items.slice(0, 30).map((c) => (
            <li key={c.id} className="row wrap" style={{ gap: 6 }}>
              <span className="badge">{c.kind}</span><span className="grow truncate" title={c.path || c.dst}>{c.summary}</span>
              <span className="faint">{c.at}</span>
              {c.undone ? <span className="badge">undone</span> : <button className="btn ghost sm" onClick={() => undo(c)}><Undo2 size={12} /> Undo</button>}
            </li>
          ))}
        </ul>
      )}
      {toast.node}
    </div>
  );
}

function Pairing({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState<{ code: string; minutes: number } | null>(null);
  const toast = useToast();
  const url = window.location.origin;
  const cmd = code ? `cd ~/ai-workspace/companion && python3 -m rooks_companion pair ${url} ${code.code} && python3 -m rooks_companion run` : "";
  return (
    <div className="card col">
      <h3>Pair a computer</h3>
      <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
        <li>On the computer ROOKS should use (a Mac for full desktop control), open Terminal.</li>
        <li>Press <strong>Create code</strong>, then copy and run the command. The code works once, for {code?.minutes ?? 10} minutes.</li>
        <li>On a Mac, allow <strong>Accessibility</strong> (and <strong>Screen Recording</strong> for previews) when macOS asks, or run <span className="mono">python3 -m rooks_companion permissions</span>.</li>
      </ol>
      {!code ? <div><button className="btn primary" onClick={async () => { try { setCode(await api("/computer/pair", { method: "POST" })); } catch (e) { toast.err(e); } }}><Plus size={14} /> Create code</button></div> : (
        <div className="col" style={{ gap: 6 }}>
          <div className="row"><code className="mono grow" style={{ padding: "8px 10px", background: "var(--panel-2)", borderRadius: 7, overflowX: "auto", whiteSpace: "nowrap" }}>{cmd}</code>
            <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(cmd); toast.ok("Copied"); }} aria-label="Copy command"><Copy size={13} /> Copy</button></div>
          <div className="muted small">The companion connects out to ROOKS and opens no port on your computer. Waiting for it to connect… <button className="btn ghost sm" onClick={onDone}>Check now</button></div>
        </div>
      )}
      {toast.node}
    </div>
  );
}

export default function Computer() {
  const [st, setSt] = useState<Status | null>(null);
  const [hist, setHist] = useState<Hist[]>([]);
  const [shot, setShot] = useState("");
  const [rule, setRule] = useState({ app: "", access: "blocked" });
  const toast = useToast();
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const load = async () => {
    try {
      const s = await api<Status>("/computer");
      setSt(s);
      setHist(await api<Hist[]>("/computer/history?limit=40"));
      const dev = s.devices.find((d) => d.online);
      if (dev && s.settings.screenshots) setShot((await api<{ image: string }>(`/computer/screenshot/${dev.id}`)).image);
    } catch (e) { toast.err(e); }
  };
  useEffect(() => {
    let stop = false;
    const tick = async () => { await load(); if (!stop) timer.current = setTimeout(tick, 1500); };
    tick();
    return () => { stop = true; clearTimeout(timer.current); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!st) return <div className="page"><Loader2 size={16} className="spin" /> Loading…</div>;
  const s = st.settings;
  const dev = st.devices.find((d) => d.online);
  const put = async (body: Partial<Settings>) => { try { await api("/computer/settings", { method: "PUT", body }); load(); } catch (e) { toast.err(e); } };
  const post = async (path: string, msg: string) => { try { await api(`/computer/${path}`, { method: "POST" }); toast.ok(msg); load(); } catch (e) { toast.err(e); } };
  const decide = async (a: Approval, approve: boolean, grantApp?: string) => {
    try {
      if (grantApp) await api("/computer/grants", { body: { app: grantApp, minutes: 30 } });
      await api(`/approvals/${a.id}`, { body: { approve } });
      toast.ok(approve ? "Approved" : "Denied"); load();
    } catch (e) { toast.err(e); }
  };
  const working = !!st.current;
  const state = s.stopped ? ["danger", "Stopped by you"] : s.paused ? ["warn", "You have control"] : !s.enabled ? ["", "Off"] : working ? ["accent", "Working"] : dev ? ["ok", "Ready"] : ["warn", "No computer connected"];

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <div className="page-head"><div><h1>Computer</h1><p className="muted">Let ROOKS use apps, its own browser and folders you choose on your computer. You see every action and can stop it at once.</p></div></div>

      <div className="card col" role="region" aria-label="Computer controls" style={{ marginBottom: 16, borderColor: s.stopped ? "var(--danger)" : undefined }}>
        <div className="row wrap">
          <MonitorPlay size={18} aria-hidden="true" />
          <h2 className="grow">{state[1]}</h2>
          <span className={`badge ${state[0]}`} role="status">{working && <Loader2 size={11} className="spin" />} {working ? st.current!.kind : s.enabled ? MODES.find((m) => m[0] === s.mode)?.[1] : "Off"}</span>
        </div>
        {working && <div className="small">Now: <strong>{st.current!.kind}</strong> {st.current!.summary}</div>}
        <div className="row wrap">
          {!s.stopped && <button className="btn danger" onClick={() => post("stop", "Stopped. Nothing more will run until you allow it again.")}><OctagonX size={15} /> Stop now</button>}
          {s.stopped && <button className="btn primary" onClick={() => post("allow", "Allowed again")}><Play size={14} /> Allow again</button>}
          {!s.stopped && !s.paused && <button className="btn" onClick={() => post("take-control", "You have control. ROOKS waits.")}><Hand size={14} /> Take control</button>}
          {s.paused && <button className="btn primary" onClick={() => post("hand-back", "Handed back to ROOKS")}><Play size={14} /> Hand back to ROOKS</button>}
          <label className="row small" style={{ gap: 6, marginLeft: "auto" }}><input type="checkbox" checked={s.enabled} onChange={(e) => put({ enabled: e.target.checked })} /> Computer Use on</label>
        </div>
        <div className="muted small">Stop works even if this page is closed: on the computer run <span className="mono">python3 -m rooks_companion stop</span>, or quit the companion with Ctrl+C.</div>
      </div>

      {st.approvals.length > 0 && (
        <div className="card col" style={{ borderColor: "var(--warn)", marginBottom: 16 }} role="region" aria-label="Approvals needed">
          <h3>Waiting for your approval</h3>
          {st.approvals.map((a) => {
            let app = ""; try { app = JSON.parse(a.args).app ?? ""; } catch { /* not JSON */ }
            return (
              <div key={a.id} className="col" style={{ gap: 6 }}>
                <div><strong className="mono">{a.tool}</strong> <span className="muted small">{a.reason}</span></div>
                <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0, background: "var(--panel-2)", padding: 8, borderRadius: 7 }}>{a.args}</pre>
                <div className="row wrap"><button className="btn primary sm" onClick={() => decide(a, true)}>Approve</button>
                  {app && !/send|buy|delete|submit/i.test(a.reason) && <button className="btn sm" onClick={() => decide(a, true, app)}>Approve and allow {app} for 30 min</button>}
                  <button className="btn sm" onClick={() => decide(a, false)}>Deny</button></div>
              </div>
            );
          })}
        </div>
      )}

      <div className="two-pane">
        <div className="col">
          {st.devices.length === 0 ? <Pairing onDone={load} /> : st.devices.map((d) => (
            <div key={d.id} className="card col">
              <div className="row"><Laptop size={16} aria-hidden="true" /><strong className="grow">{d.name}</strong>
                <span className={`badge ${d.online ? "ok" : ""}`}>{d.online ? "Connected" : `Last seen ${relTime(d.last_seen)}`}</span></div>
              <div className="small muted">{d.os} · companion {d.version || "?"}</div>
              <div className="row wrap" style={{ gap: 4 }}><Perm ok={d.permissions.accessibility} label="Accessibility" /><Perm ok={d.permissions.screen_recording} label="Screen Recording" /></div>
              {d.permissions.notes && <div className="alert">{d.permissions.notes}</div>}
              {d.platform_note && <div className="small muted">{d.platform_note}</div>}
              <div className="small">Can use: {d.capabilities.length ? d.capabilities.map((c) => ({ desktop: "apps", browser: "ROOKS's browser", files: "allowed folders" }[c] ?? c)).join(", ") : "nothing yet"}</div>
              <div className="small">Folders: {d.folders.length ? d.folders.map((f) => <span key={f} className="tag mono">{f}</span>) : <span className="muted">none (add with allow-folder on the computer)</span>}</div>
              {d.read_only && <div className="badge">Read only on this computer</div>}
              <div><button className="btn ghost sm danger" onClick={async () => { if (confirm(`Remove ${d.name}? It will need pairing again.`)) { await api(`/computer/devices/${d.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={13} /> Remove</button></div>
            </div>
          ))}
          {st.devices.length > 0 && !dev && <Pairing onDone={load} />}

          <div className="card col">
            <h3>What ROOKS may do</h3>
            <div className="col" role="radiogroup" aria-label="Computer Use mode" style={{ gap: 6 }}>
              {MODES.map(([m, label, about]) => (
                <button key={m} type="button" role="radio" aria-checked={s.mode === m} className="choice compact" onClick={() => put({ mode: m })}>
                  <span className="grow"><strong>{label}</strong><br /><span className="muted small">{about}</span></span>
                </button>
              ))}
            </div>
            <div className="small"><strong>Per app</strong></div>
            {Object.entries(s.app_rules).map(([app, r]) => (
              <div key={app} className="row small"><span className="grow">{app}</span><span className="badge">{({ blocked: "never", read: "look only", write: "may change" } as Record<string, string>)[r]}</span>
                <button className="btn ghost sm icon" aria-label={`Remove rule for ${app}`} onClick={() => { const n = { ...s.app_rules }; delete n[app]; put({ app_rules: n }); }}><X size={12} /></button></div>
            ))}
            <form className="row wrap" onSubmit={(e) => { e.preventDefault(); if (rule.app.trim()) { put({ app_rules: { ...s.app_rules, [rule.app.trim()]: rule.access } }); setRule({ app: "", access: "blocked" }); } }}>
              <input className="input grow" style={{ minWidth: 120 }} placeholder="App name, e.g. Mail" value={rule.app} onChange={(e) => setRule({ ...rule, app: e.target.value })} aria-label="App name" />
              <select className="input" style={{ width: "auto" }} value={rule.access} onChange={(e) => setRule({ ...rule, access: e.target.value })} aria-label="Access">
                <option value="blocked">Never</option><option value="read">Look only</option><option value="write">May change</option>
              </select>
              <button className="btn sm">Add</button>
            </form>
            {st.grants.length > 0 && <div className="small">Temporary access: {st.grants.map((g) => <span key={g.id} className="tag">{g.app} until {new Date(g.expires_at).toLocaleTimeString()} <button className="btn ghost sm icon" aria-label={`End access for ${g.app}`} onClick={async () => { await api(`/computer/grants/${g.id}`, { method: "DELETE" }); load(); }}><X size={10} /></button></span>)}</div>}
            <details className="small"><summary style={{ cursor: "pointer" }}><ShieldCheck size={12} /> Always off limits</summary>
              <p className="muted" style={{ margin: "6px 0" }}>Shells, password managers and system security settings can never be controlled, whatever you set: {st.hard_blocked.join(", ")}. Password fields are never typed into. Screen text is treated as information, never as instructions.</p></details>
            <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.screenshots} onChange={(e) => put({ screenshots: e.target.checked })} /> Show me screenshots of what ROOKS sees (kept in memory for 10 minutes, never sent to the AI model)</label>
          </div>
        </div>

        <div className="col">
          <div className="card col">
            <h3>Live view</h3>
            {shot ? <img src={shot} alt="Latest screenshot of the computer ROOKS is using" style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
              : <div className="muted small">{s.screenshots ? "No screenshot yet. One appears when ROOKS looks at the screen (needs Screen Recording on a Mac)." : "Screenshots are off."}</div>}
          </div>
          {st.screen_shared?.app && <div className="alert" role="status"><Eye size={13} /> Screen shared just now: a screenshot of {st.screen_shared.app} went to {st.screen_shared.destination}.</div>}
          <VisionCard v={st.vision} onChange={load} />
          {dev?.capabilities.includes("files") && <FileHistory />}
          <div className="card col">
            <div className="row"><h3 className="grow">Actions</h3><Link className="small" to="/settings#activity">Full activity log</Link></div>
            {hist.length === 0 ? <Empty icon={<MonitorPlay size={22} />} title="Nothing yet" hint={'Ask in chat, e.g. "Open my invoices spreadsheet and sort it by date", or start a Mission.'} /> : (
              <ul className="col small" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }} aria-live="polite">
                {hist.map((h) => (
                  <li key={h.id} className="row wrap" style={{ gap: 6 }}>
                    <span className={`badge ${STATUS_TONE[h.status] ?? ""}`}>{h.status === "uncertain" ? "unconfirmed" : h.status}</span>
                    <strong className="mono">{h.kind}</strong><span className="muted grow truncate">{h.summary}</span>
                    {h.mission_id && <Link to={`/missions/${h.mission_id}`}>mission</Link>}
                    <span className="faint">{relTime(h.created_at)}</span>
                    {h.error && <div className="danger-text" style={{ width: "100%" }}>{h.error}</div>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {!s.enabled && <div className="alert"><Ban size={13} /> Computer Use is off. Nothing on your computer can be touched until you turn it on.</div>}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
