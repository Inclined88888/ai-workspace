import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";
import { api, Model } from "./api";

marked.setOptions({ gfm: true, breaks: true });

// No images or media: reports and notes can hold text written by a model from web pages, and an image would load by
// itself with an address that can carry that text away.
const SAFE = { FORBID_TAGS: ["style", "form", "input", "button", "iframe", "object", "embed", "img", "picture", "source", "video", "audio"],
  FORBID_ATTR: ["style", "srcset"] };

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const html = DOMPurify.sanitize(marked.parse(text || "", { async: false }) as string, SAFE);
  return <div className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function useModels() {
  const [models, setModels] = useState<Model[]>([]);
  const [defaultRef, setDefaultRef] = useState("");
  const [mode, setMode] = useState("");
  const [loading, setLoading] = useState(true);
  const load = (fresh = false) => {
    setLoading(true);
    api<{ models: Model[]; default: string; mode?: string }>(`/models${fresh ? "?fresh=true" : ""}`)
      .then((r) => {
        setModels(r.models);
        setDefaultRef(r.default);
        setMode(r.mode ?? "");
      })
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => load(), []);
  return { models, defaultRef, mode, loading, reload: load };
}

export function ModelSelect({
  value,
  onChange,
  models,
  allowDefault = true,
  defaultLabel = "Default model",
}: {
  value: string;
  onChange: (v: string) => void;
  models: Model[];
  allowDefault?: boolean;
  defaultLabel?: string;
}) {
  // In Free Cloud AI and Local AI modes the models the mode allows come first; the others are listed but cannot be
  // picked, with the reason as a tooltip, so nothing paid is chosen by accident and nothing is hidden.
  const usable = models.filter((m) => m.allowed !== false);
  const blocked = models.filter((m) => m.allowed === false);
  const groups = new Map<string, Model[]>();
  usable.forEach((m) => groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]));
  const sorted = (list: Model[]) => (blocked.length ? [...list].sort((a, b) => Number(!!b.free) - Number(!!a.free)) : list);
  return (
    <select className="input" aria-label="AI model" title="AI model" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowDefault && <option value="">{defaultLabel}</option>}
      {value && blocked.some((m) => m.ref === value) && <option value={value}>{value.replace(/^\d+\//, "")} (not usable in this AI mode)</option>}
      {[...groups.entries()].map(([provider, list]) => (
        <optgroup key={provider} label={provider}>
          {sorted(list).map((m) => (
            <option key={m.ref} value={m.ref}>
              {m.model}{m.free ? "  (free)" : ""}
            </option>
          ))}
        </optgroup>
      ))}
      {blocked.length > 0 && (
        <optgroup label="Not available in this AI mode">
          {blocked.slice(0, 60).map((m) => (
            <option key={m.ref} value={m.ref} disabled title={m.blocked}>
              {m.model} · {m.provider}{m.free ? "" : "  (paid)"}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}

export function relTime(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString();
}

export function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function localDateISO(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function useToast() {
  const [msg, setMsg] = useState<{ text: string; kind: "ok" | "err" } | null>(null);
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 4000);
    return () => clearTimeout(t);
  }, [msg]);
  // Announced to screen readers: errors interrupt, confirmations wait their turn
  const node = msg ? <div className={`toast ${msg.kind}`} role={msg.kind === "err" ? "alert" : "status"} aria-live={msg.kind === "err" ? "assertive" : "polite"}>{msg.text}</div> : null;
  return {
    node,
    ok: (text: string) => setMsg({ text, kind: "ok" }),
    err: (e: unknown) => setMsg({ text: e instanceof Error ? e.message : String(e), kind: "err" }),
  };
}

export function Empty({ icon, title, hint, children }: { icon: React.ReactNode; title: string; hint?: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon" aria-hidden="true">{icon}</div>
      <div className="empty-title">{title}</div>
      {hint && <div className="muted" style={{ maxWidth: 420 }}>{hint}</div>}
      {children && <div className="row wrap" style={{ justifyContent: "center", marginTop: 8 }}>{children}</div>}
    </div>
  );
}
