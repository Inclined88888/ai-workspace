import { Check, CircleAlert, CircleDashed, CircleX, Flag, LayoutTemplate, Loader2, Pause, Play, Plus, RotateCcw, ShieldQuestion, Square, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";
import Workers, { SharedBudget, Worker } from "../components/Workers";

type Tool = { name: string; ok: boolean; preview: string; ms: number };
type Step = { title: string; done_when: string; needs_tool: boolean; after?: number[]; status: string; attempts: number; result: string; evidence: string;
  tools: Tool[]; reviewed?: string };
type Approval = { id: number; tool: string; args: string; reason: string };
type Mission = {
  id: number; goal: string; status: string; error: string; report: string; plan: Step[]; current_step: number; conversation_id: number;
  budget_tool_calls: number; budget_minutes: number; budget_cents: number; used_tool_calls: number; used_cents: number; elapsed_seconds: number;
  events: { at: string; step: number | null; kind: string; text: string }[]; approvals: Approval[]; created_at: string;
  project_id: number | null; template: string; max_attempts: number; review_unverified: number; estimate_cents: number | null;
  workers: Worker[]; shared_budget: SharedBudget;
};
const WORKING = ["queued", "running", "waiting_approval", "in_review"];
type ProjectRef = { id: number; name: string };
type Template = { id: string; title: string; about: string; needs: string; inputs: { name: string; label: string; example: string; optional?: boolean }[] };
const NEEDS: Record<string, string> = { companion: "Needs the ROOKS Companion with the folder allowed", email: "Needs email connected" };
type Item = { id: number; goal: string; status: string; steps: number; done: number; updated_at: string };

const LIVE = ["planning", "queued", "running"];
const FINAL = ["done", "partial", "failed", "cancelled"];
const LABEL: Record<string, string> = {
  planning: "Planning", review: "Check the plan", review_step: "Check a step", queued: "Starting", running: "Working", waiting_approval: "Needs your approval",
  paused: "Paused", done: "Done", partial: "Finished with gaps", failed: "Stopped: a step failed", cancelled: "Cancelled",
};
const TONE: Record<string, string> = { review_step: "warn", done: "ok", partial: "warn", failed: "danger", waiting_approval: "warn", review: "accent", running: "accent" };
const STEP_LABEL: Record<string, string> = { pending: "Not started", running: "Working", done: "Done", unverified: "Not verified", failed: "Failed", skipped: "Not run", waiting_approval: "Waiting for approval" };

function StepIcon({ s }: { s: string }) {
  if (s === "done") return <Check size={15} className="ok-text" aria-hidden="true" />;
  if (s === "running") return <Loader2 size={15} className="spin" aria-hidden="true" />;
  if (s === "failed") return <CircleX size={15} className="danger-text" aria-hidden="true" />;
  if (s === "unverified" || s === "waiting_approval") return <CircleAlert size={15} className="warn-text" aria-hidden="true" />;
  return <CircleDashed size={15} className="faint" aria-hidden="true" />;
}

function useProjects() {
  const [list, setList] = useState<ProjectRef[]>([]);
  useEffect(() => { api<ProjectRef[]>("/projects").then(setList).catch(() => setList([])); }, []);
  return list;
}

function ProjectSelect({ value, onChange, projects }: { value: number | null; onChange: (v: number | null) => void; projects: ProjectRef[] }) {
  if (!projects.length) return null;
  return (
    <label className="field">Project<select className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value ? +e.target.value : null)}>
      <option value="">None</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
  );
}

/** Ready-made business workflows: fill in the blanks, get a plan to review. Nothing runs before you approve it. */
function Templates({ onCreated, project, open }: { onCreated: (id: number) => void; project: number | null; open: boolean }) {
  const [list, setList] = useState<Template[]>([]);
  const [pick, setPick] = useState<Template | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [proj, setProj] = useState<number | null>(project);
  const projects = useProjects();
  const toast = useToast();
  useEffect(() => { api<Template[]>("/templates").then(setList).catch(() => setList([])); }, []);
  const start = async (e: FormEvent) => {
    e.preventDefault();
    try { const m = await api<Mission>(`/templates/${pick!.id}/start`, { body: { values, project_id: proj } }); setPick(null); setValues({}); onCreated(m.id); }
    catch (err) { toast.err(err); }
  };
  if (!list.length) return null;
  // Folded away once you have missions of your own, so they stay the first thing on the page
  return (
    <details className="card col" open={open || !!pick}>
      <summary className="row" style={{ cursor: "pointer" }}><LayoutTemplate size={16} aria-hidden="true" /><h3 className="grow">Start from a workflow</h3>
        <span className="muted small">{list.length} ready made</span></summary>
      {!pick ? (
        <div className="col" style={{ gap: 4 }}>
          {list.map((t) => (
            <button key={t.id} type="button" className="choice compact" onClick={() => { setPick(t); setValues({}); }}>
              <span className="grow"><strong>{t.title}</strong><br /><span className="muted small">{t.about}{t.needs && NEEDS[t.needs] ? ` ${NEEDS[t.needs]}.` : ""}</span></span>
            </button>
          ))}
        </div>
      ) : (
        <form className="col" onSubmit={start}>
          <div className="row"><strong className="grow">{pick.title}</strong><button type="button" className="btn ghost sm" onClick={() => setPick(null)}>Back</button></div>
          <div className="muted small">{pick.about}</div>
          {pick.inputs.map((i) => (
            <label key={i.name} className="field">{i.label}{i.optional ? " (optional)" : ""}
              <input className="input" placeholder={i.example} value={values[i.name] ?? ""} onChange={(e) => setValues({ ...values, [i.name]: e.target.value })} required={!i.optional} maxLength={500} /></label>
          ))}
          <ProjectSelect value={proj} onChange={setProj} projects={projects} />
          <div className="row wrap"><button className="btn primary"><Flag size={14} /> Make the plan</button>
            <span className="muted small">You review the plan before anything runs. Steps without evidence wait for you.</span></div>
        </form>
      )}
      {toast.node}
    </details>
  );
}

function NewMission({ onCreated, project }: { onCreated: (id: number) => void; project: number | null }) {
  const [goal, setGoal] = useState("");
  const [model, setModel] = useState("");
  const [limits, setLimits] = useState({ budget_tool_calls: 40, budget_minutes: 30, budget_cents: 0 });
  const [opts, setOpts] = useState({ max_attempts: 2, review_unverified: false });
  const [proj, setProj] = useState<number | null>(project);
  const [busy, setBusy] = useState(false);
  const { models } = useModels();
  const projects = useProjects();
  const toast = useToast();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { const m = await api<Mission>("/missions", { body: { goal, model: model || null, project_id: proj, ...limits, ...opts } }); setGoal(""); onCreated(m.id); }
    catch (err) { toast.err(err); } finally { setBusy(false); }
  };
  return (
    <form className="card col" onSubmit={submit}>
      <label htmlFor="mission-goal" className="field">What should ROOKS get done?</label>
      <textarea id="mission-goal" className="input" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={4000}
        placeholder="e.g. Research three CRM tools for a 5 person team, compare prices, and save a summary note with a recommendation" />
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>Model and limits</summary>
        <div className="row wrap" style={{ marginTop: 8, alignItems: "flex-end" }}>
          <label className="field">Model<ModelSelect models={models} value={model} onChange={setModel} /></label>
          <label className="field">Tool calls<input className="input" type="number" min={1} max={500} style={{ width: 100 }} value={limits.budget_tool_calls} onChange={(e) => setLimits({ ...limits, budget_tool_calls: +e.target.value })} /></label>
          <label className="field">Minutes<input className="input" type="number" min={1} max={1440} style={{ width: 100 }} value={limits.budget_minutes} onChange={(e) => setLimits({ ...limits, budget_minutes: +e.target.value })} /></label>
          <label className="field">Spend limit (cents, 0 = none)<input className="input" type="number" min={0} style={{ width: 120 }} value={limits.budget_cents} onChange={(e) => setLimits({ ...limits, budget_cents: +e.target.value })} /></label>
          <label className="field">Tries per step<input className="input" type="number" min={1} max={5} style={{ width: 90 }} value={opts.max_attempts} onChange={(e) => setOpts({ ...opts, max_attempts: +e.target.value })} /></label>
          <ProjectSelect value={proj} onChange={setProj} projects={projects} />
        </div>
        <label className="row small" style={{ gap: 6, marginTop: 8 }}><input type="checkbox" checked={opts.review_unverified} onChange={(e) => setOpts({ ...opts, review_unverified: e.target.checked })} />
          Stop and ask me when a step finishes without evidence, instead of carrying on</label>
        <div className="muted small">A spend limit is reserved against your monthly limit when the mission starts, so two missions cannot count on the same budget.</div>
      </details>
      <div className="row wrap"><button className="btn primary" disabled={busy || goal.trim().length < 5}>{busy ? <><Loader2 size={14} className="spin" /> Creating…</> : <><Flag size={14} /> Plan it</>}</button>
        <span className="muted small">ROOKS writes a plan first. Nothing runs until you approve it.</span></div>
      {toast.node}
    </form>
  );
}

function PlanEditor({ m, onSaved }: { m: Mission; onSaved: (m: Mission) => void }) {
  const [steps, setSteps] = useState(m.plan.map(({ title, done_when, needs_tool, after }) => ({ title, done_when, needs_tool, after: after ?? [] })));
  const toast = useToast();
  const set = (i: number, patch: Partial<(typeof steps)[0]>) => setSteps(steps.map((s, n) => (n === i ? { ...s, ...patch } : s)));
  const save = async () => { try { onSaved(await api<Mission>(`/missions/${m.id}/plan`, { method: "PUT", body: { steps } })); toast.ok("Plan saved"); } catch (e) { toast.err(e); } };
  return (
    <div className="col">
      {steps.map((s, i) => (
        <div key={i} className="card col" style={{ background: "var(--panel-2)", gap: 6 }}>
          <div className="row"><strong className="grow">Step {i + 1}</strong>
            <button type="button" className="btn ghost sm icon" aria-label={`Remove step ${i + 1}`} disabled={steps.length < 2} onClick={() => setSteps(steps.filter((_, n) => n !== i))}><X size={13} /></button></div>
          <label className="field">What to do<input className="input" value={s.title} maxLength={300} onChange={(e) => set(i, { title: e.target.value })} /></label>
          <label className="field">Done when<input className="input" value={s.done_when} maxLength={500} onChange={(e) => set(i, { done_when: e.target.value })} /></label>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.needs_tool} onChange={(e) => set(i, { needs_tool: e.target.checked })} /> Must use a tool (search, read, create); a claim without one is not trusted</label>
        </div>
      ))}
      <div className="row wrap">
        <button type="button" className="btn sm" disabled={steps.length >= 10} onClick={() => setSteps([...steps, { title: "", done_when: "", needs_tool: true, after: [] }])}><Plus size={13} /> Add step</button>
        <button type="button" className="btn sm" onClick={save} disabled={steps.some((s) => !s.title.trim())}>Save plan</button>
      </div>
      {toast.node}
    </div>
  );
}

function Detail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [m, setM] = useState<Mission | null>(null);
  const [editing, setEditing] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const toast = useToast();
  const navigate = useNavigate();
  const lastStatus = useRef("");

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await api<Mission>(`/missions/${id}`);
        if (stop) return;
        setM(r);
        if (r.status !== lastStatus.current) { lastStatus.current = r.status; onChanged(); }
        const busy = LIVE.includes(r.status) || r.status === "waiting_approval" || r.workers.some((w) => WORKING.includes(w.status));
        timer = setTimeout(poll, busy ? 1200 : 5000);
      } catch (e) { if (!stop) toast.err(e); }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!m) return <div className="card"><Loader2 size={16} className="spin" /> Loading…</div>;
  const act = async (what: string, method = "POST") => {
    try { const r = await api<Mission>(`/missions/${m.id}/${what}`, { method }); setM(r); onChanged(); } catch (e) { toast.err(e); }
  };
  const review = async (i: number, accept: boolean) => {
    try { setM(await api<Mission>(`/missions/${m.id}/steps/${i}/review`, { body: { accept } })); onChanged(); } catch (e) { toast.err(e); }
  };
  const decide = async (a: Approval, approve: boolean) => {
    try { await api(`/approvals/${a.id}`, { body: { approve } }); toast.ok(approve ? "Approved" : "Denied"); setM(await api<Mission>(`/missions/${m.id}`)); } catch (e) { toast.err(e); }
  };
  const remove = async () => {
    if (!confirm("Delete this mission and its log? Notes, tasks and drafts it created stay.")) return;
    try { await api(`/missions/${m.id}`, { method: "DELETE" }); onChanged(); navigate("/missions"); } catch (e) { toast.err(e); }
  };
  const done = m.plan.filter((s) => s.status === "done").length;
  const canStart = ["review", "paused", "failed", "partial"].includes(m.status);
  const startLabel = m.status === "review" ? "Approve plan and start" : m.status === "paused" ? "Resume" : "Run again from the first unfinished step";

  return (
    <div className="col gap-lg">
      <div className="card col">
        <div className="row wrap" style={{ alignItems: "flex-start" }}>
          <h2 className="grow" style={{ minWidth: 200 }}>{m.goal}</h2>
          <span className={`badge ${TONE[m.status] ?? ""}`} role="status">{LIVE.includes(m.status) && <Loader2 size={11} className="spin" />} {LABEL[m.status] ?? m.status}</span>
        </div>
        <div className="row wrap small muted" style={{ gap: 16 }}>
          <span>{done} of {m.plan.length} steps done</span>
          <span>Tool calls {m.used_tool_calls} / {m.budget_tool_calls}</span>
          <span>Time {Math.round(m.elapsed_seconds / 60)} / {m.budget_minutes} min</span>
          {m.budget_cents > 0 && <span>Spent {m.used_cents.toFixed(1)}¢ / {m.budget_cents}¢</span>}
          {m.estimate_cents != null && <span title="Average spend per step of your past missions, times this plan's steps">Estimate about {m.estimate_cents.toFixed(1)}¢</span>}
          {m.project_id && <Link to={`/projects/${m.project_id}`}>Project</Link>}
          {m.template && <span>Workflow: {m.template}</span>}
        </div>
        <div className="progress" role="progressbar" aria-label="Steps done" aria-valuemin={0} aria-valuemax={m.plan.length} aria-valuenow={done}><div style={{ width: `${m.plan.length ? (done / m.plan.length) * 100 : 0}%` }} /></div>
        {m.error && <div className={`alert ${m.status === "paused" ? "warn" : "err"}`}>{m.error}</div>}
        <div className="row wrap">
          {canStart && <button className="btn primary" onClick={() => act("start")}><Play size={14} /> {startLabel}</button>}
          {["running", "queued", "waiting_approval"].includes(m.status) && <button className="btn" onClick={() => act("pause")}><Pause size={14} /> Pause</button>}
          {!FINAL.includes(m.status) && <button className="btn ghost danger" onClick={() => { if (confirm("Cancel this mission? Steps not yet done will not run.")) act("cancel"); }}><Square size={13} /> Cancel</button>}
          {m.status === "review" && !editing && <button className="btn ghost" onClick={() => setEditing(true)}>Edit plan</button>}
          {!["running", "planning"].includes(m.status) && <button className="btn ghost sm" onClick={remove} aria-label="Delete mission"><Trash2 size={13} /></button>}
          <Link className="btn ghost sm" to={`/assistant/${m.conversation_id}`}>Full conversation</Link>
        </div>
      </div>

      {m.status === "review_step" && m.plan[m.current_step]?.status === "unverified" && (
        <div className="card col" style={{ borderColor: "var(--warn)" }} role="region" aria-label="Step needs your review">
          <h3>Step {m.current_step + 1} needs your check</h3>
          <div className="small">{m.plan[m.current_step].title}</div>
          <div className="small muted">{m.plan[m.current_step].evidence || "It finished without evidence a check could confirm."}</div>
          {m.plan[m.current_step].result && <details className="small"><summary style={{ cursor: "pointer" }}>What it replied</summary><Markdown text={m.plan[m.current_step].result} /></details>}
          <div className="row wrap"><button className="btn primary sm" onClick={() => review(m.current_step, true)}><Check size={13} /> It is done, continue</button>
            <button className="btn sm" onClick={() => review(m.current_step, false)}><RotateCcw size={13} /> Redo this step</button></div>
        </div>
      )}

      {m.approvals.length > 0 && (
        <div className="card col" style={{ borderColor: "var(--warn)" }} role="region" aria-label="Approvals needed">
          <div className="row"><ShieldQuestion size={16} aria-hidden="true" /><h3 className="grow">Needs your approval</h3></div>
          {m.approvals.map((a) => (
            <div key={a.id} className="col" style={{ gap: 6 }}>
              <div><strong className="mono">{a.tool}</strong> <span className="muted small">{a.reason}</span></div>
              <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0, background: "var(--panel-2)", padding: 8, borderRadius: 7 }}>{a.args}</pre>
              <div className="row"><button className="btn primary sm" onClick={() => decide(a, true)}>Approve</button><button className="btn sm" onClick={() => decide(a, false)}>Deny</button></div>
            </div>
          ))}
        </div>
      )}

      {editing ? <div className="card col"><h3>Edit the plan</h3><PlanEditor m={m} onSaved={(r) => { setM(r); setEditing(false); }} /></div> : (
        <div className="card col">
          <h3>{m.status === "review" ? "The plan (nothing has run yet)" : "Steps"}</h3>
          {m.status === "planning" && <div className="muted"><Loader2 size={14} className="spin" /> Writing the plan…</div>}
          <ol className="col" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
            {m.plan.map((s, i) => (
              <li key={i} className="step-row">
                <button type="button" className="row step-head" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
                  <StepIcon s={s.status} /><span className="grow" style={{ textAlign: "left" }}><strong>{i + 1}. {s.title}</strong><br /><span className="muted small">Done when: {s.done_when}{s.after?.length ? ` · after step ${s.after.join(", ")}` : ""}</span></span>
                  <span className="small muted">{STEP_LABEL[s.status] ?? s.status}{s.attempts > 1 ? ` · attempt ${s.attempts}` : ""}</span>
                </button>
                {open === i && (
                  <div className="col small" style={{ padding: "4px 0 10px 26px", gap: 6 }}>
                    {s.evidence && <div><strong>Check:</strong> {s.evidence}</div>}
                    {s.tools.length > 0 && <div><strong>Tools used:</strong><ul style={{ margin: "4px 0", paddingLeft: 18 }}>{s.tools.map((t, n) => <li key={n}><span className={t.ok ? "ok-text" : "danger-text"}>{t.ok ? "ok" : "failed"}</span> <span className="mono">{t.name}</span> <span className="muted">{t.preview.slice(0, 160)}</span></li>)}</ul></div>}
                    {s.result && <div><strong>Reply:</strong><Markdown text={s.result} /></div>}
                    {!s.evidence && !s.tools.length && !s.result && <div className="muted">Nothing yet.</div>}
                    {s.status === "unverified" && m.status === "partial" && <div className="row wrap">
                      <button className="btn sm" onClick={() => review(i, true)}><Check size={12} /> Mark as done</button>
                      <button className="btn sm" onClick={() => review(i, false)}><RotateCcw size={12} /> Redo it</button></div>}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}

      <Workers missionId={m.id} workers={m.workers} budget={m.shared_budget} cancelled={m.status === "cancelled"}
        onChange={() => api<Mission>(`/missions/${m.id}`).then(setM).catch(toast.err)} />

      {m.report && <div className="card col"><h3>Result</h3><Markdown text={m.report} /><div className="muted small">Steps marked "Not verified" finished without evidence a check could confirm. Treat them as unconfirmed.</div></div>}

      <details className="card">
        <summary style={{ cursor: "pointer" }}><strong>Activity</strong> <span className="muted small">({m.events.length})</span></summary>
        <ul className="col small" style={{ listStyle: "none", margin: "8px 0 0", padding: 0, gap: 4 }} aria-live="polite">
          {m.events.slice(-60).map((e, i) => <li key={i}><span className="faint" title={e.at}>{relTime(e.at)}</span> · {e.text}</li>)}
        </ul>
      </details>
      {toast.node}
    </div>
  );
}

export default function Missions() {
  const { id } = useParams();
  const [items, setItems] = useState<Item[] | null>(null);
  const navigate = useNavigate();
  const project = Number(new URLSearchParams(useLocation().search).get("project")) || null;
  const load = () => api<Item[]>("/missions").then(setItems).catch(() => setItems([]));
  useEffect(() => { load(); }, []);
  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <div className="page-head"><div><h1>Missions</h1><p className="muted">Bigger jobs ROOKS plans, you approve, and it works through step by step, checking each one.</p></div></div>
      <div className={`two-pane${id ? " has-detail" : ""}`}>
        <div className="col">
          <NewMission project={project} onCreated={(mid) => { load(); navigate(`/missions/${mid}`); }} />
          {items && items.length > 0 && (
            <section className="col" style={{ gap: 6 }} aria-labelledby="your-missions">
            <h2 id="your-missions" className="section-label">Your missions</h2>
            <div className="card flush" role="list" aria-label="Missions">
              {items.map((it) => (
                <Link key={it.id} role="listitem" to={`/missions/${it.id}`} className={`list-item${String(it.id) === id ? " active" : ""}`}>
                  <span className="grow"><span className="truncate" style={{ display: "block" }}>{it.goal}</span>
                    <span className="small muted">{LABEL[it.status] ?? it.status} · {it.done}/{it.steps} steps · {relTime(it.updated_at)}</span></span>
                </Link>
              ))}
            </div>
            </section>
          )}
          {items !== null && <Templates project={project} open={items.length === 0} onCreated={(mid) => { load(); navigate(`/missions/${mid}`); }} />}
        </div>
        <div>
          {id ? <Detail key={id} id={+id} onChanged={load} /> : (
            <div className="card"><Empty icon={<Flag size={24} />} title="Give ROOKS a bigger job" hint="It plans the steps and says how each one will be checked. You approve the plan; then it works through it, asks before anything you set to need approval, and reports what was done, what failed, and what it could not verify.">
              {items?.some((i) => i.status === "partial" || i.status === "failed") && <div className="small muted">Pick a mission on the left to see its result.</div>}
            </Empty></div>
          )}
        </div>
      </div>
    </div>
  );
}

