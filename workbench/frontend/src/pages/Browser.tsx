import { CheckCircle2, CircleStop, Globe, Loader2, Play, Trash2, XCircle } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, ModelSelect, useModels, useToast } from "../util";

type JobSummary = { id: string; goal: string; provider: string; model: string; status: string; source: string; created_at: number };
type Step = { step: number; url: string; title: string; evaluation: string; next_goal: string; actions: Record<string, unknown>[] };
type Job = JobSummary & {
  steps: Step[];
  error: string | null;
  has_screenshot: boolean;
  allowed_domains: string[];
  result: { final: string | null; success: boolean | null; done: boolean; steps: number; urls: string[]; duration_s: number; model: string } | null;
};

const EXAMPLES = [
  "Go to weworkremotely.com and find three remote software engineer roles. Give title, company and link for each.",
  "On pcpartpicker.com, find a compatible CPU, motherboard and 32GB DDR5 RAM for a mid range AM5 gaming build. List prices and links.",
  "Find the current top 3 stories on news.ycombinator.com with their points.",
];

const active = (s: string) => s === "queued" || s === "running";

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : new Date(ts * 1000).toLocaleDateString();
}

export default function Browser() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { models } = useModels();
  const [usage, setUsage] = useState<{ used: number; limit: number } | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [shotKey, setShotKey] = useState(0);
  const [form, setForm] = useState({ goal: "", provider: "", model: "", max_steps: 25, domains: "" });
  const toast = useToast();

  const loadJobs = () => api<JobSummary[]>("/browser/jobs").then(setJobs).catch(toast.err);
  useEffect(() => {
    api<{ usage: { used: number; limit: number } }>("/browser/providers").then((r) => setUsage(r.usage)).catch(toast.err);
    loadJobs();
  }, []);

  useEffect(() => {
    if (!id) return setJob(null);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const j = await api<Job>(`/browser/jobs/${id}`);
        if (stop) return;
        setJob(j);
        setShotKey((k) => k + 1);
        if (active(j.status)) timer = setTimeout(poll, 2000);
        else loadJobs();
      } catch (e) {
        toast.err(e);
      }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [id]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const j = await api<Job>("/browser/jobs", {
        body: {
          goal: form.goal, provider: "workbench", model: form.model || "default", max_steps: form.max_steps,
          allowed_domains: form.domains.split(",").map((d) => d.trim()).filter(Boolean),
        },
      });
      loadJobs();
      navigate(`/browser/${j.id}`);
    } catch (err) {
      toast.err(err);
    }
  };
  const cancel = async () => { await api(`/browser/jobs/${job!.id}/cancel`, { method: "POST" }); };
  const remove = async (jid: string) => {
    await api(`/browser/jobs/${jid}`, { method: "DELETE" });
    if (job?.id === jid) navigate("/browser");
    loadJobs();
  };

  return (
    <div className="page wide" style={{ maxWidth: 1400 }}>
      <div className="page-head">
        <div><h1>Browser agent</h1><p className="muted">Describe a goal. The agent opens a real browser, clicks, types and reads until it is done.</p></div>
      </div>

      <form className="card col" onSubmit={start} style={{ marginBottom: 16 }}>
        <textarea className="input" rows={3} value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} placeholder="e.g. Find three remote software engineer roles on weworkremotely.com" />
        <div className="row wrap">
          {EXAMPLES.map((ex) => <button type="button" key={ex} className="btn sm ghost" onClick={() => setForm({ ...form, goal: ex })}>{ex.slice(0, 48)}…</button>)}
        </div>
        <div className="row wrap">
          <div style={{ width: 260 }}><ModelSelect value={form.model} onChange={(model) => setForm({ ...form, model })} models={models} /></div>
          <label className="row small muted">Max steps
            <input className="input" type="number" min={1} max={50} style={{ width: 80 }} value={form.max_steps} onChange={(e) => setForm({ ...form, max_steps: Number(e.target.value) })} />
          </label>
          <input className="input grow" style={{ minWidth: 200 }} value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} placeholder="Only these sites (optional): *.pcpartpicker.com, …" />
          <button className="btn primary" disabled={form.goal.trim().length < 3}><Play size={15} /> Run</button>
        </div>
        <div className="muted small">Runs on any model connected in Settings, through Workbench, so its spend shows in Usage. Vision models read screenshots; small local models are unreliable at browsing. Max steps caps the cost of a run.</div>
        {usage && <div className="muted small">This month: {usage.used} of {usage.limit || "unlimited"} browser steps used (all callers, including Chat and Paperclip).</div>}
      </form>

      <div style={{ display: "grid", gridTemplateColumns: "300px minmax(0,1fr)", gap: 16 }} className="research-layout">
        <div className="card flush" style={{ alignSelf: "start" }}>
          {jobs.length === 0 && <Empty icon={<Globe size={22} />} title="No runs yet" />}
          {jobs.map((j) => (
            <div key={j.id} className={`list-item ${job?.id === j.id ? "active" : ""}`} onClick={() => navigate(`/browser/${j.id}`)}>
              <div className="grow">
                <div className="truncate">{j.goal}</div>
                <div className="row small faint">{active(j.status) && <Loader2 size={11} className="spin" />}{j.status} · {j.source} · {ago(j.created_at)}</div>
              </div>
              <button className="btn ghost sm icon" onClick={(e) => { e.stopPropagation(); remove(j.id); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
        <div>
          {!job && <div className="card"><Empty icon={<Globe size={26} />} title="Pick a run or start one" hint="Agents in Chat and Paperclip can use this browser too." /></div>}
          {job && (
            <div className="col gap-lg">
              <div className="card col">
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <h2 className="grow">{job.goal}</h2>
                  {active(job.status) && <button className="btn sm" onClick={cancel}><CircleStop size={14} /> Stop</button>}
                </div>
                <div className="row small muted wrap">
                  <span className={`badge ${job.status === "done" ? (job.result?.success ? "ok" : "warn") : job.status === "error" ? "danger" : "accent"}`}>{job.status}</span>
                  <span className="mono">{job.provider}{job.result?.model ? `/${job.result.model}` : job.model ? `/${job.model}` : ""}</span>
                  {job.result && <span>{job.result.steps} steps · {job.result.duration_s}s</span>}
                  {job.allowed_domains.length > 0 && <span>only {job.allowed_domains.join(", ")}</span>}
                </div>
                {job.error && <div className="alert err">{job.error}</div>}
                {job.result && (
                  <>
                    <div className="row">{job.result.success ? <CheckCircle2 size={16} className="prio-1" /> : <XCircle size={16} className="prio-2" />}<strong>{job.result.success ? "Agent reports success" : job.result.done ? "Agent finished but did not report success" : "Stopped at the step limit"}</strong></div>
                    <div className="muted small">This is the agent's own claim. Check it against the last screen and the steps; small models can invent answers.</div>
                    {job.result.final ? <Markdown text={job.result.final} /> : <div className="muted">No final answer.</div>}
                  </>
                )}
              </div>
              <div className="grid two">
                <div className="card col">
                  <h3>{active(job.status) ? "Live view" : "Last screen"}</h3>
                  {job.has_screenshot ? <img key={shotKey} src={`/api/browser/jobs/${job.id}/screenshot?k=${shotKey}`} alt="Browser screenshot" style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
                    : <div className="muted small">{active(job.status) ? "Starting browser…" : "No screenshot."}</div>}
                </div>
                <div className="card col">
                  <h3>Steps</h3>
                  {job.steps.length === 0 && <div className="muted small">{active(job.status) ? <span className="row"><Loader2 size={13} className="spin" /> Waiting for the first step</span> : "No steps recorded."}</div>}
                  {job.steps.map((s) => (
                    <div key={s.step} className="col" style={{ gap: 2, borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
                      <div className="row small"><span className="badge">{s.step}</span><strong className="grow">{s.next_goal || "…"}</strong></div>
                      {s.evaluation && <div className="muted small">{s.evaluation}</div>}
                      <div className="faint small mono truncate">{s.actions.map((a) => Object.keys(a)[0]).join(" → ")} · {s.url}</div>
                    </div>
                  ))}
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
