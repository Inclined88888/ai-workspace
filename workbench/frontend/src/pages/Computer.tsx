import { CircleStop, FolderOpen, Loader2, Monitor, OctagonX, Play, ShieldAlert, ShieldCheck, Trash2, XCircle } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Tracer } from "../Tracer";
import { CursorOverlay, DoneTick, LiveShot, parsePointer, Pointer } from "../motion";
import { Empty, Markdown, ModelSelect, useModels, useToast } from "../util";

type JobSummary = { id: string; goal: string; provider: string; model: string; status: string; source: string; created_at: number; dry_run: boolean };
type Step = {
  step: number; app: string; title: string; thought: string; action: string; args: Record<string, unknown>; result: string;
  status: string; approval?: string | null; output?: string; elapsed_ms?: number;
};
type Approval = { id: string; category: string; action: string; summary: string; detail: string; answer: boolean | null; step?: number; timed_out?: boolean };
type Result = {
  final: string | null; success: boolean | null; done: boolean; steps: number; dry_run: boolean; stopped: string; errors: string[];
  denied: string[]; files: string[]; commands: { command: string; exit_code: number | null; output?: string }[]; duration_s: number; model: string;
};
type Job = JobSummary & {
  steps: Step[]; error: string | null; has_screenshot: boolean; pending_approvals?: Approval[]; approvals: Approval[];
  result: Result | null; live: { step: number; output: string } | null;
};
type Perm = { ok: boolean; how?: string };
type DeskStatus = {
  backend: string | null; ok: boolean; error?: string; platform: string; hotkey: string; running: string | null;
  permissions?: Record<string, Perm>; accessibility?: [boolean, string];
  policy: { folders: string[]; allow_apps: string[]; deny_apps: string[] };
};

const CATEGORY: Record<string, string> = {
  shell: "Run a command", danger: "A risky command", file_write: "Write a file outside your folders", file_read: "Read a file outside your folders",
  file_delete: "Delete a file", message: "Send a message or email", purchase: "Pay or place an order", login: "Sign in or enter a password",
  app: "Use an app that is not on your list", mcp: "Use another tool (MCP)", system: "A system shortcut",
};
const EXAMPLES = [
  "Open the text editor, write a short packing list for a weekend trip and save it as packing.txt in Documents.",
  "In the terminal, show how much disk space is free and list the 5 biggest files in Downloads.",
  "Open my Documents folder in the file manager and tell me what is in it.",
];
/** Categories a person may let one task do without stopping to ask. Logins and dangerous commands always ask. */
const SKIP_ASKING: [string, string][] = [["shell", "Run commands"], ["file_write", "Save files outside my folders"], ["message", "Send emails"]];
const active = (s: string) => s === "queued" || s === "running" || s === "waiting";

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : new Date(ts * 1000).toLocaleDateString();
}

function describe(s: Step) {
  const a = s.args || {};
  const bits = Object.entries(a).filter(([k]) => k !== "thought").map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return `${s.action}${bits.length ? ` · ${bits.join(" · ")}` : ""}`;
}

export default function Computer() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { models } = useModels();
  const [desk, setDesk] = useState<DeskStatus | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [shotKey, setShotKey] = useState(0);
  const [openStep, setOpenStep] = useState<number | null>(null);
  const [shotSize, setShotSize] = useState<[number, number]>([0, 0]);
  const [form, setForm] = useState({ goal: "", model: "", max_steps: 30, dry_run: false, approve: [] as string[] });
  const [lists, setLists] = useState<{ folders: string; allow_apps: string } | null>(null);
  const toast = useToast();

  const loadJobs = () => api<JobSummary[]>("/desk/jobs").then(setJobs).catch(toast.err);
  const loadDesk = () => api<DeskStatus>("/desk/status").then((d) => {
    setDesk(d);
    setLists((l) => l ?? { folders: d.policy.folders.join("\n"), allow_apps: d.policy.allow_apps.join(", ") });
  }).catch(toast.err);
  useEffect(() => { loadDesk(); loadJobs(); }, []);

  useEffect(() => {
    if (!id) return setJob(null);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const j = await api<Job>(`/desk/jobs/${id}`);
        if (stop) return;
        setJob(j);
        setShotKey((k) => k + 1);
        if (active(j.status)) timer = setTimeout(poll, 1000);
        else loadJobs();
      } catch (e) {
        toast.err(e);
      }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [id]);

  const answer = async (a: Approval, approve: boolean) => {
    try {
      await api(`/desk/jobs/${job!.id}/approvals/${a.id}`, { method: "POST", body: { approve } });
    } catch (e) {
      toast.err(e);
    }
  };
  const start = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const j = await api<Job>("/desk/jobs", { body: { goal: form.goal, provider: "workbench", model: form.model || "default", max_steps: form.max_steps, dry_run: form.dry_run, approve: form.approve } });
      loadJobs();
      navigate(`/computer/${j.id}`);
    } catch (err) {
      toast.err(err);
    }
  };
  const stopAll = async () => {
    try {
      await api("/desk/stop", { method: "POST" });
      toast.ok("Stopped. Nothing else will be clicked or typed.");
    } catch (e) {
      toast.err(e);
    }
  };
  const remove = async (jid: string) => {
    await api(`/desk/jobs/${jid}`, { method: "DELETE" });
    if (job?.id === jid) navigate("/computer");
    loadJobs();
  };
  const saveLists = async () => {
    if (!lists) return;
    try {
      await api("/desk/settings", { method: "PUT", body: {
        folders: lists.folders.split("\n").map((f) => f.trim()).filter(Boolean),
        allow_apps: lists.allow_apps.split(",").map((f) => f.trim()).filter(Boolean),
      } });
      toast.ok("Saved");
      loadDesk();
    } catch (e) {
      toast.err(e);
    }
  };

  const missing = Object.entries(desk?.permissions || {}).filter(([, p]) => !p.ok);
  const anyRunning = jobs.some((j) => active(j.status)) || (job && active(job.status));

  return (
    <div className="page wide" style={{ maxWidth: 1400 }}>
      <div className="page-head">
        <div>
          <h1>Computer</h1>
          <p className="muted">Describe a task. Rooks Desk uses your own apps, terminal and files to do it, and asks you first before anything that matters.</p>
        </div>
        <button className="btn danger" onClick={stopAll} title={`Or press ${desk?.hotkey || "Ctrl+Alt+Esc"} anywhere`} disabled={!anyRunning}>
          <OctagonX size={16} /> Stop everything
        </button>
      </div>

      {desk && !desk.ok && <div className="alert err" style={{ marginBottom: 16 }}>Rooks Desk cannot see this screen: {desk.error}</div>}
      {missing.length > 0 && (
        <div className="alert col" style={{ marginBottom: 16, gap: 4 }}>
          <strong className="row"><ShieldAlert size={15} /> Rooks Desk needs permission</strong>
          {missing.map(([k, p]) => <div key={k} className="small">{k}: {p.how}</div>)}
          <div className="small muted">Then run <span className="mono">rooks-desk setup</span> once, or restart the app.</div>
        </div>
      )}

      <form className="card col" onSubmit={start} style={{ marginBottom: 16 }}>
        <textarea className="input" rows={3} value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })}
          placeholder="e.g. Open the text editor, write my packing list and save it in Documents" />
        <div className="row wrap">
          {EXAMPLES.map((ex) => <button type="button" key={ex} className="btn sm ghost" onClick={() => setForm({ ...form, goal: ex })}>{ex.slice(0, 52)}…</button>)}
        </div>
        <div className="row wrap">
          <div style={{ width: 260 }}><ModelSelect value={form.model} onChange={(model) => setForm({ ...form, model })} models={models} /></div>
          <label className="row small muted">Max steps
            <input className="input" type="number" min={1} max={100} style={{ width: 80 }} value={form.max_steps} onChange={(e) => setForm({ ...form, max_steps: Number(e.target.value) })} />
          </label>
          <label className="row small"><input type="checkbox" checked={form.dry_run} onChange={(e) => setForm({ ...form, dry_run: e.target.checked })} /> Dry run (look and plan, change nothing)</label>
          <button className="btn primary" disabled={form.goal.trim().length < 3}><Play size={15} /> Start</button>
        </div>
        <div className="row wrap small">
          <span className="muted">Don't ask me this time before it:</span>
          {SKIP_ASKING.map(([cat, label]) => (
            <label key={cat} className="row small"><input type="checkbox" checked={form.approve.includes(cat)}
              onChange={(e) => setForm({ ...form, approve: e.target.checked ? [...form.approve, cat] : form.approve.filter((c) => c !== cat) })} /> {label}</label>
          ))}
          <span className="faint">Sign-ins, passwords and risky commands still ask.</span>
        </div>
        <div className="muted small">
          While it works you see a red frame around the screen. Move the mouse to the top-left corner, press <span className="mono">{desk?.hotkey || "Ctrl+Alt+Esc"}</span> or
          Stop to halt it at once. Models that can see screenshots work best (free: Gemini Flash, Groq Llama 4 / Qwen 3.8); others read the window's accessibility text.
        </div>
      </form>

      <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0,1fr)", gap: 16 }} className="research-layout">
        <div className="col gap-lg" style={{ alignSelf: "start" }}>
          <div className="card flush">
            {jobs.length === 0 && <Empty icon={<Monitor size={22} />} title="No tasks yet" />}
            {jobs.map((j) => (
              <div key={j.id} className={`list-item has-tracer ${job?.id === j.id ? "active" : ""}`} onClick={() => navigate(`/computer/${j.id}`)}>
                <Tracer active={active(j.status)} />
                <div className="grow">
                  <div className="truncate">{j.goal}</div>
                  <div className="row small faint">{active(j.status) && <Loader2 size={11} className="spin" />}{j.status}{j.dry_run ? " · dry run" : ""} · {j.source} · {ago(j.created_at)}</div>
                </div>
                <button className="btn ghost sm icon" aria-label="Delete" onClick={(e) => { e.stopPropagation(); remove(j.id); }}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
          {lists && (
            <div className="card col">
              <h3 className="row"><FolderOpen size={15} /> What it may touch</h3>
              <label className="col small">Folders it can read and write without asking (one per line)
                <textarea className="input mono small" rows={4} value={lists.folders} onChange={(e) => setLists({ ...lists, folders: e.target.value })} />
              </label>
              <label className="col small">Apps it can use without asking (comma separated; empty = any except password managers)
                <input className="input" value={lists.allow_apps} onChange={(e) => setLists({ ...lists, allow_apps: e.target.value })} placeholder="Text Editor, Terminal, AutoCAD" />
              </label>
              <button className="btn sm" onClick={saveLists}>Save</button>
              <div className="faint small">Always asks: commands, files elsewhere, emails and messages, purchases, logins. Never: password fields.</div>
            </div>
          )}
        </div>
        <div>
          {!job && (
            <div className="card col">
              <Empty icon={<Monitor size={26} />} title="Start a task or pick one" hint="Chat and Rooks Code can use your computer too; their tasks show up here." />
              {desk?.ok && <img src={`/api/desk/screen?k=${shotKey}`} alt="Your screen now" style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />}
            </div>
          )}
          {job && (
            <div className="col gap-lg">
              <div className="card col has-tracer">
                <Tracer active={active(job.status)} label="Using your computer" />
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <h2 className="grow">{job.goal}</h2>
                  {active(job.status) && <button className="btn sm" onClick={() => api(`/desk/jobs/${job.id}/cancel`, { method: "POST" })}><CircleStop size={14} /> Stop</button>}
                </div>
                <div className="row small muted wrap">
                  <span className={`badge ${job.status === "done" ? (job.result?.success ? "ok" : "warn") : job.status === "error" ? "danger" : job.status === "waiting" ? "warn" : "accent"}`}>{job.status === "waiting" ? "waiting for you" : job.status}</span>
                  {job.dry_run && <span className="badge">dry run</span>}
                  <span className="mono">{job.provider}{job.result?.model ? `/${job.result.model}` : job.model ? `/${job.model}` : ""}</span>
                  {job.result && <span>{job.result.steps} steps · {job.result.duration_s}s</span>}
                </div>
                {job.error && <div className="alert err">{job.error}</div>}
                {(job.pending_approvals || []).map((a) => (
                  <div key={a.id} className="alert col spring-in" style={{ gap: 6, borderColor: "var(--accent)" }}>
                    <strong className="row"><ShieldAlert size={15} /> Allow this? {CATEGORY[a.category] || a.category}</strong>
                    <div>{a.summary}</div>
                    {a.detail && <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{a.detail}</pre>}
                    <div className="row">
                      <button className="btn sm primary" onClick={() => answer(a, true)}><ShieldCheck size={14} /> Allow once</button>
                      <button className="btn sm" onClick={() => answer(a, false)}>Deny</button>
                    </div>
                  </div>
                ))}
                {job.result && (
                  <>
                    <div className="row">{job.result.success ? <DoneTick size={18} label="Finished" /> : <XCircle size={16} className="prio-2" />}
                      <strong>{job.result.stopped ? `Stopped: ${job.result.stopped}` : job.result.success ? "Rooks Desk reports success" : job.result.done ? "Finished without success" : "Stopped at the step limit"}</strong></div>
                    <div className="muted small">This is the agent's own claim. Check the last screen, files and steps below.</div>
                    {job.result.final ? <Markdown text={job.result.final} /> : <div className="muted">No final answer.</div>}
                    {job.result.files.length > 0 && <div className="small"><span className="faint">Files: </span><span className="mono">{job.result.files.join(", ")}</span></div>}
                    {job.result.denied.length > 0 && <div className="small"><span className="faint">You said no to: </span>{job.result.denied.join("; ")}</div>}
                  </>
                )}
              </div>
              <div className="grid two">
                <div className="card col">
                  <h3>{active(job.status) ? "Live screen" : "Last screen"}</h3>
                  {job.has_screenshot ? (
                    <LiveShot src={`/api/desk/jobs/${job.id}/screenshot?k=${shotKey}`} alt="Screen" scanning={job.status === "running"} onSize={(w, h) => setShotSize((o) => (o[0] === w && o[1] === h ? o : [w, h]))}>
                      {active(job.status) && <CursorOverlay points={job.steps.map((s) => parsePointer(s.result, s.action, shotSize[0], shotSize[1])).filter((p): p is Pointer => !!p)} />}
                    </LiveShot>)
                    : <div className="muted small">{active(job.status) ? "Looking at the screen…" : "No screenshot."}</div>}
                  {job.live?.output && active(job.status) && <pre className="mono small" style={{ maxHeight: 220, overflow: "auto", background: "var(--code-bg)", padding: 8, borderRadius: 6 }}>{job.live.output}</pre>}
                </div>
                <div className="card col step-list timeline">
                  <h3>Thinking and actions</h3>
                  {job.steps.length === 0 && <div className="muted small">{active(job.status) ? <span className="row"><Loader2 size={13} className="spin" /> Looking at the screen</span> : "No steps recorded."}</div>}
                  {job.steps.map((s) => (
                    <div key={s.step} className={`col${s.status === "denied" || s.status === "error" ? " failed" : ""}`} style={{ gap: 4, borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
                      <div className="row small"><span className="badge">{s.step}</span><strong className="grow mono" style={{ wordBreak: "break-word" }}>{describe(s)}</strong>
                        {s.status && s.status !== "ok" && <span className={`badge ${s.status === "denied" || s.status === "error" ? "danger" : "warn"}`}>{s.status}</span>}</div>
                      {s.thought && <div className="small"><span className="faint">Thinking: </span>{s.thought}</div>}
                      {s.result && <div className="muted small" style={{ wordBreak: "break-word" }}>{s.result}</div>}
                      {s.output && <pre className="mono small" style={{ maxHeight: 160, overflow: "auto", margin: 0, whiteSpace: "pre-wrap" }}>{s.output}</pre>}
                      <div className="faint small truncate">{s.app}{s.title && s.title !== s.app ? ` · ${s.title}` : ""}</div>
                      {openStep === s.step
                        ? <img src={`/api/desk/jobs/${job.id}/screenshot/${s.step}`} alt={`Step ${s.step}`} onClick={() => setOpenStep(null)} style={{ width: "100%", borderRadius: 6, border: "1px solid var(--border)", cursor: "zoom-out" }} />
                        : <img src={`/api/desk/jobs/${job.id}/screenshot/${s.step}`} alt={`Step ${s.step}`} loading="lazy" onClick={() => setOpenStep(s.step)} onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} style={{ width: 160, borderRadius: 6, border: "1px solid var(--border)", cursor: "zoom-in" }} />}
                    </div>
                  ))}
                  {active(job.status) && job.steps.length > 0 && <div className="muted small row"><Loader2 size={13} className="spin" /> {job.status === "waiting" ? "Waiting for your answer" : `Working on step ${job.steps.length + 1}…`}</div>}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
