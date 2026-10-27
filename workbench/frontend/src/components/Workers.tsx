import { Check, Loader2, Play, Plus, RotateCcw, Square, Users } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { Markdown, relTime, useToast } from "../util";

type Role = { role: string; label: string; about: string; scope: string; review: boolean; tools: string[]; default_tools: string[]; asks: string[] };
export type Worker = {
  id: number; role: string; role_label: string; goal: string; expected: string; acceptance: string; scope: string; status: string; verification: string;
  attempts: number; max_attempts: number; budget_cents: number; used_cents: number; reserved_cents: number; after: number[]; review_of: number | null;
  attention: string; error: string; check_note: string; result: { summary?: string; evidence?: string; verdict?: { verdict: string; issues: string } };
  pending_approvals: number; user_started: number; updated_at: string;
};
export type SharedBudget = { cap_cents: number; used_cents: number; reserved_cents: number; free_cents: number | null };
type Detail = Worker & { events: { at: string; kind: string; text: string }[]; approvals: { id: number; tool: string; args: string; reason: string }[] };

const LABEL: Record<string, string> = {
  queued: "Waiting", running: "Working", waiting_approval: "Needs your approval", in_review: "Being reviewed", needs_you: "Needs you",
  done: "Done", failed: "Failed", blocked: "Not run", cancelled: "Cancelled",
};
const TONE: Record<string, string> = { running: "accent", waiting_approval: "warn", needs_you: "warn", done: "ok", failed: "danger", blocked: "danger" };
const VERIFIED: Record<string, string> = { verified: "Verified", accepted: "Accepted by you", rejected: "Rejected by review", unverified: "Not verified" };

/** Who waits for whom: workers in columns by how many others they wait for (reviews sit with the work they check). */
function Flow({ workers }: { workers: Worker[] }) {
  const main = workers.filter((w) => w.role !== "review");
  if (main.length < 2) return null;
  const depth = new Map<number, number>();
  const d = (w: Worker, seen = new Set<number>()): number => {
    if (depth.has(w.id)) return depth.get(w.id)!;
    if (seen.has(w.id)) return 0;
    seen.add(w.id);
    const v = w.after.length ? 1 + Math.max(...w.after.map((a) => { const x = main.find((m) => m.id === a); return x ? d(x, seen) : 0; })) : 0;
    depth.set(w.id, v);
    return v;
  };
  main.forEach((w) => d(w));
  const cols = Math.max(...main.map((w) => depth.get(w.id) ?? 0)) + 1;
  return (
    <div className="wflow" aria-label="How the work is split">
      {Array.from({ length: cols }, (_, c) => (
        <div key={c} className="wflow-col">
          <div className="faint small">{c === 0 ? "Starts" : `Then (${c})`}</div>
          {main.filter((w) => (depth.get(w.id) ?? 0) === c).map((w) => {
            const review = workers.find((r) => r.review_of === w.id);
            return (
              <div key={w.id} className={`wflow-node ${TONE[w.status] ?? ""}`} title={w.goal}>
                <span className="small"><strong>#{w.id} {w.role_label}</strong></span>
                <span className="small muted truncate">{LABEL[w.status] ?? w.status}{review ? ` · reviewed by #${review.id}` : ""}</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

/** A mission's workers: each a role with its own tools, checked and independently reviewed; you see and decide everything. */
export default function Workers({ missionId, workers, budget, cancelled, onChange }: {
  missionId: number; workers: Worker[]; budget: SharedBudget; cancelled: boolean; onChange: () => void;
}) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [par, setPar] = useState(1);
  const [maxPar, setMaxPar] = useState(4);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<Detail | null>(null);
  const [form, setForm] = useState({ role: "research", goal: "", expected: "", acceptance: "", scope: "", budget: "", after: [] as number[], review: true });
  const toast = useToast();
  useEffect(() => {
    api<{ roles: Role[]; parallel: number; max_parallel: number }>("/workers/roles").then((r) => { setRoles(r.roles); setPar(r.parallel); setMaxPar(r.max_parallel); }).catch(() => setRoles([]));
  }, []);
  useEffect(() => {  // keep an open worker's detail current while things change
    if (!open) return;
    api<Detail>(`/workers/${open.id}`).then(setOpen).catch(() => undefined);
  }, [workers]); // eslint-disable-line react-hooks/exhaustive-deps
  const role = roles.find((r) => r.role === form.role);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api(`/missions/${missionId}/workers`, { body: {
        role: form.role, goal: form.goal, expected: form.expected, acceptance: form.acceptance, scope: form.scope || null,
        budget_cents: Number(form.budget) || 0, after: form.after, needs_review: form.review } });
      toast.ok("Worker added");
      setAdding(false);
      setForm({ ...form, goal: "", expected: "", acceptance: "", after: [] });
      onChange();
    } catch (err) { toast.err(err); }
  };
  const act = async (w: Worker, what: string) => {
    try { await api(`/workers/${w.id}/${what}`, { body: what === "cancel" || what === "start" ? undefined : {} , method: "POST" }); onChange(); if (open?.id === w.id) setOpen(await api<Detail>(`/workers/${w.id}`)); }
    catch (e) { toast.err(e); }
  };
  const decide = async (aid: number, approve: boolean) => {
    try { await api(`/approvals/${aid}`, { body: { approve } }); toast.ok(approve ? "Approved" : "Denied"); onChange(); }
    catch (e) { toast.err(e); }
  };
  const setParallel = async (n: number) => {
    try { setPar((await api<{ parallel: number }>("/workers/settings", { method: "PUT", body: { parallel: n } })).parallel); } catch (e) { toast.err(e); }
  };
  const show = async (w: Worker) => {
    if (open?.id === w.id) { setOpen(null); return; }
    try { setOpen(await api<Detail>(`/workers/${w.id}`)); } catch (e) { toast.err(e); }
  };

  return (
    <div className="card col" aria-labelledby={`workers-${missionId}`}>
      <div className="row wrap">
        <Users size={16} aria-hidden="true" /><h3 id={`workers-${missionId}`} className="grow">Workers</h3>
        <label className="row small" style={{ gap: 6 }}>At once
          <select className="input" style={{ width: 64 }} value={par} onChange={(e) => setParallel(Number(e.target.value))} aria-label="Workers running at once">
            {Array.from({ length: maxPar }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        {!cancelled && <button type="button" className="btn sm" onClick={() => setAdding(!adding)} aria-expanded={adding}><Plus size={13} /> Add worker</button>}
      </div>
      <div className="muted small">Each worker does one part with only its role's tools, is checked against what you said counts as done, and by default
        an independent reviewer approves it. Changes wait for your approval; computer work waits for you to start it.
        {budget.cap_cents > 0 && ` Shared budget: ${budget.used_cents.toFixed(1)}¢ spent and ${budget.reserved_cents.toFixed(1)}¢ held of ${budget.cap_cents}¢.`}</div>
      {adding && (
        <form className="col" onSubmit={add}>
          <div className="grid two">
            <label className="field">Role<select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value, scope: "", review: !!roles.find((r) => r.role === e.target.value)?.review })}>
              {roles.filter((r) => r.role !== "review").map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}</select></label>
            <label className="field">Scope<select className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
              <option value="">Role default ({role?.scope})</option><option value="read">Read only</option><option value="draft">Read and write drafts</option>
              <option value="write">May change things (each change asks you)</option></select></label>
          </div>
          {role && <div className="small muted">{role.about} Tools: {role.default_tools.join(", ") || "none"}{role.asks.length ? `; asks before ${role.asks.join(", ")}` : ""}.</div>}
          <label className="field">Goal<input className="input" required minLength={5} value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} /></label>
          <div className="grid two">
            <label className="field">Expected output <span className="hint">optional</span><input className="input" value={form.expected} onChange={(e) => setForm({ ...form, expected: e.target.value })} /></label>
            <label className="field">Done when <span className="hint">what the check must see</span><input className="input" value={form.acceptance} onChange={(e) => setForm({ ...form, acceptance: e.target.value })} /></label>
            <label className="field">Spending cap (cents) {budget.cap_cents > 0 ? <span className="hint">required: within the mission's {budget.cap_cents}¢</span> : <span className="hint">optional</span>}
              <input className="input" type="number" min={0} step="0.5" required={budget.cap_cents > 0} value={form.budget} onChange={(e) => setForm({ ...form, budget: e.target.value })} /></label>
            <label className="row small" style={{ gap: 6, alignSelf: "end" }}><input type="checkbox" checked={form.review} onChange={(e) => setForm({ ...form, review: e.target.checked })} /> Independent review before it counts</label>
          </div>
          {workers.filter((w) => w.role !== "review").length > 0 && (
            <fieldset className="row wrap small" style={{ border: 0, padding: 0, margin: 0, gap: 10 }}><legend className="field">Waits for</legend>
              {workers.filter((w) => w.role !== "review").map((w) => (
                <label key={w.id} className="row" style={{ gap: 4 }}><input type="checkbox" checked={form.after.includes(w.id)}
                  onChange={(e) => setForm({ ...form, after: e.target.checked ? [...form.after, w.id] : form.after.filter((x) => x !== w.id) })} /> #{w.id} {w.goal.slice(0, 40)}</label>
              ))}</fieldset>
          )}
          <div className="row"><button className="btn primary sm">Add</button><button type="button" className="btn sm" onClick={() => setAdding(false)}>Cancel</button></div>
        </form>
      )}
      <Flow workers={workers} />
      {workers.length === 0 && !adding && <div className="small muted">No workers yet. Split work into parts, for example a research worker per source and a coordinator that combines them.</div>}
      <ul className="col" style={{ listStyle: "none", margin: 0, padding: 0, gap: 6 }}>
        {workers.map((w) => (
          <li key={w.id} className="col" style={{ gap: 4, borderTop: "1px solid var(--border)", paddingTop: 6 }}>
            <div className="row wrap">
              <button type="button" className="btn ghost sm grow" style={{ justifyContent: "flex-start", textAlign: "left" }} onClick={() => show(w)} aria-expanded={open?.id === w.id}>
                <span className="truncate"><strong>#{w.id} {w.role_label}</strong> · {w.goal}</span></button>
              <span className={`badge ${TONE[w.status] ?? ""}`}>{w.status === "running" && <Loader2 size={11} className="spin" />} {LABEL[w.status] ?? w.status}</span>
              {(w.status === "done" || w.verification !== "unverified") && <span className={`badge ${w.verification === "verified" || w.verification === "accepted" ? "ok" : w.verification === "rejected" ? "danger" : ""}`}>{VERIFIED[w.verification]}</span>}
            </div>
            <div className="small muted">{w.scope} scope{w.after.length ? ` · after #${w.after.join(", #")}` : ""}{w.review_of ? ` · reviews #${w.review_of}` : ""}
              {` · ${w.used_cents.toFixed(1)}¢${w.budget_cents ? ` of ${w.budget_cents}¢` : ""}`}{w.attempts ? ` · attempt ${w.attempts} of ${w.max_attempts}` : ""} · {relTime(w.updated_at)}</div>
            {(w.attention || w.error) && <div className="small" style={{ color: w.status === "failed" || w.status === "blocked" ? "var(--danger)" : "var(--warn)" }}>{w.attention || w.error}</div>}
            <div className="row wrap">
              {w.role === "computer" && !w.user_started && ["queued", "needs_you"].includes(w.status) && <button className="btn primary sm" onClick={() => act(w, "start")}><Play size={12} /> Start on my computer</button>}
              {w.status === "needs_you" && w.result?.summary && <button className="btn sm" onClick={() => act(w, "accept")}><Check size={12} /> Accept the result</button>}
              {["failed", "needs_you", "blocked", "cancelled"].includes(w.status) && !(w.role === "computer" && !w.user_started) && <button className="btn sm" onClick={() => act(w, "retry")}><RotateCcw size={12} /> Run again</button>}
              {!["done", "failed", "cancelled"].includes(w.status) && <button className="btn ghost sm danger" onClick={() => act(w, "cancel")}><Square size={11} /> Cancel</button>}
            </div>
            {open?.id === w.id && (
              <div className="col small" style={{ paddingLeft: 12, gap: 6 }}>
                {open.acceptance && <div><strong>Done when:</strong> {open.acceptance}</div>}
                {open.approvals.map((a) => (
                  <div key={a.id} className="col" style={{ gap: 4 }}>
                    <div><strong className="mono">{a.tool}</strong> <span className="muted">{a.reason}</span></div>
                    <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0, background: "var(--panel-2)", padding: 8, borderRadius: 7 }}>{a.args}</pre>
                    <div className="row"><button className="btn primary sm" onClick={() => decide(a.id, true)}>Approve</button><button className="btn sm" onClick={() => decide(a.id, false)}>Deny</button></div>
                  </div>
                ))}
                {open.check_note && <div><strong>Check:</strong> {open.check_note}</div>}
                {open.result?.verdict && <div><strong>Review:</strong> {open.result.verdict.verdict}{open.result.verdict.issues ? `: ${open.result.verdict.issues}` : ""}</div>}
                {open.result?.summary && <div><strong>Result:</strong><Markdown text={open.result.summary} /></div>}
                <details><summary style={{ cursor: "pointer" }}>Activity ({open.events.length})</summary>
                  <ul className="col" style={{ listStyle: "none", margin: "6px 0 0", padding: 0, gap: 3 }}>
                    {open.events.map((e, i) => <li key={i}><span className="faint">{relTime(e.at)}</span> · {e.text}</li>)}</ul></details>
              </div>
            )}
          </li>
        ))}
      </ul>
      {toast.node}
    </div>
  );
}
