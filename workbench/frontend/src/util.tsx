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
  defaultLabel = "Automatic",
}: {
  value: string;
  onChange: (v: string) => void;
  models: Model[];
  allowDefault?: boolean;
  defaultLabel?: string;
}) {
  // One picker everywhere. Ready: a real request with a tool attached succeeded. Not tested yet: listed by the provider
  // but never tried, so it may not work (it can still be picked, in its own group). Not available: the AI mode does
  // not allow it; listed, disabled, with the reason as a tooltip, so nothing paid is chosen by accident.
  const blocked = models.filter((m) => m.allowed === false);
  const ready = models.filter((m) => m.allowed !== false && m.status !== "untested" && m.status !== "experimental");
  const untested = models.filter((m) => m.allowed !== false && m.status === "untested");
  const experimental = models.filter((m) => m.allowed !== false && m.status === "experimental");
  const groups = new Map<string, Model[]>();
  ready.forEach((m) => groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]));
  const sorted = (list: Model[]) => [...list].sort((a, b) => Number(!!b.free) - Number(!!a.free));
  const current = value ? models.find((m) => m.ref === value) : undefined;
  return (
    <select className="input" aria-label="AI model" title="AI model" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowDefault && <option value="">{defaultLabel}</option>}
      {value && !current && <option value={value}>{value.replace(/^\d+\//, "")} (no longer offered by its provider)</option>}
      {value && current?.allowed === false && <option value={value}>{current.model} (not usable in this AI mode)</option>}
      {[...groups.entries()].map(([provider, list]) => (
        <optgroup key={provider} label={provider}>
          {sorted(list).map((m) => <option key={m.ref} value={m.ref}>{m.model}{m.free ? "  (free)" : ""}</option>)}
        </optgroup>
      ))}
      {untested.length > 0 && (
        <optgroup label="Not tested yet (may not work)">
          {sorted(untested).slice(0, 200).map((m) => (
            <option key={m.ref} value={m.ref} title="Never tried. Test it under Settings, AI models, All model connections.">
              {m.model} · {m.provider}{m.free ? "  (free)" : ""}
            </option>
          ))}
        </optgroup>
      )}
      {experimental.length > 0 && (
        <optgroup label="Experimental (plain answers, slow, no tools)">
          {experimental.map((m) => <option key={m.ref} value={m.ref}>{m.model} · {m.provider}</option>)}
        </optgroup>
      )}
      {blocked.length > 0 && (
        <optgroup label="Not available (reason on hover)">
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
