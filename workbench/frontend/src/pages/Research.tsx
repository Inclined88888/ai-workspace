import { ExternalLink, FileText, Loader2, Search, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Tracer } from "../Tracer";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Item = { id: number; question: string; model: string; status: "running" | "done" | "error"; progress: string; created_at: string };
type Full = Item & { report: string; error: string; sources: { title: string; url: string; snippet: string }[] };

export default function Research() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [items, setItems] = useState<Item[]>([]);
  const [current, setCurrent] = useState<Full | null>(null);
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
  }, [id]);

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
  const remove = async (rid: number) => {
    await api(`/research/${rid}`, { method: "DELETE" });
    if (current?.id === rid) navigate("/research");
    loadList();
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Research</h1><p className="muted">Plans searches, reads the pages and writes a cited report. Runs on your SearXNG, nothing is sent to a search company directly.</p></div>
      </div>
      <form className="card col" onSubmit={start} style={{ marginBottom: 20 }}>
        <textarea className="input" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What do you want to know? Be specific: scope, timeframe, region…" />
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
          {items.length === 0 && <Empty icon={<Search size={22} />} title="No research yet" />}
          {items.map((r) => (
            <div key={r.id} className={`list-item has-tracer ${current?.id === r.id ? "active" : ""}`} onClick={() => navigate(`/research/${r.id}`)}>
              <Tracer active={r.status === "running"} />
              <div className="grow">
                <div className="truncate">{r.question}</div>
                <div className="row small faint">{r.status === "running" ? <Loader2 size={11} className="spin" /> : null}{r.status} · {relTime(r.created_at)}</div>
              </div>
              <button className="btn ghost sm icon" onClick={(e) => { e.stopPropagation(); remove(r.id); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
        <div>
          {!current && <div className="card"><Empty icon={<FileText size={24} />} title="Pick a report or start a new one" /></div>}
          {current && (
            <div className="card col gap-lg has-tracer">
              <Tracer active={current.status === "running"} label="Research in progress" />
              <div className="row" style={{ alignItems: "flex-start" }}>
                <h2 className="grow">{current.question}</h2>
                {current.status === "done" && <button className="btn sm" onClick={save}><FileText size={14} /> Save to documents</button>}
              </div>
              <div className="faint small mono">{current.model}</div>
              {current.status === "running" && <div className="row muted"><Loader2 size={15} className="spin" /> {current.progress}</div>}
              {current.status === "error" && <div className="alert err">{current.error}</div>}
              {current.status === "done" && <Markdown text={current.report} />}
              {current.status !== "done" && current.sources.length > 0 && (
                <div className="col">
                  <h3>Sources found</h3>
                  {current.sources.map((s, i) => (
                    <a key={s.url} href={s.url} target="_blank" rel="noreferrer noopener" className="row small"><span className="badge">{i + 1}</span><span className="truncate">{s.title || s.url}</span><ExternalLink size={11} /></a>
                  ))}
                </div>
              )}
              {current.status === "done" && <div className="muted small">Check claims against the linked sources before relying on them. Small local models can misread pages.</div>}
            </div>
          )}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
