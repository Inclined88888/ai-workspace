// Background jobs board (feat/v02-automations): several jobs at once, with Running / Needs you / Done columns.
import { Bell, Check, CircleStop, Layers, Loader2, Pause, Play, RotateCcw, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { LiveMarkdown } from "./LiveBlocks";
import { jobControls, jobLabel } from "./live";
import "./v02.css";

export type Approval = { id: number; tool: string; args: string; reason: string; status: string };
export type Job = {
  id: number; kind: string; title: string; prompt: string; status: string; needs: string; detail: string; result: string;
  conversation_id: number | null; automation_id: number | null; column: "running" | "you" | "done"; approvals: Approval[];
  created_at: string; updated_at: string; finished_at: string | null;
};
export type Board = { running: Job[]; you: Job[]; done: Job[]; max_parallel: number; pro: boolean; tier: string };

const KINDS = [
  { v: "chat", label: "Assistant task" },
  { v: "browser", label: "On a website" },
  { v: "computer", label: "On this computer" },
  { v: "files", label: "With my files" },
];
const KIND_NAME: Record<string, string> = { chat: "Task", browser: "Website", computer: "Computer", files: "Files", playbook: "Show me how", automation: "Automation" };
const VERB: Record<string, { label: string; icon: typeof Play }> = {
  pause: { label: "Pause", icon: Pause }, resume: { label: "Resume", icon: Play }, stop: { label: "Stop", icon: CircleStop },
  start: { label: "Yes, run it", icon: Check }, deny: { label: "No", icon: X }, retry: { label: "Run again", icon: RotateCcw },
};

function argsPreview(raw: string): string {
  try {
    const a = JSON.parse(raw) as Record<string, unknown>;
    return Object.entries(a).map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ").slice(0, 220);
  } catch { return raw.slice(0, 220); }
}

export default function JobsPage() {
  const [board, setBoard] = useState<Board | null>(null);
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState("chat");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [perm, setPerm] = useState(typeof Notification !== "undefined" ? Notification.permission : "denied");
  const toast = useToast();

  const load = () => api<Board>("/jobs").then(setBoard).catch(toast.err);
  useEffect(() => {
    void load();
    const t = setInterval(() => { if (!document.hidden) void load(); }, 2500);
    return () => clearInterval(t);
  }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/jobs", { body: { kind, prompt } }); setPrompt(""); toast.ok("Added to the board"); void load(); }
    catch (err) { toast.err(err); }
  };
  const act = async (j: Job, verb: string) => {
    setBusy(`${j.id}:${verb}`);
    try { await api(`/jobs/${j.id}/${verb}`, { method: "POST" }); void load(); } catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const decide = async (a: Approval, approve: boolean) => {
    setBusy(`a${a.id}`);
    try { await api(`/approvals/${a.id}`, { body: { approve } }); toast.ok(approve ? "Done. The job carries on." : "Skipped. The job carries on without it."); void load(); }
    catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const clear = async () => { try { await api("/jobs/done", { method: "DELETE" }); void load(); } catch (e) { toast.err(e); } };
  const askPerm = async () => { if (typeof Notification !== "undefined") setPerm(await Notification.requestPermission()); };

  const card = (j: Job) => (
    <article key={j.id} className={`job-card job-${j.status}`}>
      <div className="row job-top">
        <span className="job-kind">{KIND_NAME[j.kind] ?? j.kind}</span>
        <span className={`job-state ${j.status}`}>{j.status === "running" && <Loader2 size={11} className="spin" />} {jobLabel(j.status, j.needs)}</span>
        <span className="grow" />
        <span className="small muted">{relTime(j.updated_at)}</span>
      </div>
      <button className="job-title" onClick={() => setOpen(open === j.id ? null : j.id)} aria-expanded={open === j.id}>{j.title}</button>
      <div className="small muted">{j.detail}</div>
      {j.approvals.map((a) => (
        <div key={a.id} className="job-approval">
          <div className="small"><strong>{a.tool.replace(/_/g, " ")}</strong> <span className="muted">{argsPreview(a.args)}</span></div>
          <div className="row">
            <button className="btn primary sm" disabled={busy === `a${a.id}`} onClick={() => void decide(a, true)}><Check size={13} /> Approve</button>
            <button className="btn ghost sm" disabled={busy === `a${a.id}`} onClick={() => void decide(a, false)}><X size={13} /> Skip</button>
          </div>
        </div>
      ))}
      <div className="row job-actions">
        {jobControls(j.status, j.needs).map((v) => {
          const V = VERB[v];
          return <button key={v} className={`btn sm ${v === "start" ? "primary" : "ghost"}`} disabled={busy === `${j.id}:${v}`} onClick={() => void act(j, v)}><V.icon size={13} /> {V.label}</button>;
        })}
        {j.conversation_id && <Link className="btn ghost sm" to={`/assistant/${j.conversation_id}`}>Open chat</Link>}
      </div>
      {open === j.id && (
        <div className="job-more">
          <div className="small muted">What it was asked</div>
          <div className="small job-prompt">{j.prompt.length > 600 ? j.prompt.slice(0, 600) + "…" : j.prompt}</div>
          {j.result && <><div className="small muted">Result</div><LiveMarkdown text={j.result} messageId={`job${j.id}`} /></>}
        </div>
      )}
    </article>
  );

  const cols: { key: keyof Pick<Board, "running" | "you" | "done">; title: string; hint: string }[] = [
    { key: "running", title: "Running", hint: "Working or in line" },
    { key: "you", title: "Needs you", hint: "Waiting for your OK" },
    { key: "done", title: "Done", hint: "Finished, stopped or failed" },
  ];

  return (
    <div className="page jobs-page">
      {toast.node}
      <div className="page-head">
        <div><h1>Jobs</h1><p className="muted">Hand off longer work and keep going. Jobs run in the background and ask you before anything is sent, posted, paid, deleted or changed outside the app.</p></div>
        {perm !== "granted" && typeof Notification !== "undefined" && <button className="btn" onClick={() => void askPerm()}><Bell size={15} /> Notify me</button>}
      </div>

      <form className="card job-new" onSubmit={add}>
        <textarea className="input" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="What should I work on? e.g. Compare the three quotes in my Files and draft a reply to the cheapest" aria-label="New job" />
        <div className="row wrap">
          <div className="seg" role="radiogroup" aria-label="Where it works">
            {KINDS.map((k) => <button type="button" key={k.v} role="radio" aria-checked={kind === k.v} className={`seg-btn ${kind === k.v ? "on" : ""}`} onClick={() => setKind(k.v)}>{k.label}</button>)}
          </div>
          <span className="grow" />
          <button className="btn primary" disabled={!prompt.trim()}><Play size={14} /> Start job</button>
        </div>
        {board && (
          <div className="small muted">
            {board.pro ? `Runs up to ${board.max_parallel} jobs at once.` : "The Free plan runs one job at a time. Pro runs several at once."} Computer jobs take turns, because there is one mouse and keyboard.
          </div>
        )}
      </form>

      {!board ? <div className="row muted"><Loader2 size={15} className="spin" /> Loading the board…</div> : (
        <div className="job-board">
          {cols.map((c) => (
            <section key={c.key} className={`job-col col-${c.key}`} aria-label={c.title}>
              <header className="job-col-head">
                <h2>{c.title}</h2><span className="job-count">{board[c.key].length}</span>
                <span className="grow" />
                {c.key === "done" && board.done.length > 0 && <button className="btn ghost sm icon" title="Clear finished jobs" aria-label="Clear finished jobs" onClick={() => void clear()}><Trash2 size={13} /></button>}
              </header>
              <div className="small muted job-col-hint">{c.hint}</div>
              {board[c.key].length === 0
                ? <Empty icon={<Layers size={20} />} title={c.key === "you" ? "Nothing needs you" : c.key === "running" ? "Nothing running" : "Nothing finished yet"} />
                : board[c.key].map(card)}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
