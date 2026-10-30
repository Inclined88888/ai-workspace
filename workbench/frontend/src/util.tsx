import DOMPurify from "dompurify";
import katex from "katex";
import "katex/dist/katex.min.css";
import { marked } from "marked";
import { Check, X } from "lucide-react";
import { MouseEvent, useEffect, useRef, useState } from "react";
import { api, Model } from "./api";
import { motionOff } from "./appearance";

marked.setOptions({ gfm: true, breaks: true });

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
// Fenced code gets a header with the language and a copy button (handled by onCopyClick below; no inline JS).
marked.use({
  renderer: {
    code({ text, lang }) {
      const language = (lang || "").split(/\s/)[0];
      return `<div class="code-block"><div class="code-head"><span>${esc(language || "code")}</span><button type="button" class="code-copy" aria-label="Copy code">Copy</button></div><pre><code>${esc(text)}</code></pre></div>`;
    },
  },
});

// Maths: $$...$$ blocks and $...$ inline, rendered with KaTeX. "$5 and $10" stays text (no space inside the dollars,
// no digit right after the closing one).
const tex = (src: string, displayMode: boolean) => {
  try {
    return katex.renderToString(src, { displayMode, throwOnError: false, output: "htmlAndMathml", strict: "ignore" });
  } catch {
    return `<code>${esc(src)}</code>`;
  }
};
marked.use({
  extensions: [
    {
      name: "mathBlock", level: "block",
      start: (src: string) => src.match(/\$\$|\\\[/)?.index,
      tokenizer(src: string) {
        const m = /^\$\$([\s\S]+?)\$\$|^\\\[([\s\S]+?)\\\]/.exec(src);
        return m ? { type: "mathBlock", raw: m[0], text: (m[1] ?? m[2]).trim() } : undefined;
      },
      renderer: (t) => `<div class="math-block">${tex((t as unknown as { text: string }).text, true)}</div>`,
    },
    {
      name: "mathInline", level: "inline",
      start: (src: string) => src.match(/\$|\\\(/)?.index,
      tokenizer(src: string) {
        const m = /^\$(?!\s)((?:\\.|[^$\\\n])+?)(?<!\s)\$(?!\d)|^\\\((.+?)\\\)/.exec(src);
        return m ? { type: "mathInline", raw: m[0], text: m[1] ?? m[2] } : undefined;
      },
      renderer: (t) => tex((t as unknown as { text: string }).text, false),
    },
  ],
});

function onCopyClick(e: MouseEvent<HTMLDivElement>) {
  const btn = (e.target as HTMLElement).closest(".code-copy");
  if (!btn) return;
  const code = btn.closest(".code-block")?.querySelector("pre")?.textContent ?? "";
  navigator.clipboard?.writeText(code).then(() => {
    btn.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1600);
  }).catch(() => undefined);
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const html = DOMPurify.sanitize(marked.parse(text || "", { async: false }) as string);
  return <div className={`prose ${className}`} onClick={onCopyClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

const reducedMotion = motionOff;

/** Reveals a freshly arrived answer progressively with a caret, like a live stream. Instant when motion is off or the OS asks for reduced motion. */
export function StreamingMarkdown({ text, onDone }: { text: string; onDone?: () => void }) {
  const [n, setN] = useState(() => (reducedMotion() ? text.length : 0));
  const done = useRef(false);
  useEffect(() => {
    if (n >= text.length) {
      if (!done.current) { done.current = true; onDone?.(); }
      return;
    }
    // Finish in ~0.9s whatever the length, at least 3 chars a frame
    const step = Math.max(3, Math.ceil(text.length / 54));
    const raf = requestAnimationFrame(() => setN((x) => Math.min(text.length, x + step)));
    return () => cancelAnimationFrame(raf);
  }, [n, text]);
  return <Markdown text={text.slice(0, n)} className={n < text.length ? "streaming" : ""} />;
}

export function Skeleton({ w = "100%", h = 12, style }: { w?: number | string; h?: number; style?: React.CSSProperties }) {
  return <span className="skeleton" aria-hidden="true" style={{ width: w, height: h, ...style }} />;
}

/** Placeholder rows while a list loads. */
export function SkeletonList({ rows = 4, avatar = false }: { rows?: number; avatar?: boolean }) {
  return (
    <div role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row">
          {avatar && <Skeleton w={18} h={18} style={{ borderRadius: 6, flex: "none" }} />}
          <div className="grow col" style={{ gap: 7 }}>
            <Skeleton w={`${62 - ((i * 13) % 30)}%`} h={11} />
            <Skeleton w={`${34 - ((i * 7) % 14)}%`} h={9} />
          </div>
        </div>
      ))}
    </div>
  );
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
}: {
  value: string;
  onChange: (v: string) => void;
  models: Model[];
  allowDefault?: boolean;
}) {
  const groups = new Map<string, Model[]>();
  models.forEach((m) => groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]));
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowDefault && <option value="">Default model</option>}
      {[...groups.entries()].map(([provider, list]) => (
        <optgroup key={provider} label={provider}>
          {list.map((m) => (
            <option key={m.ref} value={m.ref}>
              {m.model}{m.free ? "  (free)" : ""}{reliabilityLabel(m)}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

// Measured on this workspace (receipts.py): share of tool calls that worked and passed their check
export function reliabilityLabel(m: Model): string {
  const r = m.reliability;
  if (!r || !r.calls || r.success_rate === null) return "";
  return `  · ${Math.round(r.success_rate * 100)}% of ${r.calls} tool calls ok`;
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
  const [msg, setMsg] = useState<{ text: string; kind: "ok" | "err"; key: number } | null>(null);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (!msg) return;
    setLeaving(false);
    const a = setTimeout(() => setLeaving(true), 3800);
    const t = setTimeout(() => setMsg(null), 4000);
    return () => { clearTimeout(a); clearTimeout(t); };
  }, [msg]);
  const node = msg ? (
    <div key={msg.key} className={`toast ${msg.kind} ${leaving ? "leaving" : ""}`} role={msg.kind === "err" ? "alert" : "status"}>
      <span className="toast-icon" aria-hidden="true">{msg.kind === "err" ? <X size={12} strokeWidth={3} /> : <Check size={12} strokeWidth={3} />}</span>
      <span>{msg.text}</span>
    </div>
  ) : null;
  return {
    node,
    ok: (text: string) => setMsg({ text, kind: "ok", key: Date.now() }),
    err: (e: unknown) => setMsg({ text: e instanceof Error ? e.message : String(e), kind: "err", key: Date.now() }),
  };
}

export function Empty({ icon, title, hint, hero = false, children }: { icon?: React.ReactNode; title: string; hint?: string; hero?: boolean; children?: React.ReactNode }) {
  return (
    <div className={`empty ${hero ? "hero" : ""}`}>
      {icon && <div className="empty-icon" aria-hidden="true">{icon}</div>}
      <div className="empty-title">{title}</div>
      {hint && <div className="muted">{hint}</div>}
      {children}
    </div>
  );
}

/** What the smart router reports with each answer (router.py). */
export type RouterQuota = {
  used_today: number; free: boolean; resets_at: string; note?: string; limit?: number; remaining?: number | null;
  window?: "day" | "minute"; estimated?: boolean; cooling_until?: string; cooling_reason?: string;
};
export type RouterInfo = {
  provider: string; label?: string; model: string; tier: "fast" | "strong"; subscription?: boolean;
  fallbacks: { provider: string; model: string; reason: string }[]; quota?: RouterQuota;
  resting?: { provider: string; reason: string; until: string }[];
};

const n = (x: number) => x.toLocaleString();

export function resetTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "812 of 1,000 free requests left today", or the honest fallback when the provider doesn't say. */
export function quotaText(q?: RouterQuota, subscription?: boolean): string {
  if (!q) return "";
  if (subscription) return "on your plan";
  if (q.limit != null && q.remaining != null && q.window === "day")
    return `${q.estimated ? "about " : ""}${n(q.remaining)} of ${n(q.limit)}${q.free ? " free" : ""} requests left today`;
  if (q.limit != null && q.remaining != null) return `${n(q.used_today)} used today · ${n(q.remaining)} of ${n(q.limit)} left this minute`;
  return `${n(q.used_today)} used today${q.free ? " · free tier" : ""}`;
}

/** The "via" line under an answer: who answered, how much free quota is left, and what was skipped. */
export function RouterVia({ r }: { r: RouterInfo }) {
  const short = r.model.split("/").pop();
  return (
    <div className="via" title={r.quota?.note || undefined}>
      <span className="via-who">via <b>{r.provider}</b> · {short}</span>
      <span className="via-tier">{r.tier}</span>
      {r.quota && <span className="via-quota">{quotaText(r.quota, r.subscription)}</span>}
      {r.fallbacks.map((f, i) => (
        <span key={i} className="via-skip">{f.provider} {f.reason}{f.reason === "daily limit reached" ? ", switched" : ", skipped"}</span>
      ))}
      {(r.resting || []).map((x, i) => (
        <span key={`r${i}`} className="via-skip">{x.provider} resting until {resetTime(x.until)} ({x.reason})</span>
      ))}
    </div>
  );
}
