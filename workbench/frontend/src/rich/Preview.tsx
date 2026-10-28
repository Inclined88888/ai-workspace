import { Download, ExternalLink, Loader2, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import RichText from "./RichText";

/** Something produced in the conversation that can be looked at beside it: a document, a file, a research report. */
export type PreviewItem = { kind: "doc" | "file" | "research"; id: number; title?: string };

const Ctx = createContext<((item: PreviewItem) => void) | null>(null);
export const PreviewProvider = Ctx.Provider;
/** Opens the preview panel when the page has one; null elsewhere (cards then preview inline or link to the page). */
export function usePreview() {
  return useContext(Ctx);
}

type Loaded = { title: string; body: string; mono?: boolean; download?: string; open: string; note?: string };

async function load(item: PreviewItem): Promise<Loaded> {
  if (item.kind === "doc") {
    const d = await api<{ title: string; body: string; kind: string }>(`/docs/${item.id}`);
    return { title: d.title || "Untitled", body: d.body || "(This document is empty.)", open: `/${d.kind === "document" ? "documents" : "notes"}/${item.id}` };
  }
  if (item.kind === "file") {
    const f = await api<{ name: string; text: string; note: string }>(`/files/${item.id}`);
    const tabular = /\.(csv|tsv|xlsx)$/i.test(f.name);
    return { title: f.name, body: f.text || f.note || "No text could be read from this file.", mono: tabular || !/\.(md|markdown)$/i.test(f.name),
      download: `/api/files/${item.id}/download`, open: `/files/${item.id}`, note: f.text ? "" : f.note };
  }
  const r = await api<{ question: string; report: string; status: string; progress: string; sources?: unknown[] }>(`/research/${item.id}`);
  const n = Array.isArray(r.sources) ? r.sources.length : 0;
  return { title: r.question, body: r.report || (r.status === "running" ? `Still working: ${r.progress}` : "No report was written."),
    open: `/research/${item.id}`, note: n ? `${n} source${n === 1 ? "" : "s"}; the full evidence table is on the research page.` : "" };
}

export function PreviewPanel({ item, onClose }: { item: PreviewItem; onClose: () => void }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let stop = false;
    setData(null); setError("");
    load(item).then((d) => { if (!stop) setData(d); }).catch((e) => { if (!stop) setError((e as Error).message); });
    return () => { stop = true; };
  }, [item.kind, item.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const label = { doc: "Document", file: "File", research: "Research report" }[item.kind];
  return (
    <aside className="preview-panel" aria-label={`${label} preview`}>
      <div className="preview-head">
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="small faint">{label}</div>
          <h2 className="preview-title truncate" title={data?.title ?? item.title}>{data?.title ?? item.title ?? "Loading"}</h2>
        </div>
        {data?.download && <a className="btn ghost icon" href={data.download} download aria-label="Download" title="Download"><Download size={16} /></a>}
        {data && <Link className="btn ghost icon" to={data.open} aria-label="Open in its page" title="Open in its page"><ExternalLink size={16} /></Link>}
        <button ref={closeRef} type="button" className="btn ghost icon" onClick={onClose} aria-label="Close preview" title="Close (Esc)"><X size={16} /></button>
      </div>
      <div className="preview-body">
        {error ? <div className="alert err" role="alert">{error}</div>
          : !data ? <div className="row muted"><Loader2 size={14} className="spin" /> Loading…</div>
          : data.mono ? <pre className="preview-text">{data.body}</pre>
          : <RichText text={data.body} />}
        {data?.note && <p className="small faint">{data.note}</p>}
      </div>
    </aside>
  );
}
