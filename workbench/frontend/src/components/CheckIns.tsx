import { BellRing, History, Loader2, Play, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";

type Topic = { id: number; name: string; description: string; project_id: number | null; sources: string[]; cron: string; importance: string;
  notify: string; quiet_start: string; quiet_end: string; expires_at: string | null; enabled: number; next_check_at: string | null;
  last_check_at: string | null; last_status: string; last_error: string; failures: number; last_briefing: string };
type Preset = { id: string; name: string; cron: string; sources: string[]; description: string; importance: string; needs_project?: boolean; needs_url?: boolean; needs_query?: boolean };
type Limits = { per_topic_day: number; per_day: number; quiet_start: string; quiet_end: string };
type Run = { id: number; slot: string; attempt: number; status: string; new_facts: number; briefing: string; written_by: string; error: string; started_at: string };
type Delivery = { id: number; channel: string; title: string; status: string; error: string; created_at: string; sent_at: string | null };

const NOTIFY: Record<string, string> = { inbox: "Inbox", telegram: "Inbox + Telegram", call: "Ask to talk in the app", phone: "Phone call (optional line)" };
const STATUS: Record<string, string> = {
  nothing_new: "Nothing new", not_significant: "Nothing significant", delivered: "Briefing sent", deferred: "Held for quiet hours", limited: "Held by the daily limit",
  failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled", skipped: "Skipped", running: "Checking",
};

export function describeCron(cron: string): string {
  const [m, h, dom, mon, dow] = cron.split(" ");
  const time = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : "";
  if (time && dom === "*" && mon === "*") {
    if (dow === "*") return `Every day at ${time}`;
    if (dow === "1-5") return `Weekdays at ${time}`;
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    if (/^\d$/.test(dow)) return `Every ${days[Number(dow) % 7]} at ${time}`;
  }
  if (/^\d+$/.test(m) && /^\*\/\d+$/.test(h) && dom === "*" && mon === "*" && dow === "*") return `Every ${h.slice(2)} hours`;
  return cron;
}

/** Check-ins: ROOKS looks at what you chose on a schedule and tells you only what is new and matters. */
export default function CheckIns() {
  const [topics, setTopics] = useState<Topic[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [preset, setPreset] = useState<Preset | null>(null);
  const [form, setForm] = useState({ name: "", cron: "", extra: "", project_id: "", importance: "normal", notify: "inbox", quiet_start: "", quiet_end: "", expires: "" });
  const [busy, setBusy] = useState(0);
  const [hist, setHist] = useState<{ id: number; runs: Run[]; deliveries: Delivery[] } | null>(null);
  const toast = useToast();

  const load = () => api<{ topics: Topic[]; presets: Preset[]; limits: Limits }>("/checkins")
    .then((r) => { setTopics(r.topics); setPresets(r.presets); setLimits(r.limits); }).catch(toast.err);
  useEffect(() => {
    load();
    api<{ id: number; name: string }[]>("/projects").then(setProjects).catch(() => setProjects([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (p: Preset) => {
    setPreset(p);
    setForm({ ...form, name: p.name, cron: p.cron, importance: p.importance, extra: "" });
  };
  const sources = (p: Preset) => p.sources.map((s) => (s === "page:" || s === "search:" ? s + form.extra.trim() : s));
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!preset) return;
    try {
      await api("/checkins", { body: {
        name: form.name, cron: form.cron, sources: sources(preset), description: preset.description, importance: form.importance, notify: form.notify,
        project_id: form.project_id ? Number(form.project_id) : null, quiet_start: form.quiet_start, quiet_end: form.quiet_end,
        expires_at: form.expires ? new Date(`${form.expires}T23:59:00`).toISOString() : null,
      } });
      toast.ok("Check-in added");
      setPreset(null);
      load();
    } catch (err) { toast.err(err); }
  };
  const check = async (t: Topic) => {
    setBusy(t.id);
    try {
      const r = await api<{ status: string }>(`/checkins/${t.id}/run`, { method: "POST" });
      toast.ok(STATUS[r.status] ?? r.status);
    } catch (e) { toast.err(e); } finally { setBusy(0); load(); }
  };
  const toggle = async (t: Topic) => {
    try { await api(`/checkins/${t.id}`, { method: "PATCH", body: { enabled: !t.enabled } }); load(); } catch (e) { toast.err(e); }
  };
  const remove = async (t: Topic) => {
    if (!confirm(`Delete the check-in "${t.name}"? Its history goes too.`)) return;
    try { await api(`/checkins/${t.id}`, { method: "DELETE" }); load(); } catch (e) { toast.err(e); }
  };
  const history = async (t: Topic) => {
    if (hist?.id === t.id) { setHist(null); return; }
    try { setHist({ id: t.id, ...(await api<{ runs: Run[]; deliveries: Delivery[] }>(`/checkins/${t.id}/runs`)) }); } catch (e) { toast.err(e); }
  };
  const saveLimits = async () => {
    if (!limits) return;
    try { setLimits(await api<Limits>("/checkins/limits", { method: "PUT", body: limits })); toast.ok("Limits saved"); } catch (e) { toast.err(e); }
  };

  return (
    <section className="col" style={{ marginBottom: 20 }} aria-labelledby="checkins-h">
      <div className="row"><BellRing size={16} aria-hidden="true" /><h2 id="checkins-h" className="grow">Check-ins</h2></div>
      <p className="muted small" style={{ margin: 0 }}>ROOKS looks at what you choose on a schedule and tells you only what is new and matters. When nothing changed it
        stays quiet and makes no model call. Each briefing is sent once, even after a restart.</p>
      <div className="card col">
        <div className="row wrap"><span className="small muted">Add:</span>
          {presets.map((p) => <button type="button" key={p.id} className={`btn sm ${preset?.id === p.id ? "primary" : ""}`} onClick={() => pick(p)}>{p.name}</button>)}</div>
        {preset && (
          <form className="col" onSubmit={add}>
            <div className="small muted">{preset.description}</div>
            <div className="grid two">
              <label className="field">Name<input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
              <label className="field">When <span className="hint">{describeCron(form.cron)}</span>
                <input className="input mono" required value={form.cron} onChange={(e) => setForm({ ...form, cron: e.target.value })} title="minute hour day month weekday" /></label>
              {preset.needs_url && <label className="field">Page address<input className="input" type="url" required placeholder="https://…" value={form.extra} onChange={(e) => setForm({ ...form, extra: e.target.value })} /></label>}
              {preset.needs_query && <label className="field">Topic to search for<input className="input" required value={form.extra} onChange={(e) => setForm({ ...form, extra: e.target.value })} /></label>}
              <label className="field">Project {preset.needs_project ? "" : <span className="hint">optional</span>}
                <select className="input" required={!!preset.needs_project} value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>
                  <option value="">None</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
              <label className="field">Importance<select className="input" value={form.importance} onChange={(e) => setForm({ ...form, importance: e.target.value })}>
                <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent (may break quiet hours)</option></select></label>
              <label className="field">Tell me by<select className="input" value={form.notify} onChange={(e) => setForm({ ...form, notify: e.target.value })}>
                {Object.entries(NOTIFY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
              <label className="field">Stop after <span className="hint">optional</span><input className="input" type="date" value={form.expires} onChange={(e) => setForm({ ...form, expires: e.target.value })} /></label>
              <label className="field">Quiet from <span className="hint">empty: the workspace default</span><input className="input" type="time" value={form.quiet_start} onChange={(e) => setForm({ ...form, quiet_start: e.target.value })} /></label>
              <label className="field">Quiet until<input className="input" type="time" value={form.quiet_end} onChange={(e) => setForm({ ...form, quiet_end: e.target.value })} /></label>
            </div>
            {(form.notify === "call" || form.notify === "phone") && <div className="small muted">Calls follow the contact hours and limits in Settings, Voice and calls. A missed or declined call becomes one written briefing.</div>}
            <div className="row"><button className="btn primary"><Plus size={14} /> Add check-in</button><button type="button" className="btn" onClick={() => setPreset(null)}>Cancel</button></div>
          </form>
        )}
      </div>
      {topics.length === 0 ? <div className="card"><Empty icon={<BellRing size={22} />} title="No check-ins yet" hint="Start with a morning briefing: due tasks, today's events, and anything waiting for you." /></div> : (
        <div className="card flush">
          {topics.map((t) => (
            <div key={t.id} className="col" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", gap: 4 }}>
              <div className="row wrap">
                <input type="checkbox" aria-label={`${t.enabled ? "Pause" : "Turn on"} ${t.name}`} checked={!!t.enabled} onChange={() => toggle(t)} />
                <strong className="grow truncate">{t.name}</strong>
                {t.importance !== "normal" && <span className={`badge ${t.importance === "urgent" || t.importance === "high" ? "warn" : ""}`}>{t.importance}</span>}
                <button className="btn sm" disabled={busy === t.id} onClick={() => check(t)}>{busy === t.id ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Check now</button>
                <button className="btn ghost sm icon" aria-label={`History of ${t.name}`} title="History" aria-expanded={hist?.id === t.id} onClick={() => history(t)}><History size={13} /></button>
                <button className="btn ghost sm icon" aria-label={`Delete check-in ${t.name}`} title="Delete check-in" onClick={() => remove(t)}><Trash2 size={13} /></button>
              </div>
              <div className="muted small">{describeCron(t.cron)} · {NOTIFY[t.notify] ?? t.notify} · {t.sources.join(", ")}
                {t.last_check_at ? ` · last checked ${relTime(t.last_check_at)}${t.last_status ? ` (${STATUS[t.last_status] ?? t.last_status})` : ""}` : ""}
                {t.enabled && t.next_check_at ? ` · next ${new Date(t.next_check_at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}
                {t.expires_at ? ` · stops ${new Date(t.expires_at).toLocaleDateString()}` : ""}</div>
              {t.last_error && <div className="small" style={{ color: "var(--danger)" }}>{t.last_error}{t.failures > 1 ? ` (${t.failures} failures in a row)` : ""}</div>}
              {hist?.id === t.id && (
                <div className="col small" style={{ gap: 2 }}>
                  {hist.runs.length === 0 && <span className="faint">Not checked yet.</span>}
                  {hist.runs.slice(0, 15).map((r) => (
                    <div key={r.id}>{new Date(r.started_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} · {STATUS[r.status] ?? r.status}
                      {r.attempt > 1 ? ` · attempt ${r.attempt}` : ""}{r.new_facts ? ` · ${r.new_facts} new` : ""}{r.written_by === "rules" ? " · written without a model" : ""}
                      {r.error ? <span style={{ color: "var(--danger)" }}> · {r.error}</span> : ""}</div>
                  ))}
                  {hist.deliveries.slice(0, 10).map((d) => <div key={`d${d.id}`} className="faint">Sent by {d.channel}: {d.title} · {d.status}{d.error ? ` · ${d.error}` : ""}</div>)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {limits && (
        <details className="card">
          <summary className="small" style={{ cursor: "pointer" }}>Limits and quiet hours for all check-ins</summary>
          <div className="grid two" style={{ marginTop: 10 }}>
            <label className="field">Briefings per check-in per day<input className="input" type="number" min={0} max={100} value={limits.per_topic_day} onChange={(e) => setLimits({ ...limits, per_topic_day: Number(e.target.value) })} /></label>
            <label className="field">Briefings per day in total<input className="input" type="number" min={0} max={100} value={limits.per_day} onChange={(e) => setLimits({ ...limits, per_day: Number(e.target.value) })} /></label>
            <label className="field">Quiet from<input className="input" type="time" value={limits.quiet_start} onChange={(e) => setLimits({ ...limits, quiet_start: e.target.value })} /></label>
            <label className="field">Quiet until<input className="input" type="time" value={limits.quiet_end} onChange={(e) => setLimits({ ...limits, quiet_end: e.target.value })} /></label>
          </div>
          <div style={{ marginTop: 10 }}><button type="button" className="btn" onClick={saveLimits}>Save limits</button></div>
        </details>
      )}
      {toast.node}
    </section>
  );
}
