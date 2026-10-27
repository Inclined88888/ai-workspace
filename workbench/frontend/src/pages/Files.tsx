import { Download, FileText, FolderOpen, Loader2, MessageSquare, Search, Trash2, Upload } from "lucide-react";
import { DragEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, FileMeta, fileSize, uploadFile } from "../api";
import { Empty, relTime, useToast } from "../util";

type Detail = FileMeta & { text: string; total_chars: number; more: boolean };

export default function Files() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [items, setItems] = useState<FileMeta[]>([]);
  const [q, setQ] = useState("");
  const [current, setCurrent] = useState<Detail | null>(null);
  const [uploading, setUploading] = useState<string[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const load = () => api<FileMeta[]>(`/files${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setItems).catch(toast.err);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [q]);
  useEffect(() => {  // only the file clicked last may open, however the responses arrive
    if (!id) { setCurrent(null); return; }
    let gone = false;
    api<Detail>(`/files/${id}`).then((d) => { if (!gone) setCurrent(d); }).catch((e) => { if (!gone) toast.err(e); });
    return () => { gone = true; };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (list: FileList | File[]) => {
    const arr = [...list];
    if (!arr.length) return;
    setUploading(arr.map((f) => f.name));
    let last: FileMeta | null = null;
    for (const f of arr) {
      try { last = await uploadFile(f); toast.ok(last.chars ? `${f.name} uploaded and read` : `${f.name} stored. ${last.note}`); }
      catch (e) { toast.err(`${f.name}: ${(e as Error).message}`); }
    }
    setUploading([]);
    load();
    if (last && arr.length === 1) navigate(`/files/${last.id}`);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); };
  const remove = async (f: FileMeta) => {
    if (!confirm(`Delete ${f.name}? This can't be undone.`)) return;
    try { await api(`/files/${f.id}`, { method: "DELETE" }); if (current?.id === f.id) navigate("/files"); load(); } catch (e) { toast.err(e); }
  };

  return (
    <div className={`split ${current ? "has-selection" : ""}`} onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
      <div className="split-list">
        <div className="col" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <div className="row">
            <h1 className="grow" style={{ fontSize: 16 }}>Files</h1>
            <button className="btn primary sm" onClick={() => input.current?.click()} disabled={uploading.length > 0}>
              {uploading.length ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} Upload
            </button>
            <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) upload(e.target.files); e.target.value = ""; }} />
          </div>
          <div className="muted small">PDFs, Word documents and text files are read so ROOKS can search and answer questions about them. Up to 25 MB each.</div>
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} aria-hidden="true" />
            <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search inside files" aria-label="Search inside files" />
          </div>
        </div>
        <div className="scroll">
          {uploading.map((n) => <div key={n} className="list-item" style={{ cursor: "default" }}><Loader2 size={14} className="spin" /> <span className="truncate">Uploading {n}…</span></div>)}
          {items.length === 0 && !uploading.length && <Empty icon={<FolderOpen size={22} />} title={q ? "No matches" : "No files yet"} hint={q ? undefined : "Drop files here, or press Upload."} />}
          {items.map((f) => (
            <div key={f.id} className={`list-item ${current?.id === f.id ? "active" : ""}`}>
              <FileText size={15} className="faint" aria-hidden="true" />
              <Link className="list-link grow" to={`/files/${f.id}`} aria-current={current?.id === f.id ? "page" : undefined}>
                <div className="truncate">{f.name}</div>
                <div className="faint small truncate">{f.excerpt ? f.excerpt : <>{fileSize(f.size)} · {relTime(f.created_at)}{!f.chars ? " · no text" : ""}</>}</div>
              </Link>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main">
        {drag && <div className="drop-hint" aria-hidden="true">Drop to upload</div>}
        {current ? (
          <div className="page" style={{ maxWidth: 900, width: "100%" }}>
            <div className="page-head">
              <div className="grow" style={{ minWidth: 0 }}>
                <h1 className="truncate">{current.name}</h1>
                <p className="muted">{fileSize(current.size)} · uploaded {relTime(current.created_at)}{current.total_chars ? ` · ${current.total_chars.toLocaleString()} characters of text` : ""}</p>
              </div>
              <button className="btn mobile-only" onClick={() => navigate("/files")}>Back</button>
              <button className="btn primary" onClick={() => navigate("/assistant", { state: { ask: `Summarise ${current.name} and list anything I need to act on.`, files: [current.id], fileNames: [current.name] } })}><MessageSquare size={15} /> Ask ROOKS</button>
              <a className="btn" href={`/api/files/${current.id}/download`}><Download size={15} /> Download</a>
              <button className="btn ghost icon danger" onClick={() => remove(current)} aria-label={`Delete ${current.name}`} title="Delete"><Trash2 size={15} /></button>
            </div>
            {current.note && <div className="alert" style={{ marginBottom: 12 }}>{current.note}</div>}
            {current.text && (
              <div className="card">
                <pre className="file-text">{current.text}</pre>
                {current.more && <div className="muted small" style={{ marginTop: 8 }}>Showing the first 20,000 characters. ROOKS reads the whole file when you ask about it.</div>}
              </div>
            )}
          </div>
        ) : (
          <Empty icon={<FolderOpen size={28} />} title="Select a file" hint="Upload contracts, reports or notes, then ask ROOKS about them. Files you attach in chat show up here too." />
        )}
      </div>
      {toast.node}
    </div>
  );
}
