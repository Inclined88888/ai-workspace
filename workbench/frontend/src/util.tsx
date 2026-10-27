import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState } from "react";
import { api, Model } from "./api";

marked.setOptions({ gfm: true, breaks: true });

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const html = DOMPurify.sanitize(marked.parse(text || "", { async: false }) as string);
  return <div className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function useModels() {
  const [models, setModels] = useState<Model[]>([]);
  const [defaultRef, setDefaultRef] = useState("");
  const [loading, setLoading] = useState(true);
  const load = (fresh = false) => {
    setLoading(true);
    api<{ models: Model[]; default: string }>(`/models${fresh ? "?fresh=true" : ""}`)
      .then((r) => {
        setModels(r.models);
        setDefaultRef(r.default);
      })
      .catch(() => setModels([]))
      .finally(() => setLoading(false));
  };
  useEffect(() => load(), []);
  return { models, defaultRef, loading, reload: load };
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
  const groups = new Map<string, Model[]>();
  models.forEach((m) => groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]));
  return (
    <select className="input" aria-label="AI model" title="AI model" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowDefault && <option value="">{defaultLabel}</option>}
      {[...groups.entries()].map(([provider, list]) => (
        <optgroup key={provider} label={provider}>
          {list.map((m) => (
            <option key={m.ref} value={m.ref}>
              {m.model}{m.free ? "  (free)" : ""}
            </option>
          ))}
        </optgroup>
      ))}
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
