import { ExternalLink, FileText, Loader2, RotateCcw, Search, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Item = { id: number; question: string; model: string; status: "running" | "done" | "error" | "cancelled"; progress: string; created_at: string };
type Claim = { claim: string; quote: string; kind: string };
type Source = { n?: number; title: string; url: string; snippet: string; site?: string; date?: string; author?: string; credibility?: string; credibility_why?: string;
  freshness?: string; relevance?: number; score?: number; found_by?: string[]; duplicates?: string[]; claims?: Claim[] };
type Stats = { sources_read?: number; sources_used?: number; skipped?: number; claims?: number; claims_dropped_unquoted?: number; invalid_removed?: number;
  unsourced_findings?: number; uncited_sources?: number[]; seconds?: number };
type Full = Item & { report: string; error: string; sources: Source[]; plan: { q: string; queries: string[] }[]; skipped: { url: string; title: string; why: string }[]; stats: Stats };

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
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState(3);
  const [model, setModel] = useState("");
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
      const r = await api<Item>("/research", { body: { question, depth, model: model || null } });
      setQuestion("");
      loadList();
      navigate(`/research/${r.id}`);
    } catch (err) {
      toast.err(err);
    }
  };
  const save = async () => {
    try {
      const d = await api<{ id: number }>(`/research/${current!.id}/save`, { method: "POST" });
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
              <option value={1}>Quick (2 searches)</option><option value={3}>Standard (4)</option><option value={5}>Deep (6)</option>
            </select>
          </label>
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
                {current.status === "done" && <button className="btn sm" onClick={save}><FileText size={14} /> Save to documents</button>}
              </div>
              <div className="faint small mono">{current.model}</div>
              {current.status === "running" && <div className="row wrap muted" role="status"><Loader2 size={15} className="spin" /> <span className="grow">{current.progress}</span><button type="button" className="btn ghost sm" onClick={cancel}><X size={13} /> Cancel</button></div>}
              {(current.status === "error" || current.status === "cancelled") && <div className={`alert ${current.status === "error" ? "err" : ""} row wrap`}><span className="grow">{current.error || "Cancelled."}</span><button type="button" className="btn sm" onClick={retry}><RotateCcw size={13} /> {current.status === "error" ? "Retry" : "Run again"}</button></div>}
              {current.plan && current.plan.length > 0 && (
                <details><summary className="small muted" style={{ cursor: "pointer" }}>Research plan: {current.plan.length} sub-questions</summary>
                  <ul className="small" style={{ margin: "6px 0", paddingLeft: 18 }}>{current.plan.map((p, i) => <li key={i}>{p.q} <span className="muted">({p.queries.join("; ")})</span></li>)}</ul></details>
              )}
              {current.status === "done" && <Markdown text={current.report} />}
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
