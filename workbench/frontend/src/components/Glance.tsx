import { BellRing, Cpu, Flag, Gauge, MonitorPlay } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

/* Home at a glance: missions in progress, the next check-ins, the connected computer, model health and spend.
   Each tile reads its own real source; a tile whose source cannot be reached says so instead of guessing. */

type Mission = { id: number; goal: string; status: string; steps: number; done: number };
type Topic = { id: number; name: string; enabled: number; next_check_at: string | null };
type Comp = { settings: { enabled: boolean }; devices: { name: string; os: string; online: boolean }[] };
type MS = { default: { ref: string; error: string }; providers: { name: string; ok: boolean; checked: boolean; error: string }[] };
type Usage = { month_cents: number; month_tokens: number; limits: { monthly_limit_cents: number } };

const ACTIVE = ["planning", "review", "queued", "running", "waiting_approval", "review_step", "paused"];
const LABEL: Record<string, string> = { planning: "planning", review: "plan to check", queued: "starting", running: "working", waiting_approval: "needs approval", review_step: "step to check", paused: "paused" };

function when(iso: string) {
  const d = new Date(iso);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86400000);
  const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `today ${t}` : days === 1 ? `tomorrow ${t}` : `${d.toLocaleDateString([], { weekday: "short" })} ${t}`;
}

export default function Glance() {
  const [m, setM] = useState<Mission[] | null | "err">(null);
  const [t, setT] = useState<Topic[] | null | "err">(null);
  const [c, setC] = useState<Comp | null | "err">(null);
  const [ms, setMs] = useState<MS | null | "err">(null);
  const [u, setU] = useState<Usage | null | "err">(null);
  useEffect(() => {
    api<Mission[]>("/missions").then(setM).catch(() => setM("err"));
    api<{ topics: Topic[] }>("/checkins").then((r) => setT(r.topics)).catch(() => setT("err"));
    api<Comp>("/computer").then(setC).catch(() => setC("err"));
    api<MS>("/models/status").then(setMs).catch(() => setMs("err"));
    api<Usage>("/usage").then(setU).catch(() => setU("err"));
  }, []);
  const err = <span className="muted small">Could not load this right now.</span>;
  const wait = <div className="skeleton" style={{ height: 36 }} />;
  const active = Array.isArray(m) ? m.filter((x) => ACTIVE.includes(x.status)) : [];
  const next = Array.isArray(t) ? t.filter((x) => x.enabled && x.next_check_at).sort((a, b) => (a.next_check_at! < b.next_check_at! ? -1 : 1)).slice(0, 3) : [];
  const dev = c && c !== "err" ? c.devices.find((d) => d.online) ?? c.devices[0] : undefined;
  const bad = ms && ms !== "err" ? ms.providers.filter((p) => p.checked && !p.ok) : [];
  const limit = u && u !== "err" ? u.limits.monthly_limit_cents : 0;
  return (
    <section className="glance" aria-label="At a glance">
      <Link to="/missions" className="glance-tile">
        <div className="glance-head"><Flag size={15} aria-hidden="true" /> Missions</div>
        {m === null ? wait : m === "err" ? err : active.length ? <>
          <div className="glance-num">{active.length} <span className="small muted">open</span></div>
          {active.slice(0, 2).map((x) => <div key={x.id} className="small truncate">{x.goal} <span className="faint">· {LABEL[x.status] ?? x.status}</span></div>)}
        </> : <div className="small muted">Nothing in progress. Hand ROOKS a bigger job here.</div>}
      </Link>
      <Link to="/automations" className="glance-tile">
        <div className="glance-head"><BellRing size={15} aria-hidden="true" /> Next check-ins</div>
        {t === null ? wait : t === "err" ? err : next.length ? next.map((x) => <div key={x.id} className="small truncate">{x.name} <span className="faint">· {when(x.next_check_at!)}</span></div>)
          : <div className="small muted">None scheduled. A morning briefing takes one click.</div>}
      </Link>
      <Link to="/settings#s-models" className="glance-tile">
        <div className="glance-head"><Cpu size={15} aria-hidden="true" /> AI models</div>
        {ms === null ? wait : ms === "err" ? err : ms.default.error ? <div className="small" style={{ color: "var(--danger)" }}>{ms.default.error}</div> : <>
          <div className="small truncate"><strong>{ms.default.ref.split("/").slice(1).join("/")}</strong></div>
          <div className={`small ${bad.length ? "" : "muted"}`} style={bad.length ? { color: "var(--warn)" } : undefined}>
            {bad.length ? `${bad.map((p) => p.name).join(", ")}: needs attention` : `${ms.providers.length} provider${ms.providers.length === 1 ? "" : "s"}, all working`}</div>
        </>}
      </Link>
      <Link to="/settings#s-usage" className="glance-tile">
        <div className="glance-head"><Gauge size={15} aria-hidden="true" /> Spent this month</div>
        {u === null ? wait : u === "err" ? err : <>
          <div className="glance-num">${(u.month_cents / 100).toFixed(2)}{limit > 0 && <span className="small muted"> of ${(limit / 100).toFixed(2)}</span>}</div>
          {limit > 0 ? <div className="progress" role="progressbar" aria-label="Monthly limit used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(100, (u.month_cents / limit) * 100))}>
            <div style={{ width: `${Math.min(100, (u.month_cents / limit) * 100)}%`, background: u.month_cents >= limit * 0.9 ? "var(--warn)" : undefined }} /></div>
            : <div className="small muted">{u.month_tokens.toLocaleString()} tokens · no monthly limit set</div>}
        </>}
      </Link>
      <Link to="/computer" className="glance-tile">
        <div className="glance-head"><MonitorPlay size={15} aria-hidden="true" /> Computer</div>
        {c === null ? wait : c === "err" ? err : dev ? <div className="small"><span className={`dot`} style={{ display: "inline-block", marginRight: 6, background: dev.online ? "var(--ok)" : "var(--faint)" }} />
          {dev.name} · {dev.online ? (c.settings.enabled ? "connected" : "connected, Computer Use off") : "offline"}</div>
          : <div className="small muted">No computer paired. Optional: lets ROOKS use your apps and folders.</div>}
      </Link>
    </section>
  );
}
