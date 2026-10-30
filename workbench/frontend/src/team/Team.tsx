// feat/team-lead: the team lead's place. Assign in plain English, see who's doing what, approve what goes out.
import { AlertTriangle, Bell, CalendarRange, Check, ChevronLeft, Inbox, LayoutGrid, Lock, Plus, Send, Settings2, Sparkles, Sun, Trash2, UserRound, Users, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { isUpgrade, UpgradeCard } from "../v02/shared";
import {
  addDays, bar, CHANNEL_LABEL, daysBetween, Member, niceDate, Notice, OutboxItem, Overview, Plan, PRIORITY_LABEL, Reply, STATUS_LABEL,
  Suggestion, Task,
} from "./types";
import "./team.css";

const TABS = [
  { id: "board", label: "Board", icon: LayoutGrid },
  { id: "workload", label: "Workload", icon: Users },
  { id: "timeline", label: "Timeline", icon: CalendarRange },
  { id: "today", label: "Today's plan", icon: Sun },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;
type Tab = (typeof TABS)[number]["id"];

function useTeam() {
  const toast = useToast();
  const [data, setData] = useState<Overview | null>(null);
  const [upgrade, setUpgrade] = useState(false);
  const load = useCallback(() => api<Overview>("/team").then(setData).catch(toast.err), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  const act = async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    try { const r = await fn(); if (ok) toast.ok(ok); await load(); return r; }
    catch (e) { if (isUpgrade(e)) setUpgrade(true); else toast.err(e); return undefined; }
  };
  return { data, load, act, toast, upgrade, setUpgrade };
}

export default function Team() {
  const { id } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "board";
  const t = useTeam();
  if (id) return <PersonPage id={Number(id)} team={t} />;
  const d = t.data;
  return (
    <div className="page team">
      {t.toast.node}
      <div className="page-head">
        <div>
          <div className="v02-kicker">Your team</div>
          <h1>Team</h1>
          <p className="muted">Give people work in plain words. See who's on what, what's late and what's stuck. Nothing goes out to your team without your OK unless you've said so in Settings.</p>
        </div>
        {d && <div className="team-stats" aria-label="Team at a glance">
          <Stat n={d.counts.doing ?? 0} label="doing" /><Stat n={d.at_risk} label="need a look" warn={d.at_risk > 0} /><Stat n={d.unassigned} label="not given out" />
        </div>}
      </div>
      {t.upgrade && <UpgradeCard feature="team_tasks" onClose={() => t.setUpgrade(false)} />}
      <AssignBar team={t} />
      {d && <Approvals d={d} team={t} />}
      <nav className="tabs team-tabs" role="tablist">
        {TABS.map((x) => (
          <button key={x.id} role="tab" aria-selected={tab === x.id} className={`tab ${tab === x.id ? "active" : ""}`} onClick={() => setSp(x.id === "board" ? {} : { tab: x.id })}>
            <x.icon size={14} aria-hidden="true" /> {x.label}
          </button>
        ))}
      </nav>
      {!d ? <div className="card pad faint">Loading…</div> : !d.members.length && tab !== "settings" ? <FirstRun team={t} /> : (
        <div className="team-view" key={tab}>
          {tab === "board" && <Board d={d} team={t} />}
          {tab === "workload" && <Workload d={d} />}
          {tab === "timeline" && <Timeline d={d} />}
          {tab === "today" && <TodayPlan team={t} />}
          {tab === "settings" && <SettingsTab d={d} team={t} />}
        </div>
      )}
    </div>
  );
}

type TeamCtx = ReturnType<typeof useTeam>;

function Stat({ n, label, warn }: { n: number; label: string; warn?: boolean }) {
  return <div className={`team-stat ${warn ? "warn" : ""}`}><span className="team-stat-n">{n}</span><span className="small muted">{label}</span></div>;
}

function AssignBar({ team }: { team: TeamCtx }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    const r = await team.act(() => api<{ task: Task; message?: OutboxItem; suggestion?: Suggestion }>("/team/tasks/from-text", { body: { text } }));
    setBusy(false);
    if (r) {
      setText("");
      team.toast.ok(r.message ? (r.message.status === "waiting" ? "Added. The message is waiting for your OK below." : "Added and sent.")
        : r.suggestion ? "Added. See who I'd give it to below." : "Added.");
    }
  };
  return (
    <form className="team-assign card" onSubmit={go}>
      <Sparkles size={18} aria-hidden="true" className="team-assign-icon" />
      <input className="input grow" value={text} onChange={(e) => setText(e.target.value)} aria-label="Give someone a task"
        placeholder="Give Sam the Smith St quote by Friday, urgent" />
      <button className="btn primary" disabled={busy || !text.trim()}><Plus size={15} /> Assign</button>
    </form>
  );
}

function Approvals({ d, team }: { d: Overview; team: TeamCtx }) {
  const [all, setAll] = useState(false);
  const waiting = d.outbox.filter((o) => o.status === "waiting");
  if (!d.suggestions.length && !waiting.length && !d.replies.length) return null;
  const names = Object.fromEntries(d.members.map((m) => [m.id, m.name]));
  // replies and messages first (people are waiting on those), then ideas
  const cards = [
    ...d.replies.map((r) => <ReplyCard key={`r${r.id}`} r={r} team={team} tasks={d.tasks.filter((t) => t.assignee_id === r.member_id && t.status !== "done")} />),
    ...waiting.map((o) => <MessageCard key={`o${o.id}`} o={o} to={names[o.member_id] ?? "Someone"} team={team} />),
    ...d.suggestions.map((s) => <SuggestionCard key={`s${s.id}`} s={s} team={team} />),
  ];
  const shown = all ? cards : cards.slice(0, 6);
  return (
    <section className="team-approvals" aria-label="Waiting for you">
      <h2 className="team-h2">Waiting for you <span className="job-count">{cards.length}</span></h2>
      <div className="team-cards">{shown}</div>
      {cards.length > 6 && <button className="btn ghost sm team-more" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${cards.length}`}</button>}
    </section>
  );
}

export function SuggestionCard({ s, team }: { s: Suggestion; team: TeamCtx }) {
  return (
    <article className="team-card sug">
      <div className="team-card-kind"><Sparkles size={12} aria-hidden="true" /> {s.kind === "rebalance" ? "Even out the load" : s.kind === "next" ? "Next up" : "Who should do it"}</div>
      <div className="team-card-title">{s.headline}</div>
      <div className="small muted">{s.reason}</div>
      <div className="row team-card-actions">
        <button className="btn primary sm" onClick={() => team.act(() => api(`/team/suggestions/${s.id}/approve`, { method: "POST" }), "Done")}><Check size={13} /> Yes</button>
        <button className="btn ghost sm" onClick={() => team.act(() => api(`/team/suggestions/${s.id}/dismiss`, { method: "POST" }))}>Not now</button>
      </div>
    </article>
  );
}

function MessageCard({ o, to, team }: { o: OutboxItem; to: string; team: TeamCtx }) {
  const [edit, setEdit] = useState(false);
  const [body, setBody] = useState(o.body);
  return (
    <article className="team-card msg">
      <div className="team-card-kind"><Send size={12} aria-hidden="true" /> {o.kind === "nudge" ? "Check-in" : o.kind === "plan" ? "Today's plan" : "New task"} · {CHANNEL_LABEL[o.channel] ?? o.channel} to {to}</div>
      {edit ? <textarea className="input" rows={4} value={body} onChange={(e) => setBody(e.target.value)} aria-label="Message" />
        : <div className="team-msg-body">{o.body}</div>}
      <div className="small faint">{o.why}</div>
      <div className="row team-card-actions">
        <button className="btn primary sm" onClick={() => team.act(() => api(`/team/outbox/${o.id}/approve`, { body: edit ? { body } : {} }), "Sent")}><Send size={13} /> Send</button>
        <button className="btn ghost sm" onClick={() => setEdit(!edit)}>{edit ? "Done editing" : "Edit"}</button>
        <button className="btn ghost sm" onClick={() => team.act(() => api(`/team/outbox/${o.id}/cancel`, { method: "POST" }))}><X size={13} /> Don't send</button>
      </div>
    </article>
  );
}

function ReplyCard({ r, team, tasks }: { r: Reply; team: TeamCtx; tasks: Task[] }) {
  const [pick, setPick] = useState<number | "">(r.task_id ?? "");
  const apply = () => team.act(() => api(`/team/replies/${r.id}/apply`, { method: "POST", body: { task_id: pick || null } }), "Task updated");
  return (
    <article className="team-card reply">
      <div className="team-card-kind"><Inbox size={12} aria-hidden="true" /> Reply by {r.source === "sms" ? "text" : r.source}</div>
      <div className="team-card-title">{r.member_name} {r.task_title ? <>about “{r.task_title}”: {r.summary}</> : "sent a message"}</div>
      <blockquote className="team-quote small">{r.text.slice(0, 240)}</blockquote>
      <div className="row team-card-actions">
        {!r.task_id && tasks.length > 0 && (
          <select className="input sm" aria-label="Which task" value={pick} onChange={(e) => setPick(e.target.value ? Number(e.target.value) : "")}>
            <option value="">Which task?</option>
            {tasks.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        )}
        {pick !== "" && <button className="btn primary sm" onClick={apply}><Check size={13} /> Update the task</button>}
        <button className="btn ghost sm" onClick={() => team.act(() => api(`/team/replies/${r.id}/dismiss`, { method: "POST" }))}>Ignore</button>
      </div>
    </article>
  );
}

function RiskTag({ t }: { t: Task }) {
  if (!t.risk) return null;
  return <span className={`team-risk ${t.risk.level}`}><AlertTriangle size={11} aria-hidden="true" /> {t.risk.label}</span>;
}

function TaskCard({ t, team, members }: { t: Task; team: TeamCtx; members: Member[] }) {
  const set = (body: Partial<Task>) => team.act(() => api(`/team/tasks/${t.id}`, { method: "PATCH", body }));
  return (
    <div className={`team-task ${t.status} ${t.risk ? "risky" : ""}`}>
      <div className="row team-task-top">
        <span className="team-task-title grow">{t.title}</span>
        {t.priority >= 2 && <span className="team-prio">{PRIORITY_LABEL[t.priority]}</span>}
      </div>
      <div className="small muted row team-task-meta">
        {t.due && <span>Due {niceDate(t.due)}</span>}{t.project && <span className="truncate">{t.project}</span>}
        {t.source !== "manual" && t.source !== "chat" && <span className="team-src">{t.source}</span>}
      </div>
      {t.status !== "todo" && t.status !== "done" && <div className="team-progress" aria-label={`${t.percent}% done`}><span style={{ width: `${t.percent}%` }} /></div>}
      <RiskTag t={t} />
      <div className="row team-task-actions">
        <select className="input sm" value={t.status} aria-label="Status" onChange={(e) => set({ status: e.target.value as Task["status"] })}>
          {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="input sm" value={t.assignee_id ?? ""} aria-label="Who" onChange={(e) => set({ assignee_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">Nobody yet</option>
          {members.map((m) => <option key={m.id} value={m.id}>{m.first_name}</option>)}
        </select>
      </div>
    </div>
  );
}

function Board({ d, team }: { d: Overview; team: TeamCtx }) {
  const [by, setBy] = useState<"person" | "status">("person");
  const [showDone, setShowDone] = useState(false);
  const tasks = d.tasks.filter((t) => showDone || t.status !== "done");
  const cols = by === "person"
    ? [...d.members.map((m) => ({ key: `m${m.id}`, title: m.name, sub: `${m.role || ""}${m.role ? " · " : ""}${m.load.label}`, link: `/team/people/${m.id}`, load: m.load.percent, tasks: tasks.filter((t) => t.assignee_id === m.id) })),
       { key: "none", title: "Not given out", sub: "Tap Who to give it to someone", link: "", load: -1, tasks: tasks.filter((t) => !t.assignee_id) }]
    : (["todo", "doing", "blocked", "done"] as const).filter((s) => showDone || s !== "done").map((s) => ({ key: s, title: STATUS_LABEL[s], sub: "", link: "", load: -1, tasks: tasks.filter((t) => t.status === s) }));
  return (
    <>
      <div className="row team-toolbar">
        <div className="team-seg" role="group" aria-label="Group by">
          <button className={by === "person" ? "on" : ""} onClick={() => setBy("person")}>By person</button>
          <button className={by === "status" ? "on" : ""} onClick={() => setBy("status")}>By status</button>
        </div>
        <label className="small row"><input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Show finished</label>
      </div>
      <div className="team-board">
        {cols.map((c) => (
          <section key={c.key} className="team-col">
            <header className="team-col-head">
              <h3>{c.link ? <Link to={c.link}>{c.title}</Link> : c.title} <span className="job-count">{c.tasks.length}</span></h3>
              {c.sub && <div className="small faint">{c.sub}</div>}
              {c.load >= 0 && <div className={`team-load ${c.load > 100 ? "over" : ""}`}><span style={{ width: `${Math.min(100, c.load)}%` }} /></div>}
            </header>
            {c.tasks.map((t) => <TaskCard key={t.id} t={t} team={team} members={d.members} />)}
            {!c.tasks.length && <div className="small faint team-empty-col">Nothing here</div>}
          </section>
        ))}
      </div>
    </>
  );
}

function Workload({ d }: { d: Overview }) {
  const max = Math.max(120, ...d.members.map((m) => m.load.percent));
  return (
    <div className="card team-workload">
      <p className="small muted">How booked each person is this week: remaining hours on their open tasks against the hours they work. Over the line means too much.</p>
      {d.members.map((m) => (
        <div key={m.id} className="team-wl-row">
          <Link to={`/team/people/${m.id}`} className="team-wl-name truncate">{m.name}</Link>
          <div className="team-wl-track" role="img" aria-label={`${m.name}: ${m.load.percent}% booked`}>
            <span className={`team-wl-bar ${m.load.percent > 100 ? "over" : m.load.percent < 50 ? "light" : ""}`} style={{ width: `${(100 * Math.min(m.load.percent, max)) / max}%` }} />
            <span className="team-wl-full" style={{ left: `${(100 * 100) / max}%` }} aria-hidden="true" />
          </div>
          <div className="team-wl-num small"><strong>{m.load.percent}%</strong> <span className="faint">{m.load.hours}h of {m.load.capacity}h · {m.load.open} open</span></div>
        </div>
      ))}
    </div>
  );
}

function Timeline({ d }: { d: Overview }) {
  const today = new Date().toISOString().slice(0, 10);
  const from = addDays(today, -3), days = 21;
  const names = Object.fromEntries(d.members.map((m) => [m.id, m.first_name]));
  const groups = useMemo(() => {
    const g: Record<string, Task[]> = {};
    for (const t of d.tasks) if (t.status !== "done" || (t.due && t.due >= from)) (g[t.project || "No project"] ??= []).push(t);
    return Object.entries(g).sort(([a], [b]) => (a === "No project" ? 1 : b === "No project" ? -1 : a.localeCompare(b)));
  }, [d.tasks, from]);
  const ticks = Array.from({ length: days }, (_, i) => addDays(from, i));
  return (
    <div className="card flush team-gantt" style={{ ["--days" as string]: days }}>
      <div className="team-g-row team-g-head">
        <div className="team-g-label small faint">Project / task</div>
        <div className="team-g-grid">{ticks.map((x) => <div key={x} className={`team-g-tick ${x === today ? "now" : ""} ${[0, 6].includes(new Date(x + "T00:00:00").getDay()) ? "wkend" : ""}`}>{new Date(x + "T00:00:00").getDate()}</div>)}</div>
      </div>
      {groups.map(([proj, ts]) => (
        <div key={proj} className="team-g-group">
          <div className="team-g-proj">{proj}</div>
          {ts.map((t) => {
            const b = bar(t, from, days);
            return (
              <div key={t.id} className="team-g-row">
                <div className="team-g-label truncate small">{t.title} <span className="faint">· {t.assignee_id ? names[t.assignee_id] : "nobody"}</span></div>
                <div className="team-g-grid">
                  <span className="team-g-today" style={{ left: `calc(${daysBetween(from, today)} * 100% / var(--days))` }} aria-hidden="true" />
                  {b && <span className={`team-g-bar ${t.status} ${t.risk?.level ?? ""}`} title={`${t.title}${t.due ? ` · due ${t.due}` : ""}`}
                    style={{ left: `calc(${b.left} * 100% / var(--days))`, width: `calc(${b.width} * 100% / var(--days))` }}>
                    <span style={{ width: `${t.percent}%` }} />
                  </span>}
                </div>
              </div>
            );
          })}
        </div>
      ))}
      {!groups.length && <div className="pad"><Empty title="Nothing on the timeline" hint="Tasks with a due date show here." /></div>}
    </div>
  );
}

function TodayPlan({ team }: { team: TeamCtx }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  useEffect(() => { api<Plan>("/team/plan").then(setPlan).catch(team.toast.err); }, [team.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!plan) return <div className="card pad faint">Working out today…</div>;
  const ready = plan.people.filter((p) => p.message).length;
  return (
    <>
      <div className="card team-plan-head row">
        <div className="grow">
          <h2 className="team-h2">What everyone should do today</h2>
          <div className="small muted">{niceDate(plan.date)}: late and due-today work first, then what's under way, up to each person's hours.</div>
        </div>
        <button className="btn primary" disabled={!ready} onClick={() => team.act(() => api("/team/plan/send", { method: "POST" }), "Plans are waiting for your OK above")}>
          <Send size={14} /> Get {ready} plan{ready === 1 ? "" : "s"} ready to send
        </button>
      </div>
      <div className="team-plan">
        {plan.people.map((p) => (
          <article key={p.member.id} className={`card team-plan-person ${p.working ? "" : "off"}`}>
            <header className="row"><Link to={`/team/people/${p.member.id}`} className="team-plan-name grow">{p.member.name}</Link>
              {p.load && <span className="small faint">{p.load.percent}% booked</span>}</header>
            {!p.working ? <div className="small faint">Not working today</div> : !p.tasks.length ? <div className="small faint">Nothing on. Give them something from “Not given out”.</div> : (
              <ol className="team-plan-list">{p.tasks.map((t) => <li key={t.id}><span>{t.title}</span>{t.due && <span className="small faint"> · due {niceDate(t.due)}</span>} <RiskTag t={t} /></li>)}</ol>
            )}
          </article>
        ))}
      </div>
      {!!plan.unassigned.length && <div className="card team-unassigned"><h3 className="team-h3">Not given out yet</h3>{plan.unassigned.map((t) => <div key={t.id} className="small">{t.title}{t.due ? ` · due ${niceDate(t.due)}` : ""}</div>)}</div>}
    </>
  );
}

function MemberForm({ team, onDone, m }: { team: TeamCtx; onDone: () => void; m?: Member }) {
  const [f, setF] = useState({ name: m?.name ?? "", role: m?.role ?? "", skills: m?.skills.join(", ") ?? "", email: m?.contact.email ?? "", phone: m?.contact.phone ?? "",
    telegram: m?.contact.telegram ?? "", channel: m?.channel ?? "email", hours_per_week: m?.hours_per_week ?? 38, work_days: m?.work_days ?? ["mon", "tue", "wed", "thu", "fri"] });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body = { name: f.name, role: f.role, skills: f.skills, contact: { email: f.email, phone: f.phone, telegram: f.telegram }, channel: f.channel, hours_per_week: Number(f.hours_per_week), work_days: f.work_days };
    const r = await team.act(() => api(m ? `/team/members/${m.id}` : "/team/members", { method: m ? "PATCH" : "POST", body }), m ? "Saved" : `${f.name} added`);
    if (r) onDone();
  };
  const days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  return (
    <form className="card col gap team-member-form" onSubmit={save}>
      <h3>{m ? `Edit ${m.first_name}` : "Add someone"}</h3>
      <div className="team-form-grid">
        <label className="col small">Name<input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Sam Rivera" /></label>
        <label className="col small">Role<input className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} placeholder="Estimator, paralegal, lab tech" /></label>
        <label className="col small span2">What they're good at<input className="input" value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} placeholder="quotes, wiring, contracts, PCR" /></label>
        <label className="col small">Email<input className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label className="col small">Mobile<input className="input" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
        <label className="col small">Telegram (chat id or @name)<input className="input" value={f.telegram} onChange={(e) => setF({ ...f, telegram: e.target.value })} /></label>
        <label className="col small">Send their tasks by
          <select className="input" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{Object.entries(CHANNEL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        </label>
        <label className="col small">Hours a week<input className="input" type="number" min={1} max={80} value={f.hours_per_week} onChange={(e) => setF({ ...f, hours_per_week: Number(e.target.value) })} /></label>
        <fieldset className="col small span2 team-days"><legend>Days they work</legend>
          <div className="row">{days.map((x) => <label key={x} className="row small"><input type="checkbox" checked={f.work_days.includes(x)} onChange={(e) => setF({ ...f, work_days: e.target.checked ? [...f.work_days, x] : f.work_days.filter((y) => y !== x) })} /> {x[0].toUpperCase() + x.slice(1)}</label>)}</div>
        </fieldset>
      </div>
      <div className="row"><button className="btn primary" disabled={!f.name.trim()}>{m ? "Save" : "Add"}</button><button type="button" className="btn ghost" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

function FirstRun({ team }: { team: TeamCtx }) {
  const [adding, setAdding] = useState(false);
  return adding ? <MemberForm team={team} onDone={() => setAdding(false)} /> : (
    <div className="card pad"><Empty hero icon={<Users size={26} />} title="Add the people you give work to" hint="Their skills and hours help me suggest who should do what. They don't need the app.">
      <button className="btn primary" onClick={() => setAdding(true)}><Plus size={15} /> Add someone</button></Empty></div>
  );
}

function Toggle({ on, label, hint, onChange }: { on: boolean; label: string; hint: string; onChange: (v: boolean) => void }) {
  return (
    <label className="team-toggle">
      <input type="checkbox" role="switch" checked={on} onChange={(e) => onChange(e.target.checked)} />
      <span className="grow"><span className="team-toggle-label">{label}</span><span className="small muted">{hint}</span></span>
    </label>
  );
}

function SettingsTab({ d, team }: { d: Overview; team: TeamCtx }) {
  const s = d.settings;
  const put = (body: Record<string, unknown>) => team.act(() => api("/team/settings", { method: "PUT", body }), "Saved");
  const [adding, setAdding] = useState(false);
  const [sms, setSms] = useState({ sid: "", token: "", from_number: s.sms.from });
  const [tr, setTr] = useState<Record<string, Record<string, string>>>({ jira: {}, asana: {}, trello: {} });
  const FIELDS: Record<string, [string, string][]> = {
    jira: [["site", "Jira address (https://yours.atlassian.net)"], ["email", "Your Atlassian email"], ["token", "API token"]],
    asana: [["token", "Personal access token"], ["project", "Project id"]],
    trello: [["key", "API key"], ["token", "Token"], ["board", "Board id"]],
  };
  return (
    <div className="team-settings">
      <section className="card col gap">
        <h3 className="team-h3">Who's on the team</h3>
        {d.members.map((m) => <div key={m.id} className="row team-person-row"><Link to={`/team/people/${m.id}`} className="grow">{m.name}</Link><span className="small faint">{m.role} · {CHANNEL_LABEL[m.channel]}</span></div>)}
        {adding ? <MemberForm team={team} onDone={() => setAdding(false)} /> : <div><button className="btn sm" onClick={() => setAdding(true)}><Plus size={13} /> Add someone</button></div>}
      </section>
      <section className="card col gap">
        <h3 className="team-h3">What I may do without asking</h3>
        <p className="small muted">The first message to each person on each channel always waits for you.</p>
        <Toggle on={s.auto_notify} label="Tell people about new tasks straight away" hint="Otherwise each message waits in “Waiting for you”." onChange={(v) => put({ auto_notify: v })} />
        <Toggle on={s.auto_nudge} label="Check in on late or stuck work by myself" hint="At most one check-in a day per task." onChange={(v) => put({ auto_nudge: v })} />
        <Toggle on={s.auto_assign} label="Give out work using my suggestions" hint="Only to people who work that day and have room." onChange={(v) => put({ auto_assign: v })} />
        <label className="row small">Summary for you
          <select className="input sm" value={s.digest} onChange={(e) => put({ digest: e.target.value })}><option value="daily">Every afternoon</option><option value="weekly">Friday afternoon</option><option value="off">Off</option></select>
        </label>
      </section>
      <section className="card col gap">
        <h3 className="team-h3">Reading replies</h3>
        <Toggle on={s.read_inbox} label="Read my team's replies in my inbox" hint="Only mail from people on the team is opened." onChange={(v) => put({ read_inbox: v })} />
        <Toggle on={s.replies_review} label="Check replies before they change a task" hint="Recommended: an email's sender can be faked." onChange={(v) => put({ replies_review: v })} />
        <div className="row"><button className="btn sm" onClick={() => team.act(() => api("/team/replies/check", { method: "POST" }), "Checked")}><Inbox size={13} /> Check now</button></div>
      </section>
      <section className="card col gap">
        <h3 className="team-h3">Text messages (Twilio)</h3>
        <p className="small muted">{s.sms.configured ? `Texts go from ${s.sms.from}.` : "Use your own Twilio account to text people and read their replies. Costs are on your Twilio bill."}</p>
        <div className="team-form-grid">
          <input className="input" placeholder="Account SID (AC…)" value={sms.sid} onChange={(e) => setSms({ ...sms, sid: e.target.value })} aria-label="Twilio Account SID" />
          <input className="input" type="password" placeholder="Auth token" value={sms.token} onChange={(e) => setSms({ ...sms, token: e.target.value })} aria-label="Twilio auth token" />
          <input className="input" placeholder="Twilio number (+61…)" value={sms.from_number} onChange={(e) => setSms({ ...sms, from_number: e.target.value })} aria-label="Twilio number" />
        </div>
        <div><button className="btn sm" onClick={() => team.act(() => api("/team/sms", { method: "PUT", body: { sid: sms.sid || undefined, token: sms.token || undefined, from_number: sms.from_number } }), "Saved")}>Save</button></div>
      </section>
      <section className="card col gap">
        <h3 className="team-h3">Jira, Asana and Trello</h3>
        <p className="small muted">Bring work assigned to your people onto this board. Tokens stay encrypted on this computer.</p>
        {(["jira", "asana", "trello"] as const).map((k) => (
          <div key={k} className="team-tracker">
            <div className="row"><strong className="grow">{k[0].toUpperCase() + k.slice(1)}</strong><span className="small faint">{s.trackers[k].connected ? `Connected${s.trackers[k].last_sync ? ` · synced ${relTime(s.trackers[k].last_sync)}` : ""}` : "Not connected"}</span></div>
            <div className="team-form-grid">{FIELDS[k].map(([f, label]) => (
              <input key={f} className="input" aria-label={label} placeholder={s.trackers[k].fields[f] === "set" ? "•••• saved" : label} type={f === "token" || f === "key" ? "password" : "text"}
                value={tr[k][f] ?? (s.trackers[k].fields[f] !== "set" ? s.trackers[k].fields[f] ?? "" : "")} onChange={(e) => setTr({ ...tr, [k]: { ...tr[k], [f]: e.target.value } })} />))}
            </div>
            <div className="row">
              <button className="btn sm" onClick={() => team.act(() => api(`/team/trackers/${k}`, { method: "PUT", body: tr[k] }), "Saved")}>Save</button>
              {s.trackers[k].connected && <button className="btn sm" onClick={() => team.act(async () => { const r = await api<{ added: number; updated: number }>(`/team/trackers/${k}/sync`, { method: "POST" }); team.toast.ok(`${r.added} added, ${r.updated} updated`); })}>Sync now</button>}
            </div>
          </div>
        ))}
      </section>
      <section className="card team-privacy"><Lock size={16} aria-hidden="true" /><div><h3 className="team-h3">Privacy</h3><p className="small">{s.privacy}</p></div></section>
    </div>
  );
}

function PersonPage({ id, team }: { id: number; team: TeamCtx }) {
  const navigate = useNavigate();
  const [p, setP] = useState<(Member & { tasks: Task[]; updates: { id: number; status: string; percent: number | null; note: string; source: string; created_at: string; task_title?: string }[]; working_today: boolean }) | null>(null);
  const [edit, setEdit] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);
  const load = useCallback(() => api<typeof p>(`/team/members/${id}`).then(setP).catch(team.toast.err), [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); api<Notice[]>("/team/notices").then((n) => setNotices(n.filter((x) => (x as Notice & { member_id?: number }).member_id === id).slice(0, 8))).catch(() => undefined); }, [load, team.data, id]);
  if (!p) return <div className="page team"><div className="card pad faint">Loading…</div></div>;
  const open = p.tasks.filter((t) => t.status !== "done"), done = p.tasks.filter((t) => t.status === "done");
  const link = async () => { const r = await team.act(() => api<{ url: string; reason?: string }>(`/team/members/${id}/link`, { method: "POST" })); if (r?.url) { await navigator.clipboard?.writeText(r.url).catch(() => undefined); team.toast.ok("Private link copied"); } else if (r?.reason) team.toast.err(r.reason); };
  return (
    <div className="page team">
      {team.toast.node}
      <button className="btn ghost sm" onClick={() => navigate("/team")}><ChevronLeft size={14} /> Team</button>
      <div className="page-head team-person-head">
        <div className="team-avatar" aria-hidden="true">{p.first_name[0]}</div>
        <div className="grow">
          <div className="v02-kicker">{p.role || "Team member"}</div>
          <h1>{p.name}</h1>
          <p className="muted small">{p.working_today ? "Working today" : "Not working today"} · {p.work_days.map((x) => x[0].toUpperCase() + x.slice(1)).join(" ")} · {p.hours_per_week}h a week · {CHANNEL_LABEL[p.channel]}{p.last_heard_at ? ` · last heard from ${relTime(p.last_heard_at)}` : ""}</p>
          {!!p.skills.length && <div className="row team-skills">{p.skills.map((s) => <span key={s} className="v02-pill">{s}</span>)}</div>}
        </div>
        <div className="col gap team-person-actions">
          <button className="btn sm" onClick={() => setEdit(!edit)}><UserRound size={13} /> Edit</button>
          {!p.has_app && <button className="btn sm" onClick={link}>Private link</button>}
          <button className="btn ghost danger sm" onClick={async () => { if (confirm(`Remove ${p.name} from the team? Their open tasks go back to “Not given out”.`)) { await team.act(() => api(`/team/members/${id}`, { method: "DELETE" })); navigate("/team"); } }}><Trash2 size={13} /> Remove</button>
        </div>
      </div>
      {edit && <MemberForm team={team} m={p} onDone={() => { setEdit(false); load(); }} />}
      <div className="team-person-grid">
        <section className="card">
          <h3 className="team-h3">Load</h3>
          <div className="team-big">{p.load.percent}%</div>
          <div className={`team-load ${p.load.percent > 100 ? "over" : ""}`}><span style={{ width: `${Math.min(100, p.load.percent)}%` }} /></div>
          <div className="small muted">{p.load.hours}h left on {p.load.open} open task{p.load.open === 1 ? "" : "s"}, {p.load.capacity}h a week</div>
          {!!p.away.length && <><h3 className="team-h3 mt">Away</h3>{p.away.map((a, i) => <div key={i} className="small">{niceDate(a.from)} – {niceDate(a.to)} {a.note}</div>)}</>}
        </section>
        <section className="card col gap">
          <h3 className="team-h3">On now <span className="job-count">{open.length}</span></h3>
          {open.map((t) => <TaskCard key={t.id} t={t} team={team} members={team.data?.members ?? []} />)}
          {!open.length && <div className="small faint">Nothing on.</div>}
          {!!done.length && <details><summary className="small">Finished ({done.length})</summary>{done.map((t) => <div key={t.id} className="small faint">✓ {t.title}</div>)}</details>}
        </section>
        <section className="card col gap">
          <h3 className="team-h3"><Bell size={13} aria-hidden="true" /> What's happened</h3>
          {p.updates.map((u) => <div key={u.id} className="small team-update"><span className="faint">{relTime(u.created_at)}</span> {u.task_title ? <strong>{u.task_title}</strong> : null} {STATUS_LABEL[u.status] ?? ""}{u.percent != null ? ` ${u.percent}%` : ""} {u.note}</div>)}
          {notices.map((n) => <div key={`n${n.id}`} className="small team-update"><span className="faint">{relTime(n.created_at)}</span> {n.title}</div>)}
          {!p.updates.length && !notices.length && <div className="small faint">No updates yet.</div>}
        </section>
      </div>
    </div>
  );
}
