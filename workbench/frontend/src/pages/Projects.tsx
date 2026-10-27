import { Archive, ArchiveRestore, Check, CircleHelp, FolderKanban, Gavel, Loader2, MessageSquare, Plus, Search, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Modal } from "../components/ui";
import { Empty, ModelSelect, relTime, useModels, useToast } from "../util";

type Item = { id: number; name: string; description: string; status: string; updated_at: string; deleted_at: string | null;
  counts: { conversations: number; missions: number; tasks: number } };
type Note = { id: number; kind: string; text: string; resolved: number; answer: string; created_at: string };
type Project = {
  id: number; name: string; description: string; goals: string; instructions: string; model: string; budget_cents: number;
  local_only: number; isolated: number; permissions: Record<string, string>; status: string; deleted_at: string | null; spend_cents: number;
  conversations: { id: number; title: string; updated_at: string }[]; missions: { id: number; goal: string; status: string; updated_at: string }[];
  tasks: { id: number; title: string; status: string; due: string | null }[]; docs: { id: number; kind: string; title: string; updated_at: string }[];
  files: { id: number; name: string; size: number; created_at: string }[]; research: { id: number; question: string; status: string; created_at: string }[];
  memories: { id: number; content: string; kind: string; source: string; updated_at: string }[];
  decisions: Note[]; questions: Note[]; timeline: { at: string; what: string; link: string }[];
};
type Group = { id: string; label: string };
const PERM_LABEL: Record<string, string> = { on: "As in Settings", ask: "Ask first", off: "Off in this project" };

function NewProject({ onCreated, onClose }: { onCreated: (id: number) => void; onClose: () => void }) {
  const [f, setF] = useState({ name: "", description: "", goals: "" });
  const toast = useToast();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { const p = await api<Project>("/projects", { body: f }); onCreated(p.id); } catch (err) { toast.err(err); }
  };
  return (
    <Modal title="New project" onClose={onClose}>
      <form className="col" onSubmit={submit}>
        <label className="field">Name<input className="input" value={f.name} maxLength={120} required onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. Kitchen renovation" /></label>
        <label className="field">What it is about<textarea className="input" rows={2} value={f.description} maxLength={4000} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <label className="field">Goals<textarea className="input" rows={2} value={f.goals} maxLength={4000} onChange={(e) => setF({ ...f, goals: e.target.value })} placeholder="What done looks like" /></label>
        <div className="row"><button className="btn primary" disabled={!f.name.trim()}><Plus size={14} /> Create</button>
          <span className="muted small">Instructions, model, budget and permissions can be set next.</span></div>
      </form>
      {toast.node}
    </Modal>
  );
}

function Settings({ p, onSaved }: { p: Project; onSaved: () => void }) {
  const [f, setF] = useState({ name: p.name, description: p.description, goals: p.goals, instructions: p.instructions, model: p.model,
    budget_cents: p.budget_cents, local_only: !!p.local_only, isolated: !!p.isolated, permissions: p.permissions });
  const [groups, setGroups] = useState<Group[]>([]);
  const { models } = useModels();
  const toast = useToast();
  useEffect(() => { api<{ groups: Group[] }>("/permissions").then((r) => setGroups(r.groups)).catch(() => setGroups([])); }, []);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { await api(`/projects/${p.id}`, { method: "PATCH", body: f }); toast.ok("Saved"); onSaved(); } catch (err) { toast.err(err); }
  };
  return (
    <form className="card col" onSubmit={save}>
      <h3>Project settings</h3>
      <label className="field">Name<input className="input" value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label className="field">Description<textarea className="input" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
      <label className="field">Goals<textarea className="input" rows={2} value={f.goals} onChange={(e) => setF({ ...f, goals: e.target.value })} /></label>
      <label className="field">Instructions for ROOKS in this project<textarea className="input" rows={3} value={f.instructions} maxLength={8000}
        onChange={(e) => setF({ ...f, instructions: e.target.value })} placeholder="e.g. Write in British English. The client is Acme; never contact them directly." /></label>
      <div className="row wrap" style={{ alignItems: "flex-end" }}>
        <label className="field">Model<ModelSelect value={f.model} onChange={(model) => setF({ ...f, model })} models={models} defaultLabel="Same as everywhere" /></label>
        <label className="field">Monthly budget in US dollars <span className="hint">Leave at 0 for no project limit; your workspace limit still applies.</span>
          <input className="input" type="number" min={0} step="0.01" inputMode="decimal" style={{ width: 140 }} value={f.budget_cents / 100}
            onChange={(e) => setF({ ...f, budget_cents: Math.max(0, Math.round(Number(e.target.value || 0) * 100)) })} /></label>
        <span className="small muted">Spent this month: ${(p.spend_cents / 100).toFixed(2)}</span>
      </div>
      <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={f.local_only} onChange={(e) => setF({ ...f, local_only: e.target.checked })} />
        Local models only: work in this project never goes to a cloud model, not even as a backup</label>
      <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={f.isolated} onChange={(e) => setF({ ...f, isolated: e.target.checked })} />
        Keep separate: only this project's memories plus who you are and how you like things done are recalled here</label>
      {groups.length > 0 && (
        <details className="small"><summary style={{ cursor: "pointer" }}>Permissions in this project (can only be stricter than Settings)</summary>
          <div className="col" style={{ gap: 4, marginTop: 8 }}>
            {groups.map((g) => (
              <label key={g.id} className="row" style={{ gap: 8 }}><span className="grow">{g.label}</span>
                <select className="input" style={{ width: "auto" }} value={f.permissions[g.id] ?? "on"}
                  onChange={(e) => setF({ ...f, permissions: { ...f.permissions, [g.id]: e.target.value } })}>
                  {Object.entries(PERM_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            ))}
          </div>
        </details>
      )}
      <div><button className="btn primary sm"><Check size={13} /> Save</button></div>
      {toast.node}
    </form>
  );
}

function Notes({ p, onChanged }: { p: Project; onChanged: () => void }) {
  const [text, setText] = useState({ decision: "", question: "" });
  const toast = useToast();
  const add = async (kind: "decision" | "question") => {
    if (!text[kind].trim()) return;
    try { await api(`/projects/${p.id}/notes`, { body: { kind, text: text[kind] } }); setText({ ...text, [kind]: "" }); onChanged(); } catch (e) { toast.err(e); }
  };
  const resolve = async (n: Note) => {
    const answer = prompt("Answer (optional)") ?? "";
    try { await api(`/projects/notes/${n.id}/resolve`, { body: { answer } }); onChanged(); } catch (e) { toast.err(e); }
  };
  return (
    <div className="card col">
      <div className="row"><Gavel size={15} aria-hidden="true" /><h3 className="grow">Decisions</h3></div>
      {p.decisions.length === 0 ? <div className="muted small">None recorded yet. ROOKS records them with you as you work.</div> :
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.decisions.map((d) => <li key={d.id}>{d.text} <span className="faint">{relTime(d.created_at)}</span></li>)}</ul>}
      <form className="row" onSubmit={(e) => { e.preventDefault(); add("decision"); }}>
        <input className="input grow" placeholder="Add a decision" value={text.decision} onChange={(e) => setText({ ...text, decision: e.target.value })} aria-label="New decision" />
        <button className="btn sm">Add</button></form>
      <div className="row"><CircleHelp size={15} aria-hidden="true" /><h3 className="grow">Open questions</h3></div>
      {p.questions.length === 0 ? <div className="muted small">No open questions.</div> : (
        <ul className="col small" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
          {p.questions.map((q) => (
            <li key={q.id} className="row wrap" style={{ gap: 6 }}>
              <span className={`grow ${q.resolved ? "faint" : ""}`}>{q.text}{q.resolved && q.answer ? ` → ${q.answer}` : ""}</span>
              {!q.resolved ? <button className="btn ghost sm" onClick={() => resolve(q)}>Answer</button> : <span className="badge ok">answered</span>}
            </li>
          ))}
        </ul>
      )}
      <form className="row" onSubmit={(e) => { e.preventDefault(); add("question"); }}>
        <input className="input grow" placeholder="Add a question" value={text.question} onChange={(e) => setText({ ...text, question: e.target.value })} aria-label="New question" />
        <button className="btn sm">Add</button></form>
      {toast.node}
    </div>
  );
}

function Detail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [p, setP] = useState<Project | null>(null);
  const [purging, setPurging] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const toast = useToast();
  const navigate = useNavigate();
  const load = () => api<Project>(`/projects/${id}`).then(setP).catch((e) => { toast.err(e); setP(null); });
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!p) return <div className="card"><Loader2 size={16} className="spin" /> Loading…</div>;
  const act = async (path: string, method = "POST", msg = "Done") => {
    try { await api(`/projects/${p.id}${path}`, { method }); toast.ok(msg); load(); onChanged(); } catch (e) { toast.err(e); }
  };
  const purge = async () => {
    try { await api(`/projects/${p.id}/purge`, { body: { confirm: confirmName } }); setPurging(false); onChanged(); navigate("/projects"); } catch (e) { toast.err(e); }
  };
  const binned = !!p.deleted_at;
  const live = p.status === "active" && !binned;
  return (
    <div className="col gap-lg">
      <div className="card col">
        <div className="row wrap" style={{ alignItems: "flex-start" }}>
          <h2 className="grow">{p.name}</h2>
          <span className={`badge ${binned ? "danger" : p.status === "archived" ? "" : "ok"}`}>{binned ? "In the bin" : p.status === "archived" ? "Archived" : "Active"}</span>
          {!!p.local_only && <span className="badge">Local models only</span>}
          {!!p.isolated && <span className="badge">Kept separate</span>}
        </div>
        {p.description && <p className="muted" style={{ margin: 0 }}>{p.description}</p>}
        {p.goals && <div className="small"><strong>Goals:</strong> {p.goals}</div>}
        <div className="row wrap">
          {live && <Link className="btn primary sm" to={`/assistant?project=${p.id}`}><MessageSquare size={13} /> New chat in this project</Link>}
          {live && <Link className="btn sm" to={`/missions?project=${p.id}`}>New mission</Link>}
          {live && <button className="btn ghost sm" onClick={() => act("/archive", "POST", "Archived")}><Archive size={13} /> Archive</button>}
          {(p.status === "archived" || binned) && <button className="btn sm" onClick={() => act("/restore", "POST", "Restored")}><ArchiveRestore size={13} /> Restore</button>}
          {!binned && <button className="btn ghost sm danger" onClick={() => { if (confirm(`Move ${p.name} to the bin? Everything in it stays and comes back with Restore.`)) act("", "DELETE", "Moved to the bin"); }}><Trash2 size={13} /> Move to bin</button>}
          {binned && <button className="btn danger sm" onClick={() => setPurging(true)}><Trash2 size={13} /> Delete for good</button>}
        </div>
      </div>

      <div className="two-pane">
        <div className="col">
          <div className="card col">
            <h3>Work</h3>
            <div className="small"><strong>Conversations</strong></div>
            {p.conversations.length ? p.conversations.map((c) => <Link key={c.id} className="small truncate" to={`/assistant/${c.id}`}>{c.title} <span className="faint">{relTime(c.updated_at)}</span></Link>) : <div className="muted small">None yet.</div>}
            <div className="small"><strong>Missions</strong></div>
            {p.missions.length ? p.missions.map((m) => <Link key={m.id} className="small truncate" to={`/missions/${m.id}`}>{m.goal} <span className="badge">{m.status}</span></Link>) : <div className="muted small">None yet.</div>}
            <div className="small"><strong>Tasks</strong></div>
            {p.tasks.length ? p.tasks.map((t) => <div key={t.id} className={`small ${t.status === "done" ? "faint" : ""}`}>{t.status === "done" ? "✓ " : ""}{t.title}{t.due ? ` (due ${t.due})` : ""}</div>) : <div className="muted small">None yet.</div>}
            {p.docs.length > 0 && <><div className="small"><strong>Notes and documents</strong></div>
              {p.docs.map((d) => <Link key={d.id} className="small truncate" to={`/${d.kind === "document" ? "documents" : "notes"}/${d.id}`}>{d.title}</Link>)}</>}
            {p.research.length > 0 && <><div className="small"><strong>Research</strong></div>
              {p.research.map((r) => <Link key={r.id} className="small truncate" to={`/research/${r.id}`}>{r.question}</Link>)}</>}
            {p.files.length > 0 && <><div className="small"><strong>Files</strong></div>
              {p.files.map((f) => <Link key={f.id} className="small truncate" to={`/files/${f.id}`}>{f.name}</Link>)}</>}
          </div>
          <Notes p={p} onChanged={load} />
          <div className="card col">
            <h3>Remembered in this project</h3>
            {p.memories.length === 0 ? <div className="muted small">Nothing yet. Facts ROOKS saves while working here stay in this project.</div> : (
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{p.memories.map((m) => <li key={m.id}>{m.content} <span className="faint">{m.kind}</span></li>)}</ul>
            )}
            <Link className="small" to="/assistant-memory">Manage all memories</Link>
          </div>
        </div>
        <div className="col">
          <Settings key={p.id + p.name} p={p} onSaved={() => { load(); onChanged(); }} />
          <div className="card col">
            <h3>Timeline</h3>
            {p.timeline.length === 0 ? <div className="muted small">Nothing has happened yet.</div> : (
              <ul className="col small" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
                {p.timeline.map((t, i) => <li key={i}><span className="faint">{relTime(t.at)}</span> · {t.link ? <Link to={t.link}>{t.what}</Link> : t.what}</li>)}
              </ul>
            )}
          </div>
        </div>
      </div>

      {purging && (
        <Modal title={`Delete ${p.name} for good`} onClose={() => setPurging(false)}>
          <p className="small" style={{ margin: 0 }}>This cannot be undone. Its project memories, decisions and questions are deleted. Its conversations, missions, tasks, notes and files are kept, no longer in a project.</p>
          <label className="field">Type <strong>{p.name}</strong> to confirm<input className="input" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} /></label>
          <div className="row"><button className="btn danger" disabled={confirmName !== p.name} onClick={purge}>Delete for good</button><button className="btn" onClick={() => setPurging(false)}>Cancel</button></div>
        </Modal>
      )}
      {toast.node}
    </div>
  );
}

export default function Projects() {
  const { id } = useParams();
  const [items, setItems] = useState<Item[] | null>(null);
  const [q, setQ] = useState("");
  const [view, setView] = useState<"active" | "archived" | "bin">("active");
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const load = () => api<Item[]>(`/projects?q=${encodeURIComponent(q)}${view === "archived" ? "&archived=true" : view === "bin" ? "&bin=true" : ""}`)
    .then(setItems).catch(() => setItems([]));
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [q, view]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="page" style={{ maxWidth: 1200 }}>
      <div className="page-head"><div><h1>Projects</h1><p className="muted">One place per piece of ongoing work: its goals, conversations, missions, decisions and memory, so ROOKS picks up where you left off.</p></div>
        <button className="btn primary" onClick={() => setCreating(true)}><Plus size={14} /> New project</button></div>
      <div className={`two-pane projects-pane${id ? " has-detail" : ""}`}>
        <div className="col">
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} />
            <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects" aria-label="Search projects" />
          </div>
          <div className="row" role="tablist" aria-label="Which projects">
            {(["active", "archived", "bin"] as const).map((v) => <button key={v} role="tab" aria-selected={view === v} className={`tab ${view === v ? "active" : ""}`} onClick={() => setView(v)}>
              {{ active: "Active", archived: "With archived", bin: "Bin" }[v]}</button>)}
          </div>
          {items && items.length === 0 && <Empty icon={<FolderKanban size={22} />} title={view === "bin" ? "The bin is empty" : "No projects yet"} hint={view === "active" ? "Create one for each client, job or goal you keep coming back to." : undefined} />}
          {items && items.length > 0 && (
            <div className="card flush" role="list" aria-label="Projects">
              {items.map((it) => (
                <Link key={it.id} role="listitem" to={`/projects/${it.id}`} className={`list-item${String(it.id) === id ? " active" : ""}`}>
                  <span className="grow"><span className="truncate" style={{ display: "block" }}>{it.name}{it.status === "archived" ? " (archived)" : ""}</span>
                    <span className="small muted">{it.counts.conversations} chats · {it.counts.missions} missions · {it.counts.tasks} tasks · {relTime(it.updated_at)}</span></span>
                </Link>
              ))}
            </div>
          )}
        </div>
        <div>{id ? <Detail key={id} id={+id} onChanged={load} /> : <div className="card"><Empty icon={<FolderKanban size={24} />} title="Pick a project" hint="Or create one. Chats and missions started in a project use its brief, its memories and its rules." /></div>}</div>
      </div>
      {creating && <NewProject onClose={() => setCreating(false)} onCreated={(pid) => { setCreating(false); load(); navigate(`/projects/${pid}`); }} />}
    </div>
  );
}
