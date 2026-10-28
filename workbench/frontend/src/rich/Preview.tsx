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

type Table = { header: string[]; rows: string[][]; total_rows: number; shown_rows: number; sheet: string };
type Loaded = { title: string; body: string; mono?: boolean; download?: string; open: string; note?: string;
  image?: string; pdf?: string; table?: Table; check?: { matches: boolean; text: string } };

async function load(item: PreviewItem): Promise<Loaded> {
  if (item.kind === "doc") {
    const d = await api<{ title: string; body: string; kind: string }>(`/docs/${item.id}`);
    return { title: d.title || "Untitled", body: d.body || "(This document is empty.)", open: `/${d.kind === "document" ? "documents" : "notes"}/${item.id}` };
  }
  if (item.kind === "file") {
    const f = await api<{ name: string; mime: string; text: string; note: string }>(`/files/${item.id}`);
    // A preview is not proof that a file was saved: the check reads it from disk and compares it with what was written
    const check = await api<{ matches: boolean; text: string }>(`/v1/files/${item.id}/verify`).catch(() => undefined);
    const base = { title: f.name, body: "", download: `/api/files/${item.id}/download`, open: `/files/${item.id}`, check };
    if (/^image\/(png|jpeg|gif|webp)$/.test(f.mime)) return { ...base, image: `/api/v1/files/${item.id}/view` };
    if (f.mime === "application/pdf") return { ...base, pdf: `/api/v1/files/${item.id}/view`, body: f.text };
    if (/\.(csv|tsv|xlsx)$/i.test(f.name)) {
      const table = await api<Table>(`/v1/files/${item.id}/table`).catch(() => undefined);
      if (table) return { ...base, table };
    }
    return { ...base, body: f.text || f.note || "No text could be read from this file.", mono: !/\.(md|markdown)$/i.test(f.name),
      note: f.text ? "" : f.note };
  }
  const r = await api<{ question: string; report: string; status: string; progress: string; sources?: unknown[] }>(`/research/${item.id}`);
  const n = Array.isArray(r.sources) ? r.sources.length : 0;
  return { title: r.question, body: r.report || (r.status === "running" ? `Still working: ${r.progress}` : "No report was written."),
    open: `/research/${item.id}`, note: n ? `${n} source${n === 1 ? "" : "s"}; the full evidence table is on the research page.` : "" };
}

function PreviewTable({ t }: { t: Table }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="preview-table-wrap" tabIndex={0} role="region" aria-label="Spreadsheet rows">
        <table className="rtable preview-table">
          <thead><tr>{t.header.map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
          <tbody>{t.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <div className="small faint">{t.sheet ? `Sheet ${t.sheet}. ` : ""}{t.shown_rows < t.total_rows ? `Showing ${t.shown_rows} of ${t.total_rows} rows.` : `${t.total_rows} rows.`}</div>
    </div>
  );
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
          : data.image ? <img className="preview-image" src={data.image} alt={data.title} />
          : data.pdf ? <iframe className="preview-pdf" src={data.pdf} title={`${data.title} (PDF)`} />
          : data.table ? <PreviewTable t={data.table} />
          : data.mono ? <pre className="preview-text">{data.body}</pre>
          : <RichText text={data.body} />}
        {data?.check && <p className={`small preview-check ${data.check.matches ? "ok" : "warn"}`} role="status">{data.check.text}</p>}
        {data?.note && <p className="small faint">{data.note}</p>}
      </div>
    </aside>
  );
}
