import { ArrowLeft, Eye, FileText, Loader2, NotebookPen, Pencil, Pin, Plus, Search, Sparkles, Trash2, Wand2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Doc, stream } from "../api";
import { Empty, Markdown, ModelSelect, relTime, SkeletonList, useModels, useToast } from "../util";

const ACTIONS = [
  { id: "improve", label: "Improve writing" },
  { id: "fix", label: "Fix grammar" },
  { id: "shorten", label: "Make shorter" },
  { id: "expand", label: "Expand" },
  { id: "summarize", label: "Summarize" },
  { id: "continue", label: "Continue writing" },
  { id: "outline", label: "Draft outline" },
] as const;

function Editor({ doc, onSaved, onDeleted }: { doc: Doc; onSaved: (d: Doc) => void; onDeleted: () => void }) {
  const [title, setTitle] = useState(doc.title);
  const [body, setBody] = useState(doc.body);
  const [tags, setTags] = useState(doc.tags);
  const [preview, setPreview] = useState(false);
  const [saving, setSaving] = useState<"idle" | "pending" | "saving" | "saved">("idle");
  const [assist, setAssist] = useState<{ action: string; result: string; busy: boolean; thinking?: boolean; range: [number, number] } | null>(null);
  const [instruction, setInstruction] = useState("");
  const [model, setModel] = useState("");
  const { models } = useModels();
  const textRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const toast = useToast();
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setSaving("pending");
    const t = setTimeout(async () => {
      setSaving("saving");
      try {
        const d = await api<Doc>(`/docs/${doc.id}`, { method: "PATCH", body: { title, body, tags } });
        onSaved(d);
        setSaving("saved");
      } catch (e) {
        toast.err(e);
        setSaving("idle");
      }
    }, 700);
    return () => clearTimeout(t);
  }, [title, body, tags]);

  const runAssist = async (action: string) => {
    const el = textRef.current;
    let [start, end] = el && !preview ? [el.selectionStart, el.selectionEnd] : [0, 0];
    if (start === end || action === "continue" || action === "outline") [start, end] = [0, body.length];
    const text = action === "outline" && !body.trim() ? title : body.slice(start, end);
    if (!text.trim() && action !== "custom") return toast.err("Write something first, or select text to work on.");
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setAssist({ action, result: "", busy: true, range: action === "continue" ? [body.length, body.length] : [start, end] });
    let out = "";
    await stream("/ai/assist", { action, text: text || title, instruction, model: model || null }, (e) => {
      if (e.error) toast.err(String(e.error));
      if (e.reasoning) setAssist((a) => (a && !a.thinking ? { ...a, thinking: true } : a));
      if (e.delta) {
        out += e.delta;
        setAssist((a) => (a ? { ...a, result: out } : a));
      }
    }, ctrl.signal).catch(() => undefined);
    setAssist((a) => (a ? { ...a, busy: false } : a));
  };

  const apply = (mode: "replace" | "insert") => {
    if (!assist) return;
    const [s, e] = assist.range;
    const result = assist.result.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
    setBody(mode === "replace" ? body.slice(0, s) + result + body.slice(e) : body.slice(0, e) + (body.slice(0, e).endsWith("\n") ? "\n" : "\n\n") + result + body.slice(e));
    setAssist(null);
  };

  const togglePin = async () => onSaved(await api<Doc>(`/docs/${doc.id}`, { method: "PATCH", body: { pinned: !doc.pinned } }));
  const remove = async () => {
    if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
    await api(`/docs/${doc.id}`, { method: "DELETE" });
    onDeleted();
  };
  const words = body.trim() ? body.trim().split(/\s+/).length : 0;

  return (
    <>
      <div className="toolbar">
        <button className="btn ghost sm icon mobile-only" onClick={onDeleted} aria-label="Back"><ArrowLeft size={15} /></button>
        <div className="tabs">
          <button className={`tab ${!preview ? "active" : ""}`} onClick={() => setPreview(false)}><Pencil size={13} /> Write</button>
          <button className={`tab ${preview ? "active" : ""}`} onClick={() => setPreview(true)}><Eye size={13} /> Preview</button>
        </div>
        <div style={{ width: 180 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
        <select className="input" style={{ width: 170 }} value="" onChange={(e) => e.target.value && runAssist(e.target.value)}>
          <option value="">✨ AI assist…</option>
          {ACTIONS.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
        <div className="spacer" />
        <span className="faint small">{saving === "saving" ? "Saving…" : saving === "pending" ? "Edited" : saving === "saved" ? "Saved" : ""} · {words} words</span>
        <button className={`btn ghost sm icon ${doc.pinned ? "prio-1" : ""}`} onClick={togglePin} title="Pin"><Pin size={15} /></button>
        <button className="btn ghost sm icon danger" onClick={remove} title="Delete"><Trash2 size={15} /></button>
      </div>
      <div style={{ padding: "24px 32px", maxWidth: 860, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", gap: 10, flex: 1 }}>
        <input className="editor-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Untitled" />
        <input className="input" style={{ border: "none", padding: 0, height: 24, background: "transparent", color: "var(--muted)" }} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Add tags, comma separated" />
        <div className="row">
          <input className="input grow" value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Ask AI to change this (e.g. 'make it more formal', 'add a conclusion')…"
            onKeyDown={(e) => { if (e.key === "Enter" && instruction.trim()) runAssist("custom"); }} />
          <button className="btn" disabled={!instruction.trim()} onClick={() => runAssist("custom")}><Wand2 size={15} /> Apply</button>
        </div>
        {assist && (
          <div className="card col" style={{ borderColor: "var(--accent)" }}>
            <div className="row">
              <Sparkles size={15} className="prio-1" />
              <strong>{ACTIONS.find((a) => a.id === assist.action)?.label ?? "Custom edit"}</strong>
              {assist.busy && <Loader2 size={14} className="spin" />}
              <div className="spacer" />
              <button className="btn ghost sm icon" onClick={() => { abortRef.current?.abort(); setAssist(null); }}><X size={15} /></button>
            </div>
            <Markdown text={assist.result.replace(/<think>[\s\S]*?(<\/think>|$)/g, "") || (assist.thinking ? "*Thinking…*" : "…")} />
            {!assist.busy && assist.result && (
              <div className="row">
                <button className="btn primary sm" onClick={() => apply("replace")}>{assist.action === "continue" ? "Insert" : "Replace"}</button>
                {assist.action !== "continue" && <button className="btn sm" onClick={() => apply("insert")}>Insert below</button>}
                <button className="btn ghost sm" onClick={() => setAssist(null)}>Discard</button>
              </div>
            )}
          </div>
        )}
        {preview ? <Markdown text={body || "*Nothing here yet.*"} /> : (
          <textarea ref={textRef} className="editor-body" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Start writing. Markdown supported. Select text before using AI assist to work on just that part." />
        )}
      </div>
      {toast.node}
    </>
  );
}

export default function Docs({ kind }: { kind: "note" | "document" }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [list, setList] = useState<Doc[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [q, setQ] = useState("");
  const [current, setCurrent] = useState<Doc | null>(null);
  const toast = useToast();
  const base = kind === "note" ? "/notes" : "/documents";
  const Icon = kind === "note" ? NotebookPen : FileText;

  const load = () => api<Doc[]>(`/docs?kind=${kind}&q=${encodeURIComponent(q)}`).then(setList).catch(toast.err).finally(() => setLoaded(true));
  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [q, kind]);
  useEffect(() => {
    if (!id) return setCurrent(null);
    api<Doc>(`/docs/${id}`).then(setCurrent).catch(() => navigate(base));
  }, [id, kind]);

  const create = async () => {
    const d = await api<Doc>("/docs", { body: { kind, title: kind === "note" ? "New note" : "Untitled document" } });
    setList((l) => [d, ...l]);
    navigate(`${base}/${d.id}`);
  };

  return (
    <div className={`split ${current ? "has-selection" : ""}`}>
      <div className="split-list">
        <div className="col" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <div className="row">
            <h2 className="grow">{kind === "note" ? "Notes" : "Documents"}</h2>
            <button className="btn primary sm" onClick={create}><Plus size={14} /> New</button>
          </div>
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} />
            <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" />
          </div>
        </div>
        <div className="scroll">
          {!loaded && <SkeletonList rows={4} />}
          {loaded && list.length === 0 && <Empty icon={<Icon size={22} />} title={q ? "No matches" : `No ${kind}s yet`} />}
          {list.map((d) => (
            <div key={d.id} className={`list-item ${current?.id === d.id ? "active" : ""}`} onClick={() => navigate(`${base}/${d.id}`)} style={{ alignItems: "flex-start" }}>
              <div className="grow">
                <div className="row"><strong className="truncate grow">{d.title}</strong>{!!d.pinned && <Pin size={12} className="prio-1" />}</div>
                <div className="muted small truncate">{d.preview?.replace(/[#*_>`]/g, "") || "Empty"}</div>
                <div className="row small faint">{relTime(d.updated_at)}{d.tags && <span className="truncate">· {d.tags}</span>}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main">
        {current ? (
          <Editor key={current.id} doc={current} onSaved={(d) => { setCurrent(d); setList((l) => l.map((x) => (x.id === d.id ? { ...x, ...d, preview: d.body.slice(0, 240) } : x))); }}
            onDeleted={() => { setCurrent(null); navigate(base); load(); }} />
        ) : (
          <Empty icon={<Icon size={28} />} title={`Select or create a ${kind}`} hint={kind === "document" ? "Draft long form writing with AI assist. Research reports can be saved here." : "Quick thoughts, meeting notes, ideas. Agents in Chat can search and add to these."} />
        )}
      </div>
      {toast.node}
    </div>
  );
}
