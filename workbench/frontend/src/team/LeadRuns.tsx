// B1: the AI team lead's sub-agent runs as a tree: the goal, each read-only sub-task with its job, and the synthesis.
import { ChevronDown, ChevronRight, Sparkles } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime } from "../util";

export type SubTask = { id: string; task: string; job_id: number; status: string; conversation_id: number | null; result: string | null };
export type LeadRun = {
  id: number; goal: string; status: string; conversation_id: number | null; subtasks: SubTask[];
  synthesis: string | null; created_at: string; finished_at: string | null;
};

const LIVE = new Set(["running", "queued", "synthesizing"]);
const LABEL: Record<string, string> = {
  running: "Running", queued: "Waiting", synthesizing: "Writing it up", done: "Done", failed: "Failed", stopped: "Stopped",
};

export function statusLabel(s: string) { return LABEL[s] ?? s; }

export default function LeadRuns() {
  const [runs, setRuns] = useState<LeadRun[] | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const load = useCallback(() => api<LeadRun[]>("/team/lead?limit=20").then((r) => { setRuns(r); setErr(""); }).catch((e) => setErr(String(e?.message ?? e))), []);
  const live = !!runs?.some((r) => LIVE.has(r.status) || r.subtasks.some((s) => LIVE.has(s.status)));
  useEffect(() => { load(); }, [load]);
  useEffect(() => {  // poll only while something is still running and the page is visible
    if (!live) return;
    const t = setInterval(() => { if (document.visibilityState === "visible") load(); }, 4000);
    return () => clearInterval(t);
  }, [live, load]);

  if (err) return <div className="card pad" role="alert">Could not load AI runs: {err}</div>;
  if (!runs) return <div className="card pad faint">Loading…</div>;
  if (!runs.length) return <Empty icon={<Sparkles size={22} />} title="No AI team runs yet" hint="Ask in chat for something with several parts (for example: compare three suppliers). The team lead splits it into read-only sub-tasks, runs them in parallel and writes one answer. Each run shows here." />;
  return (
    <section className="col gap" aria-label="AI team runs" aria-live="polite">
      {runs.map((r, i) => {
        const done = r.subtasks.filter((s) => !LIVE.has(s.status)).length;
        const isOpen = open[r.id] ?? (i === 0 || LIVE.has(r.status));  // the newest and any live run start open
        return (
          <article key={r.id} className="card col gap lead-run" data-status={r.status}>
            <button className="row lead-run-head" aria-expanded={isOpen} onClick={() => setOpen({ ...open, [r.id]: !isOpen })}>
              {isOpen ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
              <strong className="grow" style={{ textAlign: "left" }}>{r.goal}</strong>
              <span className={`pill lead-${r.status}`}>{statusLabel(r.status)}</span>
              <span className="muted small">{done}/{r.subtasks.length} sub-tasks · {relTime(r.created_at)}</span>
            </button>
            {isOpen && <>
              <ol className="lead-tree" aria-label={`Sub-tasks of run ${r.id}`}>
                {r.subtasks.map((s) => (
                  <li key={s.id} className="lead-sub" data-status={s.status}>
                    <div className="row gap">
                      <span className="mono small">{s.id}</span>
                      <span className="grow">{s.task}</span>
                      <span className={`pill lead-${s.status}`}>{statusLabel(s.status)}</span>
                      {s.conversation_id ? <Link className="btn sm ghost" to={`/assistant/${s.conversation_id}`}>Open job #{s.job_id}</Link> : <span className="muted small">job #{s.job_id}</span>}
                    </div>
                    {s.result && <details><summary className="muted small">Result</summary><div className="lead-text">{s.result}</div></details>}
                  </li>
                ))}
              </ol>
              <div className="lead-synth">
                <h3 className="small">Answer</h3>
                {r.synthesis ? <div className="lead-text">{r.synthesis}</div> : <div className="muted small">Written when every sub-task has finished.</div>}
                {r.conversation_id ? <Link className="btn sm" to={`/assistant/${r.conversation_id}`}>Open the chat that started it</Link> : null}
              </div>
            </>}
          </article>
        );
      })}
    </section>
  );
}
