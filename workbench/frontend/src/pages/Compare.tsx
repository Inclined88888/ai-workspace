import { Clock, Columns3, Copy, Loader2, Plus, RotateCcw, Send, Square, X } from "lucide-react";
import { FormEvent, useRef, useState } from "react";
import { stream, StreamEvent } from "../api";
import { Empty, Markdown, ModelSelect, useModels, useToast } from "../util";

type Turn = { role: "user" | "assistant"; content: string };
type Column = { id: number; model: string; history: Turn[]; live: string; thinking: string; busy: boolean; error: string; stats: { ms?: number; first?: number | null; tokens?: number } };

let nextId = 1;
const blank = (model = ""): Column => ({ id: nextId++, model, history: [], live: "", thinking: "", busy: false, error: "", stats: {} });
const clean = (t: string) => t.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trim();

export default function Compare() {
  const { models, loading } = useModels();
  const [cols, setCols] = useState<Column[]>(() => [blank(), blank()]);
  const [prompt, setPrompt] = useState("");
  const [system, setSystem] = useState("");
  const [showSystem, setShowSystem] = useState(false);
  const aborts = useRef(new Map<number, AbortController>());
  const toast = useToast();

  const update = (id: number, fn: (c: Column) => Column) => setCols((cs) => cs.map((c) => (c.id === id ? fn(c) : c)));

  const runOne = async (col: Column, text: string) => {
    const ctrl = new AbortController();
    aborts.current.set(col.id, ctrl);
    const history: Turn[] = [...col.history, { role: "user", content: text }];
    update(col.id, (c) => ({ ...c, history, live: "", thinking: "", busy: true, error: "", stats: {} }));
    let out = "";
    const onEvent = (e: StreamEvent) => {
      if (e.error) update(col.id, (c) => ({ ...c, error: String(e.error) }));
      if (e.reasoning) { const r = e.reasoning; update(col.id, (c) => ({ ...c, thinking: c.thinking + r })); }
      if (e.delta) { out += e.delta; update(col.id, (c) => ({ ...c, live: out })); }
      if (e.done) update(col.id, (c) => ({ ...c, stats: { ms: e.ms, first: e.first_token_ms, tokens: e.usage?.completion_tokens } }));
    };
    try {
      await stream("/compare/stream", { model: col.model, messages: history, system }, onEvent, ctrl.signal);
    } catch {
      /* aborted */
    }
    update(col.id, (c) => ({ ...c, busy: false, live: "", history: out ? [...history, { role: "assistant", content: clean(out) }] : history }));
  };

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const text = prompt.trim();
    if (!text) return;
    const ready = cols.filter((c) => c.model);
    if (ready.length < 1) return toast.err("Pick at least one model");
    ready.forEach((c) => runOne(c, text));
    setPrompt("");
  };

  const stopAll = () => aborts.current.forEach((a) => a.abort());
  const reset = () => { stopAll(); setCols((cs) => cs.map((c) => ({ ...blank(c.model), id: c.id }))); };
  const busy = cols.some((c) => c.busy);

  return (
    <div className="page wide">
      <div className="page-head">
        <div><h1>Compare models</h1><p className="muted">Send the same prompt to several models and read the answers side by side. Each column keeps its own conversation.</p></div>
        <div className="spacer" />
        <button className="btn" onClick={() => setShowSystem(!showSystem)}>System prompt</button>
        <button className="btn" onClick={reset} disabled={busy}><RotateCcw size={15} /> Reset</button>
        <button className="btn" disabled={cols.length >= 4} onClick={() => setCols([...cols, blank()])}><Plus size={15} /> Column</button>
      </div>
      {showSystem && <textarea className="input" rows={3} style={{ marginBottom: 12 }} value={system} onChange={(e) => setSystem(e.target.value)} placeholder="Optional system prompt applied to every model" />}
      {!loading && models.length === 0 && <div className="alert" style={{ marginBottom: 12 }}>No models found. Download a local model or add a provider in Settings.</div>}

      <div className="compare-cols">
        {cols.map((col) => (
          <div key={col.id} className="card compare-col">
            <div className="row" style={{ marginBottom: 8 }}>
              <div className="grow"><ModelSelect value={col.model} onChange={(v) => update(col.id, (c) => ({ ...c, model: v }))} models={models} allowDefault={false} /></div>
              {cols.length > 1 && <button className="btn ghost sm icon" onClick={() => { aborts.current.get(col.id)?.abort(); setCols(cols.filter((c) => c.id !== col.id)); }}><X size={15} /></button>}
            </div>
            <div className="out">
              {col.history.length === 0 && !col.busy && <Empty icon={<Columns3 size={20} />} title={col.model ? "Ready" : "Pick a model"} />}
              {col.history.map((t, i) => t.role === "user"
                ? <div key={i} className="bubble-user">{t.content}</div>
                : <div key={i} style={{ position: "relative" }}><Markdown text={t.content} /><button className="btn ghost sm icon" title="Copy" onClick={() => navigator.clipboard.writeText(t.content)} style={{ position: "absolute", top: -4, right: 0 }}><Copy size={12} /></button></div>)}
              {col.thinking && (
                <details className="thinking" open={col.busy && !col.live}>
                  <summary className="muted small">{col.busy && !col.live ? "Thinking…" : "Show reasoning"}</summary>
                  <div className="muted small" style={{ whiteSpace: "pre-wrap" }}>{col.thinking}</div>
                </details>
              )}
              {col.busy && (col.live ? <Markdown text={clean(col.live) || "…"} /> : !col.thinking && <div className="row muted"><Loader2 size={14} className="spin" /> Waiting for first token</div>)}
              {col.error && <div className="alert err">{col.error}</div>}
            </div>
            {(col.stats.ms || col.busy) && (
              <div className="row small faint" style={{ borderTop: "1px solid var(--border)", paddingTop: 8, marginTop: 8 }}>
                <Clock size={12} />
                {col.stats.ms ? <>{(col.stats.ms / 1000).toFixed(1)}s total{col.stats.first ? ` · ${(col.stats.first / 1000).toFixed(1)}s to first token` : ""}{col.stats.tokens ? ` · ${col.stats.tokens} tokens · ${Math.round(col.stats.tokens / (col.stats.ms / 1000))} tok/s` : ""}</> : "running…"}
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="card row" onSubmit={send} style={{ marginTop: 12, position: "sticky", bottom: 12 }}>
        <textarea className="input grow" rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Ask all models… (Enter to send, Shift+Enter for a new line)"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        {busy ? <button type="button" className="btn" onClick={stopAll}><Square size={15} /> Stop</button> : <button className="btn primary" disabled={!prompt.trim()}><Send size={15} /> Send</button>}
      </form>
      {toast.node}
    </div>
  );
}
