import { Check, CheckSquare, Flag, Loader2, Plus, Sparkles, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Task } from "../api";
import { Empty, ModelSelect, SkeletonList, todayISO, useModels, useToast } from "../util";

const PRIO = ["None", "Low", "Medium", "High"];

function TaskRow({ task, onChange, onDelete }: { task: Task; onChange: (t: Task) => void; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(task);
  useEffect(() => setDraft(task), [task]);
  const today = todayISO();

  const patch = async (body: Partial<Task> & { clear_due?: boolean }) => onChange(await api<Task>(`/tasks/${task.id}`, { method: "PATCH", body }));
  const save = () => patch({ title: draft.title, notes: draft.notes, priority: draft.priority, project: draft.project, ...(draft.due ? { due: draft.due } : { clear_due: true }) });

  return (
    <div className={`task ${task.status === "done" ? "done" : ""}`}>
      <button className={`circle ${task.status === "done" ? "done" : ""}`} onClick={() => patch({ status: task.status === "done" ? "todo" : "done" })} aria-label="Toggle done">
        {task.status === "done" && <Check size={12} />}
      </button>
      <div className="grow col" style={{ gap: 4 }}>
        <div className="row" onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
          <span className="title grow">{task.title}</span>
          {task.priority > 0 && <Flag size={13} className={`prio-${task.priority}`} />}
          {task.project && <span className="badge">{task.project}</span>}
          {task.due && <span className={`badge ${task.status !== "done" && task.due < today ? "danger" : task.due === today ? "warn" : ""}`}>{task.due === today ? "Today" : task.due}</span>}
        </div>
        {task.notes && !open && <div className="muted small truncate">{task.notes}</div>}
        {open && (
          <div className="col" style={{ paddingTop: 6 }}>
            <input className="input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            <textarea className="input" rows={3} value={draft.notes} placeholder="Notes" onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            <div className="row wrap">
              <input className="input" type="date" style={{ width: 160 }} value={draft.due ?? ""} onChange={(e) => setDraft({ ...draft, due: e.target.value || null })} />
              <select className="input" style={{ width: 130 }} value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })}>
                {PRIO.map((p, i) => <option key={p} value={i}>{p} priority</option>)}
              </select>
              <input className="input" style={{ width: 160 }} placeholder="Project" value={draft.project} onChange={(e) => setDraft({ ...draft, project: e.target.value })} />
              <select className="input" style={{ width: 130 }} value={task.status} onChange={(e) => patch({ status: e.target.value as Task["status"] })}>
                <option value="todo">To do</option><option value="doing">In progress</option><option value="done">Done</option>
              </select>
              <div className="spacer" />
              <button className="btn ghost sm danger" onClick={onDelete}><Trash2 size={14} /></button>
              <button className="btn primary sm" onClick={async () => { await save(); setOpen(false); }}>Save</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [filter, setFilter] = useState<"open" | "done" | "all">("open");
  const [project, setProject] = useState("");
  const [goal, setGoal] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const { models } = useModels();
  const toast = useToast();

  const load = () => api<Task[]>("/tasks").then(setTasks).catch(toast.err).finally(() => setLoaded(true));
  useEffect(() => {
    load();
  }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      const t = await api<Task>("/tasks", { body: { title, due: due || null, project } });
      setTasks((l) => [t, ...l]);
      setTitle("");
      setDue("");
    } catch (err) {
      toast.err(err);
    }
  };

  const breakdown = async () => {
    setBusy(true);
    try {
      const created = await api<Task[]>("/tasks/breakdown", { body: { goal, project, model: model || null } });
      setTasks((l) => [...created, ...l]);
      setGoal("");
      toast.ok(`Added ${created.length} tasks`);
    } catch (err) {
      toast.err(err);
    } finally {
      setBusy(false);
    }
  };

  const projects = useMemo(() => [...new Set(tasks.map((t) => t.project).filter(Boolean))].sort(), [tasks]);
  const today = todayISO();
  const visible = tasks.filter((t) => (filter === "all" || (filter === "done" ? t.status === "done" : t.status !== "done")) && (!project || t.project === project));
  const groups: [string, Task[]][] = filter === "done"
    ? [["Completed", visible]]
    : [
        ["Overdue", visible.filter((t) => t.status !== "done" && t.due && t.due < today)],
        ["Today", visible.filter((t) => t.status !== "done" && t.due === today)],
        ["Upcoming", visible.filter((t) => t.status !== "done" && t.due && t.due > today)],
        ["No date", visible.filter((t) => t.status !== "done" && !t.due)],
        ...(filter === "all" ? [["Completed", visible.filter((t) => t.status === "done")] as [string, Task[]]] : []),
      ];

  const update = (t: Task) => setTasks((l) => l.map((x) => (x.id === t.id ? t : x)));
  const remove = async (id: number) => {
    await api(`/tasks/${id}`, { method: "DELETE" });
    setTasks((l) => l.filter((x) => x.id !== id));
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Tasks</h1><p className="muted">Agents in Chat can read, add and complete these.</p></div>
        <div className="spacer" />
        <div className="tabs">
          {(["open", "done", "all"] as const).map((f) => <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>{f[0].toUpperCase() + f.slice(1)}</button>)}
        </div>
        {projects.length > 0 && (
          <select className="input" style={{ width: 160 }} value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects</option>
            {projects.map((p) => <option key={p}>{p}</option>)}
          </select>
        )}
      </div>

      <form className="card row wrap" onSubmit={add} style={{ marginBottom: 12 }}>
        <input className="input grow" style={{ minWidth: 200 }} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a task and press Enter" />
        <input className="input" type="date" style={{ width: 160 }} value={due} onChange={(e) => setDue(e.target.value)} />
        <button className="btn primary" disabled={!title.trim()}><Plus size={16} /> Add</button>
      </form>

      <div className="card row wrap" style={{ marginBottom: 20 }}>
        <Sparkles size={16} className="prio-1" />
        <input className="input grow" style={{ minWidth: 200 }} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Break a goal into tasks, e.g. 'Plan a team offsite in March'" />
        <div style={{ width: 180 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
        <button className="btn" disabled={goal.trim().length < 3 || busy} onClick={breakdown}>{busy ? <Loader2 size={15} className="spin" /> : "Break down"}</button>
      </div>

      {!loaded && <div className="card flush"><SkeletonList rows={4} avatar /></div>}
      {loaded && visible.length === 0 && <Empty icon={<CheckSquare size={24} />} title={filter === "done" ? "Nothing completed yet" : "All clear"} />}
      <div className="col gap-lg">
        {groups.filter(([, l]) => l.length).map(([name, list]) => (
          <div key={name}>
            <div className="row" style={{ marginBottom: 6 }}><h3 className={name === "Overdue" ? "prio-3" : ""}>{name}</h3><span className="faint small">{list.length}</span></div>
            <div className="card flush">{list.map((t) => <TaskRow key={t.id} task={t} onChange={update} onDelete={() => remove(t.id)} />)}</div>
          </div>
        ))}
      </div>
      {toast.node}
    </div>
  );
}
