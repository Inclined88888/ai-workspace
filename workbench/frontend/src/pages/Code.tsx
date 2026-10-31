import { CheckCircle2, CircleStop, Code2, FolderOpen, Loader2, Play, RefreshCw, Send, Terminal, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, useToast } from "../util";

export type CliAgent = {
  id: "claude" | "codex"; name: string; plan: string; install: string; login: string; login_hint: string; docs: string; terms: string;
  installed: boolean; version: string; signed_in: boolean; auth: "subscription" | "api_key" | "none"; ready: boolean; detail: string;
};
type RunSummary = { id: string; agent: string; agent_name: string; task: string; folder: string; mode: string; status: string; created_at: number };
type Ev = { kind: string; t: number; text?: string; tool?: string; label?: string; input?: string; id?: string; error?: boolean; sub?: boolean; model?: string };
type Pending = { id: string; tool: string; rule: string; label: string; input: string };
type Run = RunSummary & {
  events: Ev[]; event_count: number; pending: Pending[]; result: string; error: string; cost_usd: number | null; tokens_in: number;
  tokens_out: number; model: string; diff: string; files: string[]; session: boolean;
};

const MODES: { id: string; label: string; hint: string }[] = [
  { id: "ask", label: "Ask first", hint: "Reads freely. Asks you before it edits a file or runs a command." },
  { id: "edits", label: "Edit files", hint: "Edits files in this folder on its own. Still asks before commands." },
  { id: "plan", label: "Plan only", hint: "Reads and proposes. Changes nothing." },
];
const active = (s: string) => s === "queued" || s === "running";
const STATUS: Record<string, string> = { queued: "Starting", running: "Working", needs_approval: "Needs you", done: "Done", failed: "Didn't finish", stopped: "Stopped" };

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : new Date(ts * 1000).toLocaleDateString();
}

/** Install / sign-in guidance for one CLI. Used here and in onboarding. */
export function AgentSetup({ agent, onRecheck, checking }: { agent: CliAgent; onRecheck: () => void; checking?: boolean }) {
  return (
    <div className="cli-setup col">
      <div className="row">
        <span className={`dot ${agent.ready ? "ok" : agent.installed ? "warn" : ""}`} aria-hidden />
        <strong className="grow">{agent.ready ? (agent.auth === "subscription" ? "Ready, signed in with your plan" : "Ready, signed in with an API key")
          : agent.installed ? "Installed, not signed in" : "Not installed"}</strong>
        <button type="button" className="btn sm ghost" onClick={onRecheck} disabled={checking}>
          {checking ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />} Check again
        </button>
      </div>
      {!agent.ready && (
        <ol className="cli-steps">
          {!agent.installed && <li>Install it: <code>{agent.install}</code></li>}
          <li>Sign in: run <code>{agent.login}</code> in a terminal and choose your {agent.id === "claude" ? "Claude" : "ChatGPT"} account.</li>
          <li>Come back and press Check again.</li>
        </ol>
      )}
      {!agent.ready && <a className="small" href={agent.docs} target="_blank" rel="noreferrer">Official {agent.name} setup guide</a>}
      {agent.ready && agent.auth === "api_key" && <div className="small muted">{agent.detail}</div>}
      {agent.ready && agent.version && <div className="small faint">{agent.name} {agent.version.replace(/\(.*\)/, "").trim()}</div>}
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff">{diff.split("\n").map((l, i) => {
      const cls = l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff ") || l.startsWith("index ") ? "meta"
        : l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "";
      return <span key={i} className={cls}>{l}{"\n"}</span>;
    })}</pre>
  );
}

function EventRow({ e }: { e: Ev }) {
  if (e.kind === "user") return <div className="cev cev-user">{e.text}</div>;
  if (e.kind === "text") return <div className={`cev cev-text ${e.sub ? "sub" : ""}`}><Markdown text={e.text || ""} /></div>;
  if (e.kind === "thinking") return <details className="cev cev-think"><summary>Thinking</summary><div className="small muted pre">{e.text}</div></details>;
  if (e.kind === "tool") return (
    <details className={`cev cev-tool ${e.sub ? "sub" : ""}`}>
      <summary><Terminal size={13} /> <span className="truncate">{e.label}</span></summary>
      {e.input && <pre className="small">{e.input}</pre>}
    </details>
  );
  if (e.kind === "tool_result") return e.text ? <pre className={`cev cev-out ${e.error ? "err" : ""}`}>{e.text}</pre> : null;
  if (e.kind === "approved") return <div className="cev cev-note ok"><CheckCircle2 size={13} /> You allowed: {e.text}</div>;
  if (e.kind === "denied") return <div className="cev cev-note">{e.text}</div>;
  if (e.kind === "result") return null;
  return <div className="cev cev-note">{e.text}</div>;
}

export default function Code() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [agents, setAgents] = useState<CliAgent[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [form, setForm] = useState(() => {
    const saved = JSON.parse(localStorage.getItem("wb-code-form") || "{}");
    return { agent: saved.agent || "claude", folder: saved.folder || "", mode: saved.mode || "ask", task: "" };
  });
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const [tick, setTick] = useState(0);
  const kickUntil = useRef(0); // keep polling briefly after an action while the job restarts

  const detect = (fresh = false) => {
    setChecking(true);
    return api<{ agents: CliAgent[] }>(`/cli-agents${fresh ? "?fresh=true" : ""}`).then((r) => setAgents(r.agents)).catch(toast.err).finally(() => setChecking(false));
  };
  const loadRuns = () => api<RunSummary[]>("/cli-agents/runs").then(setRuns).catch(toast.err);
  useEffect(() => { detect(); loadRuns(); }, []);

  useEffect(() => {
    if (!id) return setRun(null);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await api<Run>(`/cli-agents/runs/${id}`);
        if (stop) return;
        setRun(r);
        if (active(r.status) || Date.now() < kickUntil.current) timer = setTimeout(poll, 1000);
        else loadRuns();
      } catch (e) { if (!stop) toast.err(e); }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [id, tick]);

  useEffect(() => { if (run && active(run.status)) bottom.current?.scrollIntoView({ block: "nearest" }); }, [run?.event_count]);

  const chosen = agents?.find((a) => a.id === form.agent);
  const start = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      localStorage.setItem("wb-code-form", JSON.stringify({ agent: form.agent, folder: form.folder, mode: form.mode }));
      const r = await api<Run>("/cli-agents/runs", { method: "POST", body: { agent: form.agent, task: form.task, folder: form.folder, mode: form.mode } });
      setForm({ ...form, task: "" });
      setRun(r);
      loadRuns();
      navigate(`/code/${r.id}`);
    } catch (err) { toast.err(err); } finally { setBusy(false); }
  };
  const act = async (path: string, body?: unknown) => {
    if (!run) return;
    try {
      await api(`/cli-agents/runs/${run.id}/${path}`, { method: "POST", body: body ?? {} });
      kickUntil.current = Date.now() + 4000;
      setRun({ ...run, status: path === "deny" ? "done" : path === "stop" ? "stopped" : "running", pending: [] });
      setTick((t) => t + 1);
    } catch (err) { toast.err(err); }
  };
  const remove = async (rid: string) => {
    try { await api(`/cli-agents/runs/${rid}`, { method: "DELETE" }); if (rid === id) navigate("/code"); loadRuns(); } catch (err) { toast.err(err); }
  };

  const ready = !!chosen?.ready;
  return (
    <div className="page wide code-page">
      <div className="page-head">
        <div>
          <h1>Coding agent</h1>
          <p className="muted">Hand a coding job to Claude Code or Codex on this computer. It runs on your own plan, in a folder you pick, and asks before it changes anything.</p>
        </div>
      </div>

      <form className="card col code-new" onSubmit={start}>
        <div className="seg" role="radiogroup" aria-label="Agent">
          {(agents || [{ id: "claude", name: "Claude Code" }, { id: "codex", name: "Codex" }] as CliAgent[]).map((a) => (
            <button type="button" role="radio" aria-checked={form.agent === a.id} key={a.id} className={form.agent === a.id ? "on" : ""}
              onClick={() => setForm({ ...form, agent: a.id })}>
              {a.name}{a.ready && <span className="dot ok" aria-label="ready" />}
            </button>
          ))}
        </div>
        {chosen && !ready && <AgentSetup agent={chosen} onRecheck={() => detect(true)} checking={checking} />}
        <textarea className="input" rows={3} value={form.task} onChange={(e) => setForm({ ...form, task: e.target.value })}
          placeholder="What should it do? e.g. Add a dark mode toggle to the settings page and run the tests" />
        <div className="row wrap">
          <label className="input-icon grow" style={{ minWidth: 240 }}>
            <FolderOpen size={15} />
            <input className="input" value={form.folder} onChange={(e) => setForm({ ...form, folder: e.target.value })} placeholder="Project folder, e.g. ~/code/my-app" aria-label="Project folder" />
          </label>
          <select className="input" value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })} aria-label="Permissions" style={{ width: "auto" }}>
            {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
          <button className="btn primary" disabled={!ready || busy || !form.task.trim() || !form.folder.trim()}>
            {busy ? <Loader2 size={15} className="spin" /> : <Play size={15} />} Start
          </button>
        </div>
        <div className="small faint">{MODES.find((m) => m.id === form.mode)?.hint} Only use folders you trust: the agent loads that project's own settings and hooks.</div>
      </form>

      <div className="code-layout">
        <aside className="col code-list">
          {runs.length > 0 && <div className="small faint list-label">Recent jobs</div>}
          {runs.map((r) => (
            <div key={r.id} className={`list-item ${r.id === id ? "active" : ""}`} onClick={() => navigate(`/code/${r.id}`)}>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="truncate">{r.task}</div>
                <div className="row small faint">{active(r.status) && <Loader2 size={11} className="spin" />}{STATUS[r.status] || r.status} · {r.agent_name} · {ago(r.created_at)}</div>
              </div>
              <button className="btn ghost sm icon" aria-label="Delete job" onClick={(e) => { e.stopPropagation(); remove(r.id); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </aside>

        <section className="col gap-lg" style={{ minWidth: 0 }}>
          {!run && <Empty icon={<Code2 size={26} />} title={runs.length ? "Pick a job or start one" : "No jobs yet"} hint="Progress, approvals and the changes it made show up here." />}
          {run && (
            <>
              <div className="card col">
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <h2 className="grow">{run.task}</h2>
                  {active(run.status) && <button className="btn sm" onClick={() => act("stop")}><CircleStop size={14} /> Stop</button>}
                </div>
                <div className="row small muted wrap">
                  <span className={`badge ${run.status === "done" ? "ok" : run.status === "failed" ? "danger" : run.status === "needs_approval" ? "warn" : "accent"}`}>{STATUS[run.status] || run.status}</span>
                  <span>{run.agent_name}{run.model ? ` · ${run.model}` : ""}</span>
                  <span className="mono truncate">{run.folder}</span>
                  {(run.tokens_in > 0 || run.tokens_out > 0) && <span>{(run.tokens_in + run.tokens_out).toLocaleString()} tokens</span>}
                </div>
                {run.error && <div className="alert err">{run.error}{/Not logged in|401|Unauthorized/.test(run.error) && <div className="small">Sign in again in a terminal with <code>{run.agent === "claude" ? "claude" : "codex login"}</code>, then retry.</div>}</div>}
              </div>

              {run.pending.length > 0 && (
                <div className="card col approval">
                  <strong>{run.agent_name} wants to</strong>
                  {run.pending.map((p) => (
                    <details key={p.id} className="pending"><summary>{p.label}</summary>{p.input && <pre className="small">{p.input}</pre>}</details>
                  ))}
                  <div className="row">
                    <button className="btn primary sm" onClick={() => act("approve")}>Allow and continue</button>
                    <button className="btn sm" onClick={() => act("deny")}>Don't allow</button>
                  </div>
                  <div className="small faint">Allowing lets it run exactly these steps for the rest of this job.</div>
                </div>
              )}

              <div className="card col events">
                {run.events.map((e, i) => <EventRow key={i} e={e} />)}
                {active(run.status) && <div className="row small muted"><Loader2 size={13} className="spin" /> Working…</div>}
                <div ref={bottom} />
              </div>

              {run.result && !active(run.status) && run.result.trim() !== [...run.events].reverse().find((e) => e.kind === "text")?.text?.trim() && (
                <div className="card col">
                  <h3>Result</h3>
                  <Markdown text={run.result} />
                </div>
              )}

              {run.files.length > 0 && (
                <div className="card col">
                  <h3>Changes <span className="faint small">{run.files.length} file{run.files.length === 1 ? "" : "s"}</span></h3>
                  {run.diff ? <DiffView diff={run.diff} /> : <div className="small muted">{run.files.join(", ")}</div>}
                </div>
              )}

              {!active(run.status) && run.session && run.status !== "needs_approval" && (
                <form className="row" onSubmit={(e) => { e.preventDefault(); if (reply.trim()) { act("message", { text: reply }); setReply(""); } }}>
                  <input className="input grow" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Ask for a follow-up in the same session" />
                  <button className="btn" disabled={!reply.trim()}><Send size={14} /> Send</button>
                </form>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
