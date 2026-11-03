// feat/attachments: files and images in the chat composer (attach button, drag and drop, paste) and in the history.
import { FileText, Paperclip, X } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "./api";
import "./attachments.css";

export type Att = { id: string; name: string; mime: string; size: number; kind: "image" | "document"; chars?: number; notes?: string[] };
type Pending = { key: string; name: string; preview?: string; att?: Att; error?: string };

export const MAX_FILES = 6;
export const MAX_BYTES = 10 * 1024 * 1024;

const kb = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function toBase64(f: File): Promise<string> {
  return new Promise((ok, bad) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => bad(r.error ?? new Error("Could not read the file"));
    r.readAsDataURL(f);
  });
}

/** Files waiting to go with the next message. Each uploads as soon as it is added. */
export function useAttachments() {
  const [items, setItems] = useState<Pending[]>([]);
  const add = (files: Iterable<File>) => {
    const list = [...files];
    setItems((cur) => {
      const room = MAX_FILES - cur.length;
      const next: Pending[] = [];
      list.forEach((f, i) => {
        const key = `${Date.now()}-${i}-${f.name}`;
        if (i >= room) { next.push({ key, name: f.name || "file", error: `At most ${MAX_FILES} files per message` }); return; }
        if (f.size > MAX_BYTES) { next.push({ key, name: f.name || "file", error: `Over ${kb(MAX_BYTES)}` }); return; }
        const preview = f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined;
        next.push({ key, name: f.name || (preview ? "pasted image.png" : "file"), preview });
        toBase64(f)
          .then((data) => api<Att>("/assistant/attachments", { body: { name: f.name || (preview ? "pasted image.png" : "file"), mime: f.type, data } }))
          .then((att) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, att } : x))))
          .catch((e: unknown) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, error: e instanceof Error ? e.message : String(e) } : x))));
      });
      return [...cur, ...next];
    });
  };
  const remove = (key: string) => setItems((xs) => { xs.find((x) => x.key === key)?.preview && URL.revokeObjectURL(xs.find((x) => x.key === key)!.preview!); return xs.filter((x) => x.key !== key); });
  const clear = () => setItems((xs) => { xs.forEach((x) => x.preview && URL.revokeObjectURL(x.preview)); return []; });
  const ready = items.filter((x) => x.att).map((x) => x.att!) as Att[];
  const uploading = items.some((x) => !x.att && !x.error);
  return { items, add, remove, clear, ready, uploading };
}

/** Image files from a paste or a drop (pasted screenshots have no name). */
export const filesFrom = (dt: DataTransfer | null): File[] => (dt ? [...dt.files] : []);

export function AttachButton({ onFiles, disabled }: { onFiles: (f: File[]) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className="btn ghost sm icon" aria-label="Attach files" title="Attach images or documents (or paste, or drop them here)"
        disabled={disabled} onClick={() => input.current?.click()}><Paperclip size={15} /></button>
      <input ref={input} type="file" multiple hidden aria-hidden="true"
        accept="image/png,image/jpeg,image/webp,image/gif,.pdf,.docx,.xlsx,.xls,.pptx,.csv,.tsv,.txt,.md,.json,.yaml,.yml,.xml,.html,.rtf,.odt,.py,.js,.ts,.tsx,.java,.go,.rs,.c,.cpp,.sql,.sh,.ipynb"
        onChange={(e) => { if (e.target.files) onFiles([...e.target.files]); e.target.value = ""; }} />
    </>
  );
}

export function PendingChips({ items, onRemove }: { items: Pending[]; onRemove: (key: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="att-row" aria-label="Attached files">
      {items.map((p) => (
        <div key={p.key} className={`att-chip${p.error ? " bad" : ""}${!p.att && !p.error ? " loading" : ""}`} title={p.error || p.att?.notes?.join(" ") || p.name}>
          {p.preview ? <img src={p.preview} alt="" /> : <FileText size={14} aria-hidden="true" />}
          <span className="truncate">{p.name}</span>
          {p.error && <span className="small">{p.error}</span>}
          <button type="button" className="btn ghost sm icon" aria-label={`Remove ${p.name}`} onClick={() => onRemove(p.key)}><X size={12} /></button>
        </div>
      ))}
    </div>
  );
}

/** Attachments on a sent message: image thumbnails (open full size) and document chips (download). */
export function MessageAttachments({ items }: { items?: Att[] }) {
  if (!items?.length) return null;
  return (
    <div className="att-row sent">
      {items.map((a) => {
        const url = `/api/assistant/attachments/${encodeURIComponent(a.id)}`;
        return a.kind === "image"
          ? <a key={a.id} href={url} target="_blank" rel="noreferrer" className="att-thumb" title={a.name}><img src={url} alt={a.name} loading="lazy" /></a>
          : <a key={a.id} href={url} className="att-chip" title={`${a.name}, ${kb(a.size)}`}><FileText size={14} aria-hidden="true" /><span className="truncate">{a.name}</span></a>;
      })}
    </div>
  );
}
