import { BellRing, Clock, ExternalLink, Inbox, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";

type Action = { label: string; method: string; path: string; body: unknown; effect: string; tone: string; confirm: string };
type Item = { id: string; type: string; title: string; what: string; why: string; actions: Action[]; risk: string; cost: string; after: string;
  link: string; at: string; priority: number; deferred_until: string | null };
type Data = { items: Item[]; deferred: number; counts: Record<string, number> };

const TYPE: Record<string, string> = {
  approval: "Approvals", spending: "Spending", call: "Calls", worker: "Workers", mission: "Missions", automation: "Automations",
  research: "Research", reminder: "Reminders", follow_up: "Follow-ups",
};
const TONE: Record<string, string> = { approval: "warn", spending: "warn", call: "accent", worker: "accent", mission: "accent", automation: "danger" };

/** Everything waiting for you, one decision at a time. */
export default function NeedsYou() {
  const [data, setData] = useState<Data | null>(null);
  const [filter, setFilter] = useState("");
  const [showDeferred, setShowDeferred] = useState(false);
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const load = () => api<Data>(`/needs-you${showDeferred ? "?include_deferred=true" : ""}`).then((d) => { setData(d); window.dispatchEvent(new Event("wb:attention")); }).catch(toast.err);
  useEffect(() => { load(); const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 15000); return () => window.clearInterval(t); }, [showDeferred]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (i: Item, a: Action) => {
    if (a.confirm && !confirm(a.confirm)) return;
    setBusy(`${i.id}:${a.label}`);
    try { await api(a.path, { method: a.method, body: a.body ?? undefined }); toast.ok(`${a.label}: done`); await load(); }
    catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const later = async (i: Item, hours: number) => {
    try { await api("/needs-you/defer", { body: { id: i.id, hours } }); toast.ok("Hidden for now. Nothing was decided."); load(); } catch (e) { toast.err(e); }
  };

  const items = (data?.items ?? []).filter((i) => !filter || i.type === filter);
  return (
    <div className="page" style={{ maxWidth: 960 }}>
      <div className="page-head"><div><h1>Needs you</h1>
        <p className="muted">Everything waiting for your decision, most urgent first. Each choice applies to that one item only.</p></div></div>
      {data && data.items.length > 0 && (
        <div className="row wrap" style={{ marginBottom: 12 }} role="toolbar" aria-label="Filter">
          <button type="button" className={`btn sm ${!filter ? "primary" : ""}`} aria-pressed={!filter} onClick={() => setFilter("")}>All ({data.items.length})</button>
          {Object.entries(data.counts).filter(([, n]) => n > 0).map(([k, n]) => (
            <button key={k} type="button" className={`btn sm ${filter === k ? "primary" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{TYPE[k] ?? k} ({n})</button>))}
          <div className="spacer" />
          {(data.deferred > 0 || showDeferred) && <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={showDeferred} onChange={(e) => setShowDeferred(e.target.checked)} />
            Show {data.deferred} hidden for later</label>}
        </div>
      )}
      {!data && <div className="card"><Loader2 size={16} className="spin" /> Loading…</div>}
      {data && items.length === 0 && (
        <div className="card"><Empty icon={<Inbox size={24} />} title="Nothing needs you right now"
          hint="Approvals, results to check, failed automations, spending stops, calls and overdue tasks will show up here." /></div>
      )}
      <ul className="col" style={{ listStyle: "none", margin: 0, padding: 0, gap: 10 }}>
        {items.map((i) => (
          <li key={i.id} className="card col" style={{ gap: 6 }} aria-labelledby={`ny-${i.id}`}>
            <div className="row wrap">
              <span className={`badge ${TONE[i.type] ?? ""}`}>{TYPE[i.type] ?? i.type}</span>
              <strong id={`ny-${i.id}`} className="grow" style={{ minWidth: 200 }}>{i.title}</strong>
              {i.at && <span className="faint small">{relTime(i.at)}</span>}
            </div>
            <div className="small" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{i.what}</div>
            <dl className="small ny-facts">
              {i.why && <><dt>Why</dt><dd>{i.why}</dd></>}
              {i.risk && <><dt>Risk</dt><dd>{i.risk}</dd></>}
              {i.cost && <><dt>Cost</dt><dd>{i.cost}</dd></>}
              {i.after && <><dt>Next</dt><dd>{i.after}</dd></>}
            </dl>
            {i.actions.length > 0 && (
              <ul className="col small" style={{ listStyle: "none", margin: 0, padding: 0, gap: 2 }} aria-label="What each choice does">
                {i.actions.map((a) => <li key={a.label}><strong>{a.label}:</strong> <span className="muted">{a.effect}</span></li>)}
              </ul>
            )}
            <div className="row wrap">
              {i.actions.map((a) => (
                <button key={a.label} type="button" className={`btn sm ${a.tone === "primary" ? "primary" : ""}`} disabled={!!busy} onClick={() => run(i, a)}>
                  {busy === `${i.id}:${a.label}` && <Loader2 size={12} className="spin" />} {a.label}</button>))}
              <Link className="btn ghost sm" to={i.link}><ExternalLink size={12} /> Open</Link>
              <div className="spacer" />
              {i.deferred_until ? <span className="faint small"><Clock size={11} /> Hidden until {new Date(i.deferred_until).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span> : (
                <select className="input" style={{ width: 130 }} aria-label={`Hide "${i.title}" for later`} value="" onChange={(e) => e.target.value && later(i, Number(e.target.value))}>
                  <option value="">Later…</option><option value="1">In 1 hour</option><option value="4">In 4 hours</option><option value="20">Tomorrow</option><option value="168">Next week</option>
                </select>)}
            </div>
          </li>
        ))}
      </ul>
      {toast.node}
    </div>
  );
}

/** The count shown next to "Needs you" in the navigation. */
export function useAttentionCount(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const load = () => api<{ count: number }>("/needs-you/count").then((r) => setN(r.count)).catch(() => undefined);
    load();
    // Changes arrive through the live connection (live.ts); this slow check only covers a browser without it
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, typeof EventSource === "undefined" ? 30000 : 300000);
    window.addEventListener("wb:attention", load);
    return () => { window.clearInterval(t); window.removeEventListener("wb:attention", load); };
  }, []);
  return n;
}

export const NeedsYouIcon = BellRing;
