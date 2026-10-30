// feat/v02-core: Projects with memory. One place per job, client, matter or course.
import { Archive, Brain, CalendarClock, Check, FileText, FolderKanban, Globe2, Link2, MessageSquare, NotebookPen, Plus, Trash2, UserRound, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { isUpgrade, Project, ProjectList, UpgradeCard } from "../v02/shared";
import "../v02/v02.css";

type Item = { id: number; kind: string; ref: string; title: string; body: string; due: string | null; done: boolean; auto: boolean; reason: string; overdue?: boolean; created_at: string };
type Detail = Project & { kind_label: string; active: boolean; items: Record<string, Item[]>; facts: { id: number; content: string }[];
  suggestions: { kind: string; ref: string; title: string; matched: string[] }[] };

const ADD: { kind: string; label: string; icon: typeof Plus; title?: string; body?: string; due?: boolean }[] = [
  { kind: "note", label: "Note", icon: NotebookPen, body: "What to remember, e.g. gate code, scope, marking rubric" },
  { kind: "person", label: "Person", icon: UserRound, title: "Name", body: "Role or contact (optional)" },
  { kind: "deadline", label: "Deadline", icon: CalendarClock, title: "What is due", due: true },
  { kind: "link", label: "Link", icon: Link2, title: "Web address" },
];
const SECTIONS: { kind: string; label: string; icon: typeof Plus }[] = [
  { kind: "deadline", label: "Deadlines", icon: CalendarClock }, { kind: "person", label: "People", icon: UserRound },
  { kind: "note", label: "Notes", icon: NotebookPen }, { kind: "file", label: "Files", icon: FileText },
  { kind: "site", label: "Sites", icon: Globe2 }, { kind: "chat", label: "Chats", icon: MessageSquare }, { kind: "link", label: "Links", icon: Link2 },
];

export default function Projects() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [list, setList] = useState<ProjectList | null>(null);
  const [sel, setSel] = useState<Detail | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "job", description: "", keywords: "" });
  const [upgrade, setUpgrade] = useState(false);
  const [adding, setAdding] = useState<string>("");
  const [draft, setDraft] = useState({ title: "", body: "", due: "" });
  const [archived, setArchived] = useState(false);

  const load = useCallback(async () => {
    const r = await api<ProjectList>(`/projects${archived ? "?archived=true" : ""}`);
    setList(r);
    return r;
  }, [archived]);
  const open = useCallback(async (pid: number) => setSel(await api<Detail>(`/projects/${pid}`)), []);
  useEffect(() => {
    load().then((r) => {
      const pid = id ? Number(id) : r.active_id ?? r.projects[0]?.id;
      if (pid) open(pid).catch(toast.err); else setSel(null);
    }).catch(toast.err);
  }, [id, load, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) toast.ok(ok); await load(); if (sel) await open(sel.id).catch(() => setSel(null)); }
    catch (e) { if (isUpgrade(e)) setUpgrade(true); else toast.err(e); }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const p = await api<Detail>("/projects", { body: form });
      setCreating(false); setForm({ name: "", kind: "job", description: "", keywords: "" });
      await load(); navigate(`/projects/${p.id}`);
    } catch (err) { if (isUpgrade(err)) { setCreating(false); setUpgrade(true); } else toast.err(err); }
  };
  const startCreate = () => (list && !list.limit.can_add ? setUpgrade(true) : setCreating(true));

  const addItem = async (e: FormEvent) => {
    e.preventDefault();
    if (!sel) return;
    const body = adding === "link" ? { kind: "link", ref: draft.title, title: draft.title } : { kind: adding, title: draft.title, body: draft.body, due: draft.due || null };
    await act(() => api(`/projects/${sel.id}/items`, { body }));
    setAdding(""); setDraft({ title: "", body: "", due: "" });
  };

  const spec = ADD.find((a) => a.kind === adding);
  const projects = list?.projects ?? [];
  return (
    <div className="page v02">
      {toast.node}
      <div className="page-head">
        <div>
          <div className="v02-kicker">Your work</div>
          <h1>Projects</h1>
          <p className="muted">One place for each job, client, case or course. Files, sites, chats, people and deadlines live together, and the assistant uses them whenever you mention the project.</p>
        </div>
        <button className="btn primary" onClick={startCreate}><Plus size={15} /> New project</button>
      </div>
      {upgrade && <UpgradeCard feature="projects_unlimited" onClose={() => setUpgrade(false)} />}

      {creating && (
        <form className="card col gap v02-create" onSubmit={create}>
          <h3>New project</h3>
          <label className="col small">Name<input className="input" autoFocus value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. 12 Smith St renovation, Acme Pty Ltd, BIOL101" /></label>
          <label className="col small">What kind
            <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              {Object.entries(list?.kinds ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="col small">Words that mean this project (optional)<input className="input" value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} placeholder="job number, client name, case number, course code" /></label>
          <label className="col small">About it (optional)<textarea className="input" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
          <div className="row"><button className="btn primary" disabled={form.name.trim().length < 2}>Create</button><button type="button" className="btn ghost" onClick={() => setCreating(false)}>Cancel</button></div>
        </form>
      )}

      <div className="work-layout">
        <div className="card flush work-list">
          {!projects.length && <div className="pad"><Empty icon={<FolderKanban size={22} />} title={archived ? "Nothing archived" : "No projects yet"} hint={archived ? undefined : "Start one for the job, client or course you're on now."} /></div>}
          {projects.map((p) => (
            <div key={p.id} className={`work-item ${sel?.id === p.id ? "on" : ""}`} role="link" tabIndex={0}
              onClick={() => navigate(`/projects/${p.id}`)} onKeyDown={(e) => { if (e.key === "Enter") navigate(`/projects/${p.id}`); }}>
              <div className="grow">
                <div className="truncate">{p.name}{list?.active_id === p.id && <span className="v02-pill">Open</span>}</div>
                <div className="small faint truncate">
                  {p.status === "archived" ? "Archived" : p.next_deadline ? `Next: ${p.next_deadline.title} · ${p.next_deadline.due}` : `${Object.values(p.counts ?? {}).reduce((a, b) => a + b, 0)} things`}
                </div>
              </div>
            </div>
          ))}
          <div className="pad small"><button className="btn ghost sm" onClick={() => setArchived(!archived)}>{archived ? "Hide archived" : "Show archived"}</button>
            {list && !list.limit.unlimited && <div className="faint" style={{ marginTop: 6 }}>Free plan: {list.limit.active} of {list.limit.free_limit} project in use.</div>}
          </div>
        </div>

        <div className="work-main">
          {!sel && <div className="card"><Empty icon={<FolderKanban size={24} />} title="Pick or start a project" /></div>}
          {sel && (
            <div className="card col gap-lg v02-project">
              <div className="row wrap" style={{ alignItems: "flex-start" }}>
                <div className="grow">
                  <div className="v02-kicker">{sel.kind_label}</div>
                  <h2 className="v02-title">{sel.name}</h2>
                  {sel.description && <p className="muted">{sel.description}</p>}
                  {sel.keywords && <div className="small faint">Recognised by: {sel.keywords}</div>}
                </div>
                {sel.status === "active" && (sel.active
                  ? <button className="btn sm" onClick={() => act(() => api("/projects/active", { method: "PUT", body: { project_id: null } }), "Closed. New chats start without a project.")}><X size={14} /> Close</button>
                  : <button className="btn primary sm" onClick={() => act(() => api("/projects/active", { method: "PUT", body: { project_id: sel.id } }), "New chats will use this project")}><Check size={14} /> Work on this</button>)}
                <button className="btn sm" onClick={() => navigate("/assistant", { state: { draft: `About ${sel.name}: ` } })}><MessageSquare size={14} /> Ask about it</button>
                <button className="btn ghost sm icon" aria-label={sel.status === "archived" ? "Restore project" : "Archive project"} title={sel.status === "archived" ? "Restore" : "Archive"}
                  onClick={() => act(() => api(`/projects/${sel.id}`, { method: "PUT", body: { status: sel.status === "archived" ? "active" : "archived" } }), sel.status === "archived" ? "Restored" : "Archived")}><Archive size={14} /></button>
                <button className="btn ghost sm icon" aria-label="Delete project" onClick={() => confirm(`Delete ${sel.name}? Its files, chats and sites stay in the app.`) && act(() => api(`/projects/${sel.id}`, { method: "DELETE" }), "Deleted").then(() => navigate("/projects"))}><Trash2 size={14} /></button>
              </div>

              <div className="row wrap v02-add">
                {ADD.map((a) => <button key={a.kind} className={`btn sm ${adding === a.kind ? "primary" : ""}`} onClick={() => setAdding(adding === a.kind ? "" : a.kind)}><a.icon size={14} /> {a.label}</button>)}
              </div>
              {spec && (
                <form className="col gap v02-add-form" onSubmit={addItem}>
                  {spec.title && <input className="input" autoFocus value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder={spec.title} />}
                  {spec.body && <textarea className="input" rows={2} autoFocus={!spec.title} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} placeholder={spec.body} />}
                  {spec.due && <input className="input" type="date" value={draft.due} onChange={(e) => setDraft({ ...draft, due: e.target.value })} aria-label="Due date" />}
                  <div className="row"><button className="btn primary sm" disabled={!(draft.title.trim() || draft.body.trim())}>Add</button><button type="button" className="btn ghost sm" onClick={() => setAdding("")}>Cancel</button></div>
                </form>
              )}

              {sel.suggestions.length > 0 && (
                <div className="v02-suggest">
                  <h3>Might belong here</h3>
                  {sel.suggestions.map((s) => (
                    <div key={s.kind + s.ref} className="row v02-row">
                      <span className="badge">{s.kind}</span><span className="grow truncate">{s.title}</span>
                      <span className="small faint">mentions {s.matched.join(", ")}</span>
                      <button className="btn sm" onClick={() => act(() => api(`/projects/${sel.id}/items`, { body: { kind: s.kind, ref: s.ref } }))}><Plus size={13} /> Add</button>
                      <button className="btn ghost sm icon" aria-label="Not this project" onClick={() => act(() => api(`/projects/${sel.id}/dismiss`, { body: { kind: s.kind, ref: s.ref } }))}><X size={13} /></button>
                    </div>
                  ))}
                </div>
              )}

              {SECTIONS.filter((s) => sel.items[s.kind]?.length).map((s) => (
                <section key={s.kind} className="v02-section">
                  <h3><s.icon size={14} /> {s.label}</h3>
                  {sel.items[s.kind].map((it) => (
                    <div key={it.id} className={`row v02-row ${it.overdue ? "late" : ""} ${it.done ? "done" : ""}`}>
                      {it.kind === "deadline" && <input type="checkbox" checked={it.done} aria-label={`Done: ${it.title}`} onChange={() => act(() => api(`/projects/items/${it.id}`, { method: "PUT", body: { done: !it.done } }))} />}
                      <div className="grow">
                        {it.kind === "chat" ? <a href={`/assistant/${it.ref}`} onClick={(e) => { e.preventDefault(); navigate(`/assistant/${it.ref}`); }}>{it.title}</a>
                          : it.kind === "file" ? <a href="/files" onClick={(e) => { e.preventDefault(); navigate("/files"); }}>{it.title}</a>
                          : it.kind === "site" ? <a href="/sites" onClick={(e) => { e.preventDefault(); navigate("/sites"); }}>{it.title}</a>
                          : it.kind === "link" ? <a href={it.ref} target="_blank" rel="noreferrer noopener">{it.title}</a>
                          : it.kind === "note" ? <span className="v02-note">{it.body}</span> : <span>{it.title}</span>}
                        {it.kind === "person" && it.body && <span className="small muted"> · {it.body}</span>}
                        {it.auto && <div className="small faint">Filed automatically: {it.reason}</div>}
                      </div>
                      {it.due && <span className={`small ${it.overdue ? "v02-late" : "muted"}`}>{it.overdue ? "Overdue · " : ""}{it.due}</span>}
                      <button className="btn ghost sm icon" aria-label="Take out of project" onClick={() => act(() => api(`/projects/items/${it.id}`, { method: "DELETE" }))}><X size={13} /></button>
                    </div>
                  ))}
                </section>
              ))}
              {!SECTIONS.some((s) => sel.items[s.kind]?.length) && <Empty title="Nothing here yet" hint="Add a person, deadline or note above. Chats and files that mention this project are filed here automatically." />}

              {sel.facts.length > 0 && (
                <section className="v02-section">
                  <h3><Brain size={14} /> What I know that relates</h3>
                  {sel.facts.map((f) => <div key={f.id} className="small v02-row">{f.content}</div>)}
                  <a className="small" href="/about-you" onClick={(e) => { e.preventDefault(); navigate("/about-you"); }}>Edit in What I know</a>
                </section>
              )}
              {sel.last_used_at && <div className="small faint">Last worked on {relTime(sel.last_used_at)}</div>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
