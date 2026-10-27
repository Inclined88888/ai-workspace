// Structured answer blocks (schema version 1). The backend builds them from real tool results (app/blocks.py); the
// model never writes them. Everything arriving here is still treated as untrusted: each block is validated field by
// field, and anything that does not validate is dropped (the reply's text is still shown). Strings are only ever
// rendered as text, links only when they are http(s), and actions come from the kind and id, never from the data.

export const BLOCKS_VERSION = 1;

export type Format = "number" | "cents" | "percent" | "text";
export type Kind = "task" | "event" | "doc" | "mission" | "project" | "file" | "email" | "memory" | "schedule" | "research";
export const KINDS: Kind[] = ["task", "event", "doc", "mission", "project", "file", "email", "memory", "schedule", "research"];
export type Rec = Record<string, string | number | boolean | null>;

export type Block =
  | { type: "records"; kind: Kind; title: string; items: Rec[] }
  | { type: "table"; title: string; columns: { key: string; label: string; format?: Format }[]; rows: Rec[]; note?: string }
  | { type: "kpis"; items: { label: string; value: number; format: Format }[] }
  | { type: "chart"; kind: "bar" | "line" | "donut"; title: string; format: Format; points: { label: string; value: number }[] }
  | { type: "sources"; items: { n: number | null; title: string; url: string; site: string; snippet: string; date: string }[] }
  | { type: "list"; title: string; items: string[] }
  | { type: "notice"; tone: "info" | "warn"; text: string };

const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const FORMATS: Format[] = ["number", "cents", "percent", "text"];
const cap = <T,>(a: T[], n = 50) => a.slice(0, n);

export function safeUrl(u: unknown): string {
  if (!isStr(u)) return "";
  try {
    const p = new URL(u);
    return p.protocol === "http:" || p.protocol === "https:" ? p.href : "";
  } catch { return ""; }
}

/** Only scalar fields survive; nested objects and functions are dropped. */
function rec(v: unknown): Rec | null {
  if (!isObj(v)) return null;
  const out: Rec = {};
  for (const [k, x] of Object.entries(v)) {
    if (typeof x === "string") out[k] = x.slice(0, 2000);
    else if (isNum(x) || typeof x === "boolean" || x === null) out[k] = x;
  }
  return out;
}

export function validate(raw: unknown): Block | null {
  if (!isObj(raw) || !isStr(raw.type)) return null;
  switch (raw.type) {
    case "records": {
      if (!KINDS.includes(raw.kind as Kind) || !Array.isArray(raw.items)) return null;
      const items = cap(raw.items.map(rec).filter((x): x is Rec => !!x));
      return items.length ? { type: "records", kind: raw.kind as Kind, title: isStr(raw.title) ? raw.title : "", items } : null;
    }
    case "table": {
      if (!Array.isArray(raw.columns) || !Array.isArray(raw.rows)) return null;
      const columns = cap(raw.columns.filter((c): c is Record<string, unknown> => isObj(c) && isStr(c.key) && isStr(c.label))
        .map((c) => ({ key: c.key as string, label: c.label as string, format: FORMATS.includes(c.format as Format) ? c.format as Format : undefined })), 12);
      const rows = cap(raw.rows.map(rec).filter((x): x is Rec => !!x), 200);
      return columns.length && rows.length ? { type: "table", title: isStr(raw.title) ? raw.title : "", columns, rows, note: isStr(raw.note) ? raw.note : undefined } : null;
    }
    case "kpis": {
      if (!Array.isArray(raw.items)) return null;
      const items = cap(raw.items.filter((i): i is Record<string, unknown> => isObj(i) && isStr(i.label) && isNum(i.value))
        .map((i) => ({ label: i.label as string, value: i.value as number, format: FORMATS.includes(i.format as Format) ? i.format as Format : "number" })), 8);
      return items.length ? { type: "kpis", items } : null;
    }
    case "chart": {
      if (!["bar", "line", "donut"].includes(raw.kind as string) || !Array.isArray(raw.points)) return null;
      const points = cap(raw.points.filter((p): p is Record<string, unknown> => isObj(p) && isStr(p.label) && isNum(p.value))
        .map((p) => ({ label: p.label as string, value: p.value as number })), 60);
      // a chart needs real numbers to draw: no points, or negative values for a donut, means no chart
      if (points.length < (raw.kind === "line" ? 2 : 1) || (raw.kind === "donut" && points.some((p) => p.value < 0))) return null;
      return { type: "chart", kind: raw.kind as "bar" | "line" | "donut", title: isStr(raw.title) ? raw.title : "",
        format: FORMATS.includes(raw.format as Format) ? raw.format as Format : "number", points };
    }
    case "sources": {
      if (!Array.isArray(raw.items)) return null;
      const items = cap(raw.items.filter(isObj).map((s) => ({ n: isNum(s.n) ? s.n : null, title: isStr(s.title) ? s.title : "", url: safeUrl(s.url),
        site: isStr(s.site) ? s.site : "", snippet: isStr(s.snippet) ? s.snippet : "", date: isStr(s.date) ? s.date : "" })).filter((s) => s.url));
      return items.length ? { type: "sources", items } : null;
    }
    case "list": {
      if (!Array.isArray(raw.items)) return null;
      const items = cap(raw.items.filter(isStr));
      return items.length ? { type: "list", title: isStr(raw.title) ? raw.title : "", items } : null;
    }
    case "notice":
      return isStr(raw.text) ? { type: "notice", tone: raw.tone === "warn" ? "warn" : "info", text: raw.text.slice(0, 1000) } : null;
    default:
      return null;  // unknown block types are ignored; the reply's text still stands
  }
}

export function validateAll(raw: unknown): Block[] {
  return Array.isArray(raw) ? raw.map(validate).filter((b): b is Block => !!b) : [];
}

export function fmt(v: unknown, f: Format = "text"): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v !== "number") return String(v);
  if (f === "cents") return v >= 100 ? `$${(v / 100).toFixed(2)}` : `${v < 1 && v > 0 ? v.toFixed(2) : v.toFixed(1).replace(/\.0$/, "")}¢`;
  if (f === "percent") return `${Math.round(v * 100)}%`;
  if (f === "number") return v.toLocaleString();
  return String(v);
}
