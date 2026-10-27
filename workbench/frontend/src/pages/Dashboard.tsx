import { AlertTriangle, ArrowRight, Bell, Brain, CalendarClock, CalendarDays, Check, CheckSquare, FileText, KeyRound, MessageSquare, NotebookPen, Plus, Search, Send, ShieldAlert, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, CalEvent, Doc, Task } from "../api";
import { fmtTime, relTime, todayISO, useToast } from "../util";
import Glance from "../components/Glance";

type Dash = {
  events: CalEvent[];
  tasks_due: Task[];
  overdue: number;
  open_tasks: number;
  recent_docs: Doc[];
  research: { id: number; question: string; status: string; created_at: string }[];
};
type Ready = {
  dismissed: boolean; providers: number; conversations: number; tasks: number; notes: number; documents: number; research: number;
  calendars: number; mail_accounts: number; schedules: number; agents: number; memories: number; search_provider: string; telegram: boolean;
};
type Inbox = { unread: number; items: { id: number; title: string; source: string; read: number; created_at: string }[] };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function dayLabel(iso: string, allDay: boolean) {
  const d = allDay ? new Date(`${iso}T00:00:00`) : new Date(iso);
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === tomorrow.toDateString()) return "Tomorrow";
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

const STARTERS = [
  "Plan my week from my calendar and tasks",
  "Draft a short follow-up email to a client",
  "Turn this idea into a task list:",
];

function Checklist({ r, onHide }: { r: Ready; onHide: () => void }) {
  // Every tick comes from real data in the workspace
  const items = [
    { done: r.providers > 0, label: "Connect an AI model", hint: "Needed for the assistant, research and agents", to: "/settings", icon: KeyRound },
    { done: r.conversations > 0, label: "Ask ROOKS something", hint: "Try: plan my week", to: "/assistant", icon: MessageSquare },
    { done: r.tasks > 0, label: "Create your first task", hint: "Use quick capture above", to: "/tasks", icon: CheckSquare },
    { done: r.notes + r.documents > 0, label: "Save a note", hint: "Ideas, meeting notes, anything", to: "/notes", icon: NotebookPen },
    r.search_provider
      ? { done: r.research > 0, label: "Run your first research", hint: "Answers with sources", to: "/research", icon: Search }
      : { done: false, label: "Turn on web search", hint: "Add a free Brave or Tavily key so research and the assistant can search", to: "/settings#search", icon: Search },
    { done: r.schedules > 0, label: "Create an automation", hint: "Like a daily briefing at 8:00", to: "/automations", icon: CalendarClock },
  ];
  const left = items.filter((i) => !i.done).length;
  if (left === 0) return null;
  return (
    <section className="card col" aria-labelledby="gs-title">
      <div className="row">
        <h2 id="gs-title" className="grow">Getting started <span className="muted small" style={{ fontWeight: 400 }}>{items.length - left} of {items.length} done</span></h2>
        <button className="btn ghost sm" onClick={onHide} title="Hide the checklist (you can bring it back in Settings)"><X size={14} /> Hide</button>
      </div>
      <div className="progress" role="progressbar" aria-label="Getting started progress" aria-valuenow={items.length - left} aria-valuemin={0} aria-valuemax={items.length}>
        <div style={{ width: `${((items.length - left) / items.length) * 100}%` }} />
      </div>
      {items.some((i) => i.done) && (
        <div className="checklist-done row wrap" style={{ gap: 6 }}><Check size={13} aria-hidden="true" style={{ color: "var(--ok)" }} />
          Done: {items.filter((i) => i.done).map((i) => i.label).join(", ")}</div>
      )}
      <ul className="checklist">
        {items.filter((i) => !i.done).map((i) => (
          <li key={i.label} className={i.done ? "done" : ""}>
            <span className="tick" aria-hidden="true">{i.done && <Check size={12} />}</span>
            <i.icon size={16} className="faint" aria-hidden="true" />
            <div className="grow"><div className="what">{i.label}</div>{!i.done && <div className="muted small">{i.hint}</div>}</div>
            <span className="sr-only">{i.done ? "Done" : "Not done"}</span>
            {!i.done && <Link className="btn sm" to={i.to}>Start <ArrowRight size={13} /></Link>}
          </li>
        ))}
      </ul>
      <div className="row wrap small muted" style={{ gap: 6 }}>Optional:
        <Link to="/capabilities">See what ROOKS can do</Link>·<Link to="/settings#s-privacy">Review privacy</Link>·
        <Link to="/assistant">Try Voice in the Assistant</Link>·<Link to="/computer">Pair your computer</Link></div>
    </section>
  );
}

export default function Dashboard(_: { chatUrl?: string }) {
  const [data, setData] = useState<Dash | null>(null);
  const [ready, setReady] = useState<Ready | null>(null);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [approvals, setApprovals] = useState(0);
  const [ask, setAsk] = useState("");
  const [capture, setCapture] = useState("");
  const [mode, setMode] = useState<"task" | "note" | "research">("task");
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();

  const load = () => {
    api<Dash>("/dashboard").then(setData).catch(toast.err);
    api<Ready>("/getting-started").then(setReady).catch(() => setReady(null));
    api<Inbox>("/inbox").then(setInbox).catch(() => setInbox(null));
    api<unknown[]>("/approvals").then((a) => setApprovals(a.length)).catch(() => setApprovals(0));
  };
  useEffect(() => { load(); }, []);

  const askRooks = (e?: FormEvent, text = ask) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t) return;
    navigate("/assistant", { state: { ask: t } });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = capture.trim();
    if (!text || saving) return;
    setSaving(true);
    try {
      if (mode === "task") {
        await api("/tasks", { body: { title: text } });
        toast.ok("Task added");
      } else if (mode === "note") {
        const [first, ...rest] = text.split("\n");
        await api("/docs", { body: { kind: "note", title: first.slice(0, 120), body: rest.join("\n") || text } });
        toast.ok("Note saved");
      } else {
        const r = await api<{ id: number }>("/research", { body: { question: text } });
        navigate(`/research/${r.id}`);
        return;
      }
      setCapture("");
      load();
    } catch (err) {
      toast.err(err); // the text stays in the box so nothing is lost
    } finally {
      setSaving(false);
    }
  };

  const hideChecklist = async () => {
    try { setReady(await api<Ready>("/getting-started", { method: "PUT", body: { dismissed: true } })); toast.ok("Checklist hidden. Bring it back from Settings."); }
    catch (e) { toast.err(e); }
  };

  const today = todayISO();
  const noModel = ready !== null && ready.providers === 0;
  const unread = inbox?.unread ?? 0;
  const attention = (data?.overdue ?? 0) + approvals + unread;
  const hasAgenda = !!data && (data.events.length > 0 || data.tasks_due.length > 0);
  const hasRecent = !!data && (data.recent_docs.length > 0 || data.research.length > 0);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{greeting()}</h1>
          <p className="muted">{new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}. Here's what's happening in your workspace.</p>
        </div>
      </header>

      <div className="col gap-lg">
        <section className="card col hero" aria-labelledby="ask-title">
          <h2 id="ask-title">What do you want to get done?</h2>
          <form className="row" onSubmit={askRooks}>
            <label htmlFor="home-ask" className="sr-only">Ask ROOKS</label>
            <input id="home-ask" className="input grow lg" value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask ROOKS anything, in plain English" />
            <button className="btn primary lg" disabled={!ask.trim()} aria-label="Ask ROOKS" title="Ask ROOKS"><Send size={16} /><span className="hide-narrow">Ask</span></button>
          </form>
          <div className="row wrap" aria-label="Suggestions">
            {STARTERS.map((s) => <button key={s} type="button" className="chip" onClick={() => (s.endsWith(":") ? setAsk(`${s} `) : askRooks(undefined, s))}>{s}</button>)}
          </div>
          {noModel && <div className="alert">The assistant needs an AI model before it can answer. <Link to="/settings">Connect one in Settings</Link>. Everything else on this page works without it.</div>}
        </section>

        {!noModel && <Glance />}

        {attention > 0 && (
          <section className="card col" aria-labelledby="att-title">
            <div className="row"><Bell size={16} aria-hidden="true" /><h2 id="att-title" className="grow">Needs your attention</h2><Link className="btn ghost sm" to="/needs-you">See everything <ArrowRight size={13} /></Link></div>
            <div className="row wrap">
              {approvals > 0 && <Link className="btn sm" to="/needs-you"><ShieldAlert size={14} /> {approvals} waiting for approval</Link>}
              {(data?.overdue ?? 0) > 0 && <Link className="btn sm" to="/tasks"><AlertTriangle size={14} style={{ color: "var(--danger)" }} /> {data!.overdue} overdue {data!.overdue === 1 ? "task" : "tasks"}</Link>}
              {unread > 0 && <Link className="btn sm" to="/automations"><Bell size={14} /> {unread} unread in your inbox</Link>}
            </div>
          </section>
        )}

        {ready && !ready.dismissed && <Checklist r={ready} onHide={hideChecklist} />}

        <form className="card col" onSubmit={submit} aria-labelledby="qc-title">
          <div className="row wrap">
            <h2 id="qc-title" className="grow">Quick capture</h2>
            <div className="tabs" role="tablist" aria-label="What to capture">
              {(["task", "note", "research"] as const).map((m) => (
                <button type="button" role="tab" aria-selected={mode === m} key={m} className={`tab ${mode === m ? "active" : ""}`} onClick={() => setMode(m)}>
                  {m === "task" ? "Task" : m === "note" ? "Note" : "Research"}
                </button>
              ))}
            </div>
          </div>
          <div className="row">
            <label htmlFor="capture" className="sr-only">{mode === "task" ? "New task" : mode === "note" ? "New note" : "Research question"}</label>
            <input id="capture" className="input grow" value={capture} onChange={(e) => setCapture(e.target.value)}
              placeholder={mode === "task" ? "Add a task, like: Send the invoice by Friday" : mode === "note" ? "Jot something down" : "Ask a question to research, answered with sources"} />
            <button className="btn primary" disabled={!capture.trim() || saving} aria-label={mode === "research" ? "Start research" : "Save"} title={mode === "research" ? "Start research" : "Save"}>
              {mode === "research" ? <Search size={16} /> : <Plus size={16} />}
            </button>
          </div>
          {mode === "research" && ready && !ready.search_provider && <div className="muted small">Research needs web search. <Link to="/settings#search">Turn on web search</Link> first.</div>}
        </form>

        {hasAgenda && (
          <div className="grid two">
            <section className="card flush" aria-labelledby="up-title">
              <div className="card-head" style={{ padding: "14px 14px 0" }}>
                <CalendarDays size={16} aria-hidden="true" /><h2 id="up-title">Coming up</h2><div className="spacer" /><Link to="/calendar" className="small">Calendar <ArrowRight size={12} /></Link>
              </div>
              {data!.events.length === 0 && <p className="muted small" style={{ padding: "0 14px 14px", margin: 0 }}>Nothing on your calendar this week.</p>}
              {data!.events.slice(0, 8).map((ev) => (
                <div key={ev.id} className="list-item" style={{ cursor: "default" }}>
                  <span className="dot" style={{ background: ev.color }} aria-hidden="true" />
                  <div className="grow">
                    <div className="truncate">{ev.title || "(untitled)"}</div>
                    <div className="muted small">{dayLabel(ev.start, !!ev.all_day)} · {ev.all_day ? "All day" : fmtTime(ev.start)}{ev.location ? ` · ${ev.location}` : ""}</div>
                  </div>
                </div>
              ))}
            </section>
            <section className="card flush" aria-labelledby="due-title">
              <div className="card-head" style={{ padding: "14px 14px 0" }}>
                <CheckSquare size={16} aria-hidden="true" /><h2 id="due-title">Due soon</h2><div className="spacer" /><Link to="/tasks" className="small">{data!.open_tasks} open <ArrowRight size={12} /></Link>
              </div>
              {data!.tasks_due.length === 0 && <p className="muted small" style={{ padding: "0 14px 14px", margin: 0 }}>Nothing due this week.</p>}
              {data!.tasks_due.slice(0, 8).map((t) => (
                <Link key={t.id} to="/tasks" className="list-item">
                  <div className="grow truncate">{t.title}</div>
                  <span className={`badge ${t.due! < today ? "danger" : t.due === today ? "warn" : ""}`}>{t.due! < today ? "Overdue" : t.due === today ? "Today" : t.due}</span>
                </Link>
              ))}
            </section>
          </div>
        )}

        {hasRecent && (
          <div className="grid two">
            {data!.recent_docs.length > 0 && (
              <section className="card flush" aria-labelledby="rw-title">
                <div className="card-head" style={{ padding: "14px 14px 0" }}><FileText size={16} aria-hidden="true" /><h2 id="rw-title">Recent writing</h2></div>
                {data!.recent_docs.map((d) => (
                  <Link key={d.id} to={`/${d.kind === "note" ? "notes" : "documents"}/${d.id}`} className="list-item">
                    {d.kind === "note" ? <NotebookPen size={15} className="faint" aria-label="Note" /> : <FileText size={15} className="faint" aria-label="Document" />}
                    <div className="grow truncate">{d.title}</div>
                    <span className="faint small">{relTime(d.updated_at)}</span>
                  </Link>
                ))}
              </section>
            )}
            {data!.research.length > 0 && (
              <section className="card flush" aria-labelledby="rs-title">
                <div className="card-head" style={{ padding: "14px 14px 0" }}><Search size={16} aria-hidden="true" /><h2 id="rs-title">Research</h2><div className="spacer" /><Link to="/research" className="small">All <ArrowRight size={12} /></Link></div>
                {data!.research.map((r) => (
                  <Link key={r.id} to={`/research/${r.id}`} className="list-item">
                    <div className="grow truncate">{r.question}</div>
                    <span className={`badge ${r.status === "done" ? "ok" : r.status === "error" ? "danger" : "accent"}`}>{r.status === "done" ? "Done" : r.status === "error" ? "Failed" : "Working"}</span>
                  </Link>
                ))}
              </section>
            )}
          </div>
        )}

        {inbox && inbox.items.length > 0 && (
          <section className="card flush" aria-labelledby="act-title">
            <div className="card-head" style={{ padding: "14px 14px 0" }}><Brain size={16} aria-hidden="true" /><h2 id="act-title">Automation and agent activity</h2><div className="spacer" /><Link to="/automations" className="small">Inbox <ArrowRight size={12} /></Link></div>
            {inbox.items.slice(0, 5).map((i) => (
              <Link key={i.id} to="/automations" className="list-item">
                {!i.read && <span className="dot" style={{ background: "var(--accent)" }} aria-label="Unread" />}
                <div className="grow truncate">{i.title}</div>
                <span className="faint small">{relTime(i.created_at)}</span>
              </Link>
            ))}
          </section>
        )}
      </div>
      {toast.node}
    </div>
  );
}
