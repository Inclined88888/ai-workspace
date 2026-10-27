import { BellOff, BellRing, ExternalLink, FileText, Loader2, RotateCcw, Search, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Item = { id: number; question: string; model: string; status: "running" | "done" | "error" | "cancelled"; progress: string; created_at: string };
type Claim = { claim: string; quote: string; kind: string };
type Source = { n?: number; title: string; url: string; snippet: string; site?: string; date?: string; author?: string; credibility?: string; credibility_why?: string;
  freshness?: string; relevance?: number; score?: number; found_by?: string[]; duplicates?: string[]; claims?: Claim[] };
type Stats = { sources_read?: number; sources_used?: number; skipped?: number; claims?: number; claims_dropped_unquoted?: number; invalid_removed?: number;
  unsourced_findings?: number; uncited_sources?: number[]; seconds?: number; rounds?: number; max_rounds?: number; stopped_because?: string;
  contradictions?: number; verification?: Record<string, number>; inferences?: number };
type Round = { round: number; queries: string[]; pages_read: number; new_sources: number; claims: number; thin: string[]; gaps: string[] };
type Evidence = { id: number; claim: string; basis: "fact" | "inference" | "uncertain"; kind: string; source_n: number | null; url: string; title: string; published: string;
  retrieved_at: string; excerpt: string; location: string; credibility: string; confidence: number; verification: string; verified_by: string;
  contradicts: number[]; corroborated_by: number[]; round: number };
type Full = Item & { report: string; error: string; sources: Source[]; plan: { q: string; queries: string[] }[]; skipped: { url: string; title: string; why: string }[]; stats: Stats;
  rounds: Round[]; evidence: Evidence[]; monitor: { id: number; cron: string; enabled: number; last_check_at: string | null } | null; project_id: number | null; max_rounds: number };
type ProjectRef = { id: number; name: string };
const BASIS: Record<string, string> = { fact: "ok", inference: "accent", uncertain: "warn" };
const CHECK: Record<string, string> = { supported: "Supported", not_supported: "Not supported", unclear: "Unclear", unverified: "Not checked" };

function download(name: string, text: string, mime: string) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function evidenceCsv(rows: Evidence[]): string {
  const cols: (keyof Evidence)[] = ["basis", "claim", "source_n", "url", "title", "published", "retrieved_at", "excerpt", "location", "verification", "confidence"];
  const cell = (v: unknown) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

function EvidenceTable({ rows }: { rows: Evidence[] }) {
  const [only, setOnly] = useState("");
  const shown = only ? rows.filter((e) => e.basis === only) : rows;
  const label = (id: number) => { const e = rows.find((x) => x.id === id); return e?.source_n ? `[${e.source_n}]` : `#${id}`; };
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row wrap"><h3 className="grow">Evidence</h3>
        {(["", "fact", "uncertain", "inference"] as const).map((b) => (
          <button key={b} type="button" className={`btn sm ${only === b ? "primary" : ""}`} aria-pressed={only === b} onClick={() => setOnly(b)}>
            {b ? `${b[0].toUpperCase()}${b.slice(1)} (${rows.filter((e) => e.basis === b).length})` : `All (${rows.length})`}</button>))}
      </div>
      <div className="muted small">Facts are stated by a source in words found on the page and were not doubted on an independent re-read. Uncertain
        means the re-read did not support it, or it is an opinion or forecast. Inferences are drawn by the report; no source states them.</div>
      <div style={{ overflowX: "auto" }}>
        <table className="table small" aria-label="Evidence ledger">
          <thead><tr><th>Basis</th><th>Claim</th><th>Source</th><th>Independent check</th><th>Confidence</th></tr></thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id}>
                <td><span className={`badge ${BASIS[e.basis] ?? ""}`}>{e.basis}</span></td>
                <td style={{ minWidth: 220 }}>{e.claim}
                  {e.excerpt && <details><summary className="faint" style={{ cursor: "pointer" }}>Exact words</summary><span className="muted">"{e.excerpt}"</span><br /><span className="faint">{e.location}</span></details>}
                  {e.contradicts.length > 0 && <div style={{ color: "var(--warn)" }}>Disagrees with {e.contradicts.map(label).join(", ")}</div>}
                  {e.corroborated_by.length > 0 && <div className="faint">Also stated by {e.corroborated_by.map(label).join(", ")}</div>}</td>
                <td>{e.source_n ? <a href={e.url} target="_blank" rel="noreferrer noopener" title={e.title}>[{e.source_n}]</a> : <span className="faint">none</span>}
                  {e.published && <div className="faint">published {e.published}</div>}{e.retrieved_at && <div className="faint">read {e.retrieved_at.slice(0, 10)}</div>}
                  {e.round > 1 && <div className="faint">round {e.round}</div>}</td>
                <td title={e.verified_by ? `Checked by ${e.verified_by}` : undefined}>{CHECK[e.verification] ?? e.verification}</td>
                <td>{e.basis === "inference" ? <span className="faint">n/a</span> : e.confidence.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const CRED: Record<string, string> = { higher: "ok", medium: "", lower: "warn" };

function SourceCard({ s, i }: { s: Source; i: number }) {
  return (
    <details className="card" style={{ padding: 10 }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }} className="row wrap">
        <span className="badge">{s.n ?? i + 1}</span>
        <span className="grow truncate" style={{ minWidth: 160 }}><strong>{s.title || s.url}</strong> <span className="muted small">{s.site}</span></span>
        {s.credibility && <span className={`badge ${CRED[s.credibility] ?? ""}`} title={s.credibility_why}>{s.credibility} credibility</span>}
        {s.freshness && <span className="badge" title={s.date || "no date on the page"}>{s.date ? `${s.date} · ${s.freshness}` : "undated"}</span>}
      </summary>
      <div className="col small" style={{ marginTop: 8, gap: 6 }}>
        <a href={s.url} target="_blank" rel="noreferrer noopener" className="row" style={{ wordBreak: "break-all" }}>{s.url} <ExternalLink size={11} /></a>
        {s.credibility_why && <div className="muted">Credibility is an estimate from the kind of site ({s.credibility_why}), not a check of this page.</div>}
        {s.claims && s.claims.length > 0 ? (
          <div><strong>Claims taken from it</strong> (each with the exact words found on the page)
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{s.claims.map((c, n) => <li key={n}>{c.claim} <span className="tag">{c.kind}</span><br /><span className="muted">"{c.quote}"</span></li>)}</ul></div>
        ) : <div className="muted">No quoted claims were extracted; the report used its text directly.</div>}
        {s.found_by && <div className="muted">Found by: {s.found_by.join("; ")}</div>}
        {s.duplicates && s.duplicates.length > 0 && <div className="muted">Same content also at: {s.duplicates.join(", ")}</div>}
        {s.relevance !== undefined && <div className="faint">Relevance {Math.round(s.relevance * 100)}% · score {s.score}</div>}
      </div>
    </details>
  );
}

export default function Research() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [items, setItems] = useState<Item[]>([]);
  const [searchProvider, setSearchProvider] = useState<string | null>(null);
  useEffect(() => { api<{ search_provider: string }>("/getting-started").then((r) => setSearchProvider(r.search_provider)).catch(() => setSearchProvider(null)); }, []);
  const [current, setCurrent] = useState<Full | null>(null);
  const [runs, setRuns] = useState(0);
  const location = useLocation();
  const [question, setQuestion] = useState(() => (location.state as { question?: string } | null)?.question ?? "");
  const [depth, setDepth] = useState(3);
  const [model, setModel] = useState("");
  const [projectId, setProjectId] = useState<number | "">("");
  const [saveTo, setSaveTo] = useState<number | "">("");
  const [cron, setCron] = useState("0 8 * * 1");
  const [projects, setProjects] = useState<ProjectRef[]>([]);
  useEffect(() => { api<ProjectRef[]>("/projects").then(setProjects).catch(() => setProjects([])); }, []);
  const { models } = useModels();
  const toast = useToast();

  const loadList = () => api<Item[]>("/research").then(setItems).catch(toast.err);
  useEffect(() => { loadList(); }, []);

  useEffect(() => {
    if (!id) return setCurrent(null);
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const r = await api<Full>(`/research/${id}`);
        if (stop) return;
        setCurrent(r);
        if (r.status === "running") timer = setTimeout(poll, 1500);
        else loadList();
      } catch (e) {
        toast.err(e);
      }
    };
    poll();
    return () => { stop = true; clearTimeout(timer); };
  }, [id, runs]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const r = await api<Item>("/research", { body: { question, depth, model: model || null, project_id: projectId || null } });
      setQuestion("");
      loadList();
      navigate(`/research/${r.id}`);
    } catch (err) {
      toast.err(err);
    }
  };
  const save = async () => {
    try {
      const d = await api<{ id: number }>(`/research/${current!.id}/save`, { body: { project_id: saveTo || current!.project_id || null } });
      navigate(`/documents/${d.id}`);
    } catch (e) {
      toast.err(e);
    }
  };
  const retry = async () => {
    try {
      await api(`/research/${current!.id}/retry`, { method: "POST" });
      setRuns((n) => n + 1);
      loadList();
    } catch (e) {
      toast.err(e);
    }
  };
  const follow = async (on: boolean) => {
    try {
      setCurrent(on ? await api<Full>(`/research/${current!.id}/monitor`, { body: { cron } }) : await api<Full>(`/research/${current!.id}/monitor`, { method: "DELETE" }));
      toast.ok(on ? "Following: new results arrive as check-ins" : "No longer following");
    } catch (e) { toast.err(e); }
  };
  const cancel = async () => {
    try { await api(`/research/${current!.id}/cancel`, { method: "POST" }); } catch (e) { toast.err(e); }
  };
  const remove = async (rid: number) => {
    await api(`/research/${rid}`, { method: "DELETE" });
    if (current?.id === rid) navigate("/research");
    loadList();
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Research</h1><p className="muted">Ask a question. ROOKS plans several web searches, reads the pages and writes a report with numbered sources you can check.</p></div>
      </div>
      {searchProvider === "" && <div className="alert" role="status" style={{ marginBottom: 16 }}>Web search isn't set up yet, so research can't find sources. <Link to="/settings#search">Turn it on in Settings</Link>: a free Tavily key takes a minute.</div>}
      <form className="card col" onSubmit={start} style={{ marginBottom: 20 }}>
        <label htmlFor="research-q" className="sr-only">Research question</label>
        <textarea id="research-q" className="input" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What do you want to know? Be specific: scope, timeframe, region…" />
        <div className="row wrap">
          <div style={{ width: 220 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
          <label className="row small muted">Depth
            <select className="input" style={{ width: 150 }} value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
              <option value={1}>Quick (1 round)</option><option value={3}>Standard (up to 2 rounds)</option><option value={5}>Deep (up to 3 rounds)</option>
            </select>
          </label>
          {projects.length > 0 && <label className="row small muted">Project
            <select className="input" style={{ width: 170 }} value={projectId} onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : "")}>
              <option value="">None</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
          <div className="spacer" />
          <button className="btn primary" disabled={question.trim().length < 5}><Search size={15} /> Start research</button>
        </div>
      </form>

      <div style={{ display: "grid", gridTemplateColumns: "280px minmax(0,1fr)", gap: 16 }} className="research-layout">
        <div className="card flush" style={{ alignSelf: "start" }}>
          {items.length === 0 && <Empty icon={<Search size={22} />} title="No research yet" hint="Your reports will be listed here." />}
          {items.map((r) => (
            <div key={r.id} className={`list-item ${current?.id === r.id ? "active" : ""}`} onClick={() => navigate(`/research/${r.id}`)}>
              <div className="grow">
                <div className="truncate">{r.question}</div>
                <div className="row small faint">{r.status === "running" ? <Loader2 size={11} className="spin" /> : null}{r.status} · {relTime(r.created_at)}</div>
              </div>
              <button className="btn ghost sm icon" aria-label="Delete research" title="Delete research" onClick={(e) => { e.stopPropagation(); if (confirm(`Delete "${r.question.slice(0, 80)}"?`)) remove(r.id); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
        <div>
          {!current && <div className="card"><Empty icon={<FileText size={24} />} title="Pick a report or start a new one" /></div>}
          {current && (
            <div className="card col gap-lg">
              <div className="row" style={{ alignItems: "flex-start" }}>
                <h2 className="grow">{current.question}</h2>
              </div>
              {current.status === "done" && (
                <div className="row wrap">
                  {projects.length > 0 && !current.project_id && <select className="input" style={{ width: 170 }} aria-label="Save into a project" value={saveTo}
                    onChange={(e) => setSaveTo(e.target.value ? Number(e.target.value) : "")}><option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>}
                  <button className="btn sm" onClick={save}><FileText size={14} /> Save as a document{current.project_id || saveTo ? " in the project" : ""}</button>
                  <button className="btn sm ghost" onClick={() => download(`${current.question.slice(0, 60).replace(/[^\w .-]+/g, "_")}.md`, current.report, "text/markdown")}>Report (.md)</button>
                  {current.evidence?.length > 0 && <button className="btn sm ghost" onClick={() => download("evidence.csv", evidenceCsv(current.evidence), "text/csv")}>Evidence (.csv)</button>}
                  <div className="spacer" />
                  {current.monitor ? <button className="btn sm" onClick={() => follow(false)}><BellOff size={14} /> Stop following</button> : <>
                    <select className="input" style={{ width: 150 }} aria-label="How often to look for new results" value={cron} onChange={(e) => setCron(e.target.value)}>
                      <option value="0 8 * * 1">Weekly, Monday 8:00</option><option value="0 8 * * *">Daily, 8:00</option><option value="0 8 1 * *">Monthly, 1st</option></select>
                    <button className="btn sm" onClick={() => follow(true)} title="A check-in searches this question on a schedule and tells you only about new results"><BellRing size={14} /> Follow for new results</button></>}
                </div>
              )}
              <div className="faint small mono">{current.model}</div>
              {current.status === "running" && <div className="row wrap muted" role="status"><Loader2 size={15} className="spin" /> <span className="grow">{current.progress}</span><button type="button" className="btn ghost sm" onClick={cancel}><X size={13} /> Cancel</button></div>}
              {(current.status === "error" || current.status === "cancelled") && <div className={`alert ${current.status === "error" ? "err" : ""} row wrap`}><span className="grow">{current.error || "Cancelled."}</span><button type="button" className="btn sm" onClick={retry}><RotateCcw size={13} /> {current.status === "error" ? "Retry" : "Run again"}</button></div>}
              {current.plan && current.plan.length > 0 && (
                <details><summary className="small muted" style={{ cursor: "pointer" }}>Research plan: {current.plan.length} sub-questions</summary>
                  <ul className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>{current.plan.map((p, i) => <li key={i}>{p.q} <span className="muted">({p.queries.join("; ")})</span></li>)}</ul></details>
              )}
              {current.rounds && current.rounds.length > 0 && (
                <details open={current.status === "running"}><summary className="small muted" style={{ cursor: "pointer" }}>
                  {current.rounds.length} of up to {current.max_rounds || current.stats?.max_rounds || current.rounds.length} round(s){current.stats?.stopped_because ? `; stopped because ${current.stats.stopped_because}` : ""}</summary>
                  <ol className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>{current.rounds.map((r) => (
                    <li key={r.round}>Searched {r.queries.length} quer{r.queries.length === 1 ? "y" : "ies"}, read {r.pages_read} page(s), {r.new_sources} new source(s), {r.claims} claim(s).
                      {r.thin.length > 0 && <span className="muted"> Still thin: {r.thin.join("; ")}.</span>}
                      {r.gaps.length > 0 && <span className="muted"> Gaps: {r.gaps.join("; ")}.</span>}</li>))}</ol></details>
              )}
              {current.status === "done" && <Markdown text={current.report} />}
              {current.status === "done" && current.evidence && current.evidence.length > 0 && <EvidenceTable rows={current.evidence} />}
              {current.stats && current.stats.sources_used !== undefined && (
                <div className="small muted">Read {current.stats.sources_read} pages, used {current.stats.sources_used}, skipped {current.stats.skipped}. {current.stats.claims} quoted claims
                  {current.stats.claims_dropped_unquoted ? `; ${current.stats.claims_dropped_unquoted} discarded because their quote was not on the page` : ""}.
                  {current.stats.uncited_sources && current.stats.uncited_sources.length > 0 ? ` Sources not cited: ${current.stats.uncited_sources.join(", ")}.` : ""}</div>
              )}
              {current.sources.length > 0 && (
                <div className="col" style={{ gap: 6 }}>
                  <h3>Inspect sources {current.status !== "done" && <span className="muted small">(so far)</span>}</h3>
                  {current.sources.map((s, i) => <SourceCard key={s.url} s={s} i={i} />)}
                </div>
              )}
              {current.skipped && current.skipped.length > 0 && (
                <details><summary className="small muted" style={{ cursor: "pointer" }}>{current.skipped.length} pages not used</summary>
                  <ul className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>{current.skipped.map((k) => <li key={k.url}><a href={k.url} target="_blank" rel="noreferrer noopener">{k.title || k.url}</a>: {k.why}</li>)}</ul></details>
              )}
              {current.status === "done" && <div className="muted small">The report separates what sources say from inferences. Citations to sources that do not exist are removed automatically, but check important claims against the sources before relying on them.</div>}
            </div>
          )}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
