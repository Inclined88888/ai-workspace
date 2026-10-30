import { ArrowRight, CalendarDays, CheckSquare, FileText, NotebookPen, Plus, Search, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, CalEvent, Doc, Task } from "../api";
import { Empty, fmtTime, relTime, Skeleton, SkeletonList, todayISO, useToast } from "../util";

type Dash = {
  events: CalEvent[];
  tasks_due: Task[];
  overdue: number;
  open_tasks: number;
  recent_docs: Doc[];
  research: { id: number; question: string; status: string; created_at: string }[];
};

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

export default function Dashboard(_: { chatUrl?: string }) {
  const [data, setData] = useState<Dash | null>(null);
  const [capture, setCapture] = useState("");
  const [mode, setMode] = useState<"task" | "note" | "research">("task");
  const toast = useToast();
  const navigate = useNavigate();

  const load = () => api<Dash>("/dashboard").then(setData).catch(toast.err);
  useEffect(() => {
    load();
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = capture.trim();
    if (!text) return;
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
      toast.err(err);
    }
  };

  const today = todayISO();
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{greeting()}</h1>
          <p className="muted">{new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</p>
        </div>
        <div className="spacer" />
        <Link className="btn primary" to="/assistant"><Sparkles size={16} /> Ask your assistant</Link>
      </div>

      <form className="card col" onSubmit={submit} style={{ marginBottom: 16 }}>
        <div className="row">
          <div className="tabs">
            {(["task", "note", "research"] as const).map((m) => (
              <button type="button" key={m} className={`tab ${mode === m ? "active" : ""}`} onClick={() => setMode(m)}>
                {m === "task" ? "Task" : m === "note" ? "Note" : "Research"}
              </button>
            ))}
          </div>
          <span className="muted small">Quick capture</span>
        </div>
        <div className="row">
          <input
            className="input grow"
            value={capture}
            onChange={(e) => setCapture(e.target.value)}
            placeholder={mode === "task" ? "Add a task…" : mode === "note" ? "Jot something down…" : "Ask a research question, answered with sources…"}
          />
          <button className="btn primary" disabled={!capture.trim()}>{mode === "research" ? <Search size={16} /> : <Plus size={16} />}</button>
        </div>
      </form>

      <div className="grid three stats" style={{ marginBottom: 16 }}>
        <div className="card stat"><span className="muted small">Open tasks</span><span className="num">{data ? data.open_tasks : <Skeleton w={36} h={28} />}</span></div>
        <div className="card stat"><span className="muted small">Overdue</span><span className="num" style={{ color: data?.overdue ? "var(--danger-text)" : undefined }}>{data ? data.overdue : <Skeleton w={36} h={28} />}</span></div>
        <div className="card stat"><span className="muted small">Events this week</span><span className="num">{data ? data.events.length : <Skeleton w={36} h={28} />}</span></div>
      </div>

      <div className="grid two">
        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 14px 0" }}>
            <CalendarDays size={16} /><h2>Coming up</h2><div className="spacer" /><Link to="/calendar" className="small">Calendar <ArrowRight size={12} /></Link>
          </div>
          {!data && <SkeletonList rows={3} />}
          {data && data.events.length === 0 && <Empty icon={<CalendarDays size={22} />} title="Nothing scheduled" hint="Add a calendar from the Calendar page." />}
          {data?.events.slice(0, 8).map((ev) => (
            <div key={ev.id} className="list-item" style={{ cursor: "default" }}>
              <span className="dot" style={{ background: ev.color }} />
              <div className="grow">
                <div className="truncate">{ev.title || "(untitled)"}</div>
                <div className="muted small">{dayLabel(ev.start, !!ev.all_day)} · {ev.all_day ? "All day" : fmtTime(ev.start)}{ev.location ? ` · ${ev.location}` : ""}</div>
              </div>
            </div>
          ))}
        </div>

        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 14px 0" }}>
            <CheckSquare size={16} /><h2>Due soon</h2><div className="spacer" /><Link to="/tasks" className="small">Tasks <ArrowRight size={12} /></Link>
          </div>
          {!data && <SkeletonList rows={3} />}
          {data && data.tasks_due.length === 0 && <Empty icon={<CheckSquare size={22} />} title="Nothing due this week" />}
          {data?.tasks_due.slice(0, 8).map((t) => (
            <Link key={t.id} to="/tasks" className="list-item">
              <div className="grow truncate">{t.title}</div>
              <span className={`badge ${t.due! < today ? "danger" : t.due === today ? "warn" : ""}`}>{t.due! < today ? "Overdue" : t.due === today ? "Today" : t.due}</span>
            </Link>
          ))}
        </div>

        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 14px 0" }}><FileText size={16} /><h2>Recent writing</h2></div>
          {!data && <SkeletonList rows={3} />}
          {data && data.recent_docs.length === 0 && <Empty icon={<NotebookPen size={22} />} title="No notes yet" />}
          {data?.recent_docs.map((d) => (
            <Link key={d.id} to={`/${d.kind === "note" ? "notes" : "documents"}/${d.id}`} className="list-item">
              {d.kind === "note" ? <NotebookPen size={15} className="faint" /> : <FileText size={15} className="faint" />}
              <div className="grow truncate">{d.title}</div>
              <span className="faint small">{relTime(d.updated_at)}</span>
            </Link>
          ))}
        </div>

        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 14px 0" }}><Search size={16} /><h2>Research</h2><div className="spacer" /><Link to="/research" className="small">All <ArrowRight size={12} /></Link></div>
          {!data && <SkeletonList rows={3} />}
          {data && data.research.length === 0 && <Empty icon={<Search size={22} />} title="No research yet" hint="Use quick capture above." />}
          {data?.research.map((r) => (
            <Link key={r.id} to={`/research/${r.id}`} className="list-item">
              <div className="grow truncate">{r.question}</div>
              <span className={`badge ${r.status === "done" ? "ok" : r.status === "error" ? "danger" : "accent"}`}>{r.status}</span>
            </Link>
          ))}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
