import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowUp, Bell, Brain, CalendarClock, CalendarDays, CheckSquare, Code2, FileText, FolderKanban, FolderOpen,
  Globe2, GraduationCap, Inbox, MessageSquare, NotebookPen, Puzzle, Search, UsersRound, Video,
} from "lucide-react";
import { api, CalEvent, Task } from "../api";
import { fmtTime, todayISO, useToast } from "../util";

type Arrived = { project_id: number; name: string; what: string; kind: string };
type Dash = { events: CalEvent[]; tasks_due: Task[]; overdue: number; customers?: Arrived[] };
type Piece = { key: string; line: string; ask: string; note: string };

function dayNote(due: string, today: string) {
  if (due < today) return "Late";
  if (due === today) return "Today";
  return new Date(`${due}T00:00:00`).toLocaleDateString([], { weekday: "short" });
}

const STARTS: { id: string; label: string; text: string; icon: typeof Search }[] = [
  { id: "catchup", label: "Catch me up", text: "Catch me up on my mail, what's due, and anything that changed.", icon: Inbox },
  { id: "day", label: "My day", text: "Plan my day. Check my calendar and what's due, open the places I've shown you and see what's new, then put today in order.", icon: CalendarDays },
  { id: "remind", label: "Remind me", text: "Remind me ", icon: Bell },
  { id: "find", label: "Look it up", text: "Look this up: ", icon: Search },
];

const MORE: { to: string; label: string; icon: typeof Search }[] = [
  { to: "/assistant", label: "Chat", icon: MessageSquare },
  { to: "/mail", label: "Mail", icon: Inbox },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
  { to: "/notes", label: "Notes", icon: NotebookPen },
  { to: "/files", label: "Files", icon: FolderOpen },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/research", label: "Research", icon: Search },
  { to: "/code", label: "Coding", icon: Code2 },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/meetings", label: "Meetings", icon: Video },
  { to: "/automations", label: "Automations", icon: CalendarClock },
  { to: "/knowledge", label: "Memory", icon: Brain },
  { to: "/skills", label: "Skills", icon: Puzzle },
  { to: "/team", label: "Team", icon: UsersRound },
  { to: "/sites", label: "Sites", icon: Globe2 },
  { to: "/teach", label: "Show me how", icon: GraduationCap },
];

function isToday(iso: string, allDay: boolean) {
  const d = allDay ? new Date(`${iso.slice(0, 10)}T00:00:00`) : new Date(iso);
  return d.toDateString() === new Date().toDateString();
}

function plainWhat(what: string) {
  return what.replace(/^(Mail|Chat):\s*/, "").trim();
}

function pieces(data: Dash, today: string): Piece[] {
  const out: Piece[] = [];
  const seen = new Set<string>();
  const add = (p: Piece) => {
    const k = p.line.toLowerCase();
    if (!p.line || seen.has(k)) return;
    seen.add(k);
    out.push(p);
  };
  for (const t of data.tasks_due ?? []) {
    if (t.due && t.due < today) add({ key: `late${t.id}`, line: t.title, ask: `${t.title} is late. What should happen now?`, note: "Late" });
  }
  for (const ev of data.events ?? []) {
    if (!isToday(ev.start, !!ev.all_day)) continue;
    const title = ev.title || "Untitled";
    add({ key: `e${ev.id}`, line: title, ask: `Get me ready for ${title}.`, note: ev.all_day ? "Today" : fmtTime(ev.start) });
  }
  for (const t of data.tasks_due ?? []) {
    if (t.due === today) add({ key: `t${t.id}`, line: t.title, ask: t.title, note: "Today" });
  }
  for (const c of data.customers ?? []) {
    const line = plainWhat(c.what);
    const ask = c.kind === "mail" ? `Look at this mail and tell me what it needs: ${line}` : line;
    add({ key: `a${c.project_id}-${c.kind}-${line}`, line, ask, note: c.kind === "mail" ? "Mail" : "New" });
  }
  for (const t of data.tasks_due ?? []) {
    if (!t.due || t.due <= today) continue;
    add({ key: `wait${t.id}`, line: t.title, ask: t.title, note: dayNote(t.due, today) });
  }
  for (const ev of data.events ?? []) {
    if (isToday(ev.start, !!ev.all_day)) continue;
    const title = ev.title || "Untitled";
    add({ key: `later${ev.id}`, line: title, ask: `What's needed for ${title}?`, note: new Date(ev.start).toLocaleDateString([], { weekday: "short" }) });
  }
  return out.slice(0, 6);
}

export default function Dashboard(_: { chatUrl?: string }) {
  const [data, setData] = useState<Dash | null>(null);
  const [line, setLine] = useState("");
  const [pill, setPill] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();
  const field = useRef<HTMLInputElement>(null);
  const moreRef = useRef<HTMLDivElement>(null);
  const today = todayISO();

  const load = () => api<Dash>("/dashboard").then(setData).catch(toast.err);
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const left = data ? pieces(data, today) : [];
  const asTask = /^add\s+/i.test(line.trim());
  const date = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });

  const choose = (id: string, text: string) => {
    setOpen(false);
    if (pill === id && line === text) {
      setPill(null);
      setLine("");
      return;
    }
    setPill(id);
    setLine(text);
    field.current?.focus();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const text = line.trim();
    if (!text) return;
    if (asTask) {
      try {
        await api("/tasks", { body: { title: text.replace(/^add\s+/i, ""), due: today } });
        setLine("");
        setPill(null);
        load();
      } catch (err) {
        toast.err(err);
      }
      return;
    }
    start(text);
  };

  const start = (text: string) => navigate("/assistant", { state: { draft: text, start: true } });

  return (
    <div className="page home">
      <div className="start-center">
        <p className="desk-date">{date}</p>
        <h1>What should Rooks do?</h1>

        <form className="home-add" onSubmit={submit}>
          <input
            ref={field}
            value={line}
            onChange={(e) => { setLine(e.target.value); if (pill && !STARTS.some((s) => s.text === e.target.value)) setPill(null); }}
            placeholder="Ask anything, or give it a job"
            aria-label="What should happen"
          />
          <button className="home-send" type="submit" disabled={!line.trim()} aria-label={asTask ? "Add" : "Send"}>
            <ArrowUp size={16} />
          </button>
        </form>

        <div className="start-actions">
          {STARTS.map((s) => (
            <button key={s.id} type="button" className={`start-chip${pill === s.id ? " on" : ""}`} aria-pressed={pill === s.id} onClick={() => choose(s.id, s.text)}>
              <s.icon size={14} />
              {s.label}
            </button>
          ))}
          <div className="start-more" ref={moreRef}>
            <button type="button" className={`start-chip${open ? " on" : ""}`} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((v) => !v)}>
              More
            </button>
            {open && (
              <div className="start-menu" role="menu" aria-label="Everything else">
                {MORE.map((item) => (
                  <Link key={item.to} to={item.to} role="menuitem" onClick={() => setOpen(false)}>
                    <item.icon size={16} />
                    {item.label}
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {left.length > 0 && (
        <section className="left-dock" aria-label="Still to do">
          <p>Still to do</p>
          <ul>
            {left.map((p) => (
              <li key={p.key}>
                <button type="button" onClick={() => start(p.ask)}>
                  <span className="truncate">{p.line}</span>
                  <small>{p.note}</small>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {toast.node}
    </div>
  );
}
