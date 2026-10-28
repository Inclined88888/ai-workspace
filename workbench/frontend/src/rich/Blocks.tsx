import { ArrowUpDown, Check, Copy, Download, ExternalLink, FileText, Loader2, Pause, Play, Square } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Block, fmt, JobKind, Kind, Rec } from "./schema";
import RichText from "./RichText";
import { usePreview } from "./Preview";
import type { WorkItem } from "../live";

const Chart = lazy(() => import("./Chart"));

/* Renders validated blocks through an explicit allow-list of components. Every value is rendered as text. Controls
   are decided here from the block's kind and record id (never from the data), and each one calls the normal API, so
   permissions, approvals and sign-in apply exactly as they do on the page it opens. */

const STATUS_TONE: Record<string, string> = {
  done: "ok", todo: "", doing: "accent", running: "accent", queued: "accent", planning: "accent", review: "warn", review_step: "warn",
  waiting_approval: "warn", paused: "warn", failed: "danger", partial: "warn", cancelled: "", error: "danger",
};
const STATUS_LABEL: Record<string, string> = {
  todo: "To do", doing: "In progress", done: "Done", planning: "Planning", review: "Plan to check", review_step: "Step to check",
  queued: "Starting", running: "Working", waiting_approval: "Needs approval", paused: "Paused", partial: "Finished with gaps",
  failed: "Failed", cancelled: "Cancelled", error: "Failed",
};
const FINAL = ["done", "failed", "cancelled", "error", "partial", "deleted"];

const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const id = (r: Rec) => (typeof r.id === "number" ? r.id : null);

function when(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return iso.length <= 10 ? d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })
    : d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** A record that keeps itself current while it is still working (missions and research started from chat). */
function useLive(kind: Kind, r: Rec): Rec {
  const [cur, setCur] = useState(r);
  const live = r.live === true && id(r) !== null && (kind === "mission" || kind === "research");
  useEffect(() => {
    if (!live) return;
    let stop = false;
    let t: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const d = await api<Record<string, unknown>>(kind === "mission" ? `/missions/${id(r)}` : `/research/${id(r)}`);
        if (stop) return;
        const plan = Array.isArray(d.plan) ? d.plan as { status: string }[] : [];
        setCur((c) => ({ ...c, status: str(d.status), progress: str(d.progress), steps: kind === "mission" ? plan.length : c.steps,
          steps_done: kind === "mission" ? plan.filter((s) => s.status === "done").length : c.steps_done }));
        if (!["done", "failed", "cancelled", "error", "partial"].includes(str(d.status))) t = setTimeout(poll, 3000);
      } catch { /* the card keeps its last known state */ }
    };
    poll();
    return () => { stop = true; clearTimeout(t); };
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps
  return cur;
}

const QUIET = "quiet";  // an action whose result is visible by itself (a preview opening): no "Done" after it

function Act({ label, icon, run, tone = "" }: { label: string; icon?: React.ReactNode; run: () => Promise<unknown>; tone?: string }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  return (
    <>
      <button type="button" className={`btn sm ${tone}`} disabled={busy} onClick={async () => {
        setBusy(true); setMsg("");
        try { if ((await run()) !== QUIET) setMsg("Done"); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
      }}>{busy ? <Loader2 size={12} className="spin" /> : icon} {label}</button>
      {msg && <span className={`small ${msg === "Done" ? "muted" : ""}`} role={msg === "Done" ? "status" : "alert"} style={msg === "Done" ? undefined : { color: "var(--danger)" }}>{msg}</span>}
    </>
  );
}

function download(name: string, text: string, mime = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function RecordCard({ kind, r: raw }: { kind: Kind; r: Rec }) {
  const r = useLive(kind, raw);
  const rid = id(r);
  const status = str(r.status);
  const badge = status ? <span className={`badge ${STATUS_TONE[status] ?? ""}`}>{(status === "running" || status === "planning") && <Loader2 size={10} className="spin" />} {STATUS_LABEL[status] ?? status}</span> : null;
  const [preview, setPreview] = useState<string | null>(null);
  const [docBody, setDocBody] = useState<string | null>(null);
  const navigate = useNavigate();
  const openPreview = usePreview();
  let title = "", meta: string[] = [], body = "", actions: React.ReactNode = null;
  switch (kind) {
    case "task":
      title = str(r.title); meta = [r.due ? `due ${when(str(r.due))}` : "", str(r.project)];
      actions = rid !== null && <>{status !== "done" && <Act label="Mark done" icon={<Check size={12} />} run={() => api(`/tasks/${rid}`, { method: "PATCH", body: { status: "done" } })} />}
        <Link className="btn ghost sm" to="/tasks">Open tasks</Link></>;
      break;
    case "event":
      title = str(r.title); meta = [r.all_day ? `${when(str(r.start).slice(0, 10))}, all day` : when(str(r.start)), str(r.location), str(r.calendar)];
      actions = <Link className="btn ghost sm" to="/calendar">Open calendar</Link>;
      break;
    case "doc": {
      title = str(r.title) || "Untitled"; body = str(r.excerpt); meta = [str(r.kind)];
      const route = r.kind === "document" ? "documents" : "notes";
      actions = rid !== null && <>
        <Act label={docBody === null ? "Preview" : "Hide preview"} run={async () => {
          if (openPreview) { openPreview({ kind: "doc", id: rid, title }); return QUIET; }
          if (docBody !== null) { setDocBody(null); return; }
          const d = await api<{ body: string }>(`/docs/${rid}`);
          setDocBody(d.body || "(This document is empty.)");
        }} />
        <Link className="btn ghost sm" to={`/${route}/${rid}`}><FileText size={12} /> Open</Link>
        <Act label="Download" icon={<Download size={12} />} run={async () => {
          const d = await api<{ title: string; body: string }>(`/docs/${rid}`);
          download(`${(d.title || "note").replace(/[^\w .-]+/g, "_").slice(0, 80)}.md`, d.body);
        }} /></>;
      break;
    }
    case "file":
      title = str(r.name); body = str(r.excerpt); meta = [typeof r.size === "number" ? `${Math.max(1, Math.round(r.size / 1024))} KB` : ""];
      actions = rid !== null && <>
        <Act label={preview === null ? "Preview" : "Hide preview"} run={async () => {
          if (openPreview) { openPreview({ kind: "file", id: rid, title }); return QUIET; }
          if (preview !== null) { setPreview(null); return; }
          const d = await api<{ text: string }>(`/files/${rid}`).catch(() => null);
          setPreview(d?.text ?? (str(r.excerpt) || "No text could be extracted from this file."));
        }} />
        <a className="btn ghost sm" href={`/api/files/${rid}/download`} download><Download size={12} /> Download</a>
        <Link className="btn ghost sm" to={`/files/${rid}`}>Open</Link></>;
      break;
    case "mission": {
      title = str(r.goal);
      const steps = typeof r.steps === "number" && r.steps > 0 ? `${r.steps_done ?? 0} of ${r.steps} steps` : "";
      meta = [steps, typeof r.workers === "number" && r.workers ? `${r.workers} worker(s)${r.workers_active ? `, ${r.workers_active} active` : ""}` : "",
        typeof r.used_cents === "number" && r.used_cents ? `${fmt(r.used_cents, "cents")}${typeof r.budget_cents === "number" ? ` of ${fmt(r.budget_cents, "cents")}` : ""}` : ""];
      actions = rid !== null && <>
        <Link className="btn sm" to={`/missions/${rid}`}>{status === "review" ? "Check the plan" : "Open"}</Link>
        {status === "paused" && <Act label="Resume" icon={<Play size={12} />} run={() => api(`/missions/${rid}/start`, { method: "POST" })} />}
        {["running", "queued", "waiting_approval"].includes(status) && <Act label="Pause" icon={<Pause size={12} />} run={() => api(`/missions/${rid}/pause`, { method: "POST" })} />}
        {["failed", "partial"].includes(status) && <Act label="Retry" run={() => api(`/missions/${rid}/start`, { method: "POST" })} />}
        {!["done", "failed", "cancelled", "partial"].includes(status) && <Act label="Cancel" icon={<Square size={11} />} tone="ghost danger"
          run={async () => { if (confirm("Cancel this mission? Steps not yet done will not run.")) await api(`/missions/${rid}/cancel`, { method: "POST" }); }} />}</>;
      break;
    }
    case "research":
      title = str(r.question); meta = [status === "running" ? str(r.progress) : ""];
      actions = rid !== null && <><Link className="btn sm" to={`/research/${rid}`}>{status === "done" ? "Read the report" : "Open"}</Link>
        {status === "error" && <Act label="Retry" run={() => api(`/research/${rid}/retry`, { method: "POST" })} />}</>;
      break;
    case "project":
      title = str(r.name); body = r.goals ? `Goals: ${str(r.goals)}` : "";
      meta = [`${r.open_tasks ?? 0} open task(s)`, `${r.open_questions ?? 0} open question(s)`, `${r.missions ?? 0} mission(s)`,
        typeof r.spend_cents === "number" ? `${fmt(r.spend_cents, "cents")} this month${typeof r.budget_cents === "number" ? ` of ${fmt(r.budget_cents, "cents")}` : ""}` : "",
        r.local_only ? "local models only" : ""];
      actions = rid !== null && <><Link className="btn sm" to={`/projects/${rid}`}>Open project</Link>
        <button type="button" className="btn ghost sm" onClick={() => navigate(`/assistant?project=${rid}`)}>New chat in it</button></>;
      break;
    case "email":
      title = str(r.subject) || "(no subject)"; meta = [str(r.from), r.to ? `to ${str(r.to)}` : "", str(r.date), r.draft ? "draft, not sent" : ""];
      body = r.excerpt ? str(r.excerpt) : "";
      actions = r.draft ? <Link className="btn sm" to="/mail?view=drafts">Review and send</Link> : <Link className="btn ghost sm" to="/mail">Open mail</Link>;
      break;
    case "memory":
      title = str(r.content); meta = [str(r.kind)];
      actions = <Link className="btn ghost sm" to="/assistant-memory">Review memory</Link>;
      break;
    case "schedule":
      title = str(r.name); meta = [str(r.cron), r.enabled ? (r.next_run ? `next ${when(str(r.next_run))}` : "on") : "paused"];
      actions = <Link className="btn ghost sm" to="/automations">Open automations</Link>;
      break;
  }
  return (
    <li className="rcard">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="grow"><div className="rcard-title">{title}</div>
          {meta.filter(Boolean).length > 0 && <div className="small muted">{meta.filter(Boolean).join(" · ")}</div>}</div>
        {badge}
      </div>
      {body && <div className="small rcard-body">{body}</div>}
      {preview !== null && <pre className="rcard-preview">{preview}</pre>}
      {docBody !== null && <div className="rcard-preview doc-preview"><RichText text={docBody} /></div>}
      {actions && <div className="row wrap rcard-actions">{actions}</div>}
    </li>
  );
}

type Job = Extract<Block, { type: "job" }>;
const JOB_PATH: Record<JobKind, string> = { mission: "missions", research: "research", coding: "coding" };

/** Work started from the conversation, kept current while it runs. Its controls call the same endpoints as the
 *  Missions and Research pages, so approvals, budgets and limits apply exactly as they do there. */
function JobCard({ b: initial }: { b: Job }) {
  const [b, setB] = useState(initial);
  const openPreview = usePreview();
  const [tick, setTick] = useState(0);
  useEffect(() => setB(initial), [initial]);
  const live = !FINAL.includes(b.status);
  useEffect(() => {
    if (!live) return;
    let stop = false;
    const t = setTimeout(async () => {
      try {
        const [d, w] = await Promise.all([api<Record<string, unknown>>(`/${JOB_PATH[b.kind]}/${b.id}`),
          api<WorkItem>(`/v1/work/${b.kind}:${b.id}`).catch(() => null)]);
        if (stop) return;
        const plan = Array.isArray(d.plan) ? (d.plan as { title: string; status: string }[]) : null;
        setB((c) => ({ ...c, status: str(d.status), error: str(d.error), status_text: w?.status_text ?? c.status_text,
          progress: Array.isArray(d.progress) ? (d.progress as string[]).slice(-6) : d.progress ? [str(d.progress)] : c.progress,
          steps: plan ? plan.map((s) => ({ title: s.title, status: s.status })) : c.steps }));
      } catch { /* keep the last known state */ }
      if (!stop) setTick((n) => n + 1);
    }, tick === 0 ? 0 : 3000);
    return () => { stop = true; clearTimeout(t); };
  }, [live, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => setTick((n) => n + 1);
  useEffect(() => {  // the live connection says this work changed: show it now instead of at the next poll
    const on = (e: Event) => {
      const it = ((e as CustomEvent<WorkItem[]>).detail ?? []).find((w) => w.id === `${b.kind}:${b.id}`);
      if (it) setB((c) => ({ ...c, status: it.native_status, status_text: it.status_text, error: it.error || c.error }));
    };
    window.addEventListener("rooks:work", on);
    return () => window.removeEventListener("rooks:work", on);
  }, [b.kind, b.id]);
  const s = b.status;
  // The words come from the server (work.py), the same in the browser, the terminal and the desktop app
  const badge = <span className={`badge ${STATUS_TONE[s] ?? ""}`}>{["running", "planning", "queued"].includes(s) && <Loader2 size={10} className="spin" />} {b.status_text || STATUS_LABEL[s] || s}</span>;
  const label = { mission: "Mission", research: "Research", coding: b.agent || "Coding task" }[b.kind];
  const done = b.steps.filter((x) => x.status === "done").length;
  let actions: React.ReactNode = null;
  if (b.kind === "mission") {
    actions = <>
      {s === "review" && <Act label="Approve plan and start" icon={<Play size={12} />} tone="primary" run={async () => { await api(`/missions/${b.id}/start`, { method: "POST" }); refresh(); }} />}
      <Link className="btn sm" to={`/missions/${b.id}`}>{s === "review" ? "Change the plan" : ["waiting_approval", "review_step"].includes(s) ? "Answer it" : "Open mission"}</Link>
      {["running", "queued", "waiting_approval"].includes(s) && <Act label="Pause" icon={<Pause size={12} />} run={async () => { await api(`/missions/${b.id}/pause`, { method: "POST" }); refresh(); }} />}
      {s === "paused" && <Act label="Resume" icon={<Play size={12} />} run={async () => { await api(`/missions/${b.id}/start`, { method: "POST" }); refresh(); }} />}
      {["failed", "partial"].includes(s) && <Act label="Retry" run={async () => { await api(`/missions/${b.id}/start`, { method: "POST" }); refresh(); }} />}
      {!FINAL.includes(s) && <Act label="Stop" icon={<Square size={11} />} tone="ghost danger"
        run={async () => { if (confirm("Stop this mission? Steps not yet done will not run.")) { await api(`/missions/${b.id}/cancel`, { method: "POST" }); refresh(); } }} />}</>;
  } else if (b.kind === "research") {
    actions = <>
      {s === "done" && openPreview && <button type="button" className="btn sm" onClick={() => openPreview({ kind: "research", id: b.id, title: b.title })}>Preview report</button>}
      <Link className="btn ghost sm" to={`/research/${b.id}`}>{s === "done" ? "Sources and evidence" : "Open"}</Link>
      {s === "running" && <Act label="Stop" icon={<Square size={11} />} tone="ghost danger" run={async () => { await api(`/research/${b.id}/cancel`, { method: "POST" }); refresh(); }} />}
      {s === "error" && <Act label="Retry" run={async () => { await api(`/research/${b.id}/retry`, { method: "POST" }); refresh(); }} />}</>;
  } else {
    actions = s === "running" ? <Act label="Stop" icon={<Square size={11} />} tone="ghost danger" run={async () => { await api(`/coding/${b.id}/stop`, { method: "POST" }); refresh(); }} />
      : <Link className="btn ghost sm" to="/computer">Computer</Link>;
  }
  return (
    <div className="rblock">
      <div className="rcard job-card">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <div className="grow"><div className="small faint">{label}</div><div className="rcard-title">{b.title}</div>
            <div className="small muted">{[b.kind === "mission" && b.steps.length ? `${done} of ${b.steps.length} steps` : "",
              b.kind === "mission" && s === "review" && b.estimate_cents ? `estimated up to ${fmt(b.estimate_cents, "cents")}` : "",
              b.folder ? `in ${b.folder}` : "", b.billing].filter(Boolean).join(" · ")}</div></div>
          {badge}
        </div>
        {b.kind === "mission" && s === "review" && b.steps.length > 0 && <ol className="job-steps small">{b.steps.map((x, i) => <li key={i}>{x.title}</li>)}</ol>}
        {live && b.progress.length > 0 && <ul className="job-progress small muted">{b.progress.slice(-4).map((p, i) => <li key={i} className="truncate">{p}</li>)}</ul>}
        {b.error && FINAL.includes(s) && s !== "done" && <div className="small" role="alert" style={{ color: "var(--danger)" }}>{b.error}</div>}
        <div className="row wrap rcard-actions">{actions}</div>
      </div>
    </div>
  );
}

function Table({ b }: { b: Extract<Block, { type: "table" }> }) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [copied, setCopied] = useState(false);
  const rows = useMemo(() => {
    if (!sort) return b.rows;
    return [...b.rows].sort((x, y) => {
      const a = x[sort.key], c = y[sort.key];
      if (typeof a === "number" && typeof c === "number") return (a - c) * sort.dir;
      return str(a).localeCompare(str(c)) * sort.dir;
    });
  }, [b.rows, sort]);
  const csv = () => [b.columns.map((c) => c.label), ...rows.map((r) => b.columns.map((c) => str(r[c.key])))]
    .map((line) => line.map((v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)).join(",")).join("\n");
  return (
    <div className="rblock">
      <div className="row rblock-head"><span className="rblock-title grow">{b.title}</span>
        <button type="button" className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(csv()).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => undefined); }}>
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy"}</button>
        <button type="button" className="btn ghost sm" onClick={() => download("table.csv", csv(), "text/csv")}><Download size={12} /> CSV</button></div>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={b.title || "Table"}>
        <table className="table rtable">
          <thead><tr>{b.columns.map((c) => (
            <th key={c.key} scope="col" className={c.format && c.format !== "text" ? "num" : ""} aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}>
              {c.label ? <button type="button" className="th-sort" onClick={() => setSort(sort?.key === c.key ? { key: c.key, dir: sort.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 })}>
                {c.label} <ArrowUpDown size={11} aria-hidden="true" /></button> : null}</th>))}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{b.columns.map((c) => <td key={c.key} className={c.format && c.format !== "text" ? "num" : ""}>{fmt(r[c.key], c.format)}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {b.note && <div className="small faint">{b.note}</div>}
    </div>
  );
}

export function BlockView({ b, onSource }: { b: Block; onSource?: (n: number) => void }) {
  switch (b.type) {
    case "records":
      return (
        <div className="rblock">
          {b.title && <div className="rblock-title">{b.title}</div>}
          <ul className={`rcards ${b.items.length > 2 ? "grid" : ""}`}>{b.items.map((r, i) => <RecordCard key={`${b.kind}-${r.id ?? i}`} kind={b.kind} r={r} />)}</ul>
        </div>
      );
    case "table":
      return <Table b={b} />;
    case "kpis":
      return <dl className="rkpis">{b.items.map((k, i) => <div key={i} className="rkpi"><dt>{k.label}</dt><dd>{fmt(k.value, k.format)}</dd></div>)}</dl>;
    case "chart":
      return <div className="rblock"><Suspense fallback={<div className="skeleton" style={{ height: 160 }} />}><Chart kind={b.kind} title={b.title} points={b.points} format={b.format} /></Suspense></div>;
    case "sources":
      return (
        <div className="rblock">
          <div className="rblock-title">Sources</div>
          <ol className="rsources">{b.items.map((s, i) => (
            <li key={i} id={s.n ? `src-${s.n}` : undefined}>
              <details>
                <summary><span className="badge">{s.n ?? i + 1}</span> <span className="grow truncate">{s.title || s.site}</span> <span className="faint small">{s.site}{s.date ? ` · ${s.date}` : ""}</span></summary>
                <div className="col small" style={{ padding: "6px 0 4px 30px", gap: 4 }}>
                  {s.snippet && <div className="muted">{s.snippet}</div>}
                  <a href={s.url} target="_blank" rel="noopener noreferrer nofollow" className="row" style={{ wordBreak: "break-all" }} onClick={() => onSource?.(s.n ?? i + 1)}>{s.url} <ExternalLink size={11} aria-label="opens in a new tab" /></a>
                </div>
              </details>
            </li>))}</ol>
        </div>
      );
    case "list":
      return <div className="rblock">{b.title && <div className="rblock-title">{b.title}</div>}<ul className="rlist">{b.items.map((t, i) => <li key={i}>{t}</li>)}</ul></div>;
    case "job":
      return <JobCard b={b} />;
    case "notice":
      return <div className={`chat-note ${b.tone === "warn" ? "warn" : ""}`} role="status">{b.text}</div>;
  }
}

export default function Blocks({ blocks }: { blocks: Block[] }) {
  if (!blocks.length) return null;
  return <div className="rblocks">{blocks.map((b, i) => <BlockView key={i} b={b} />)}</div>;
}
