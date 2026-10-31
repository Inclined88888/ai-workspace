// Live documents in replies (feat/v02-automations): pure helpers, unit tested in tests/live.test.mjs.
// A reply can carry ```sheet / ```chart JSON blocks and ```doc Markdown blocks; they render as editable documents.

export type Cell = string | number | null;
export type Sheet = { title: string; columns: string[]; rows: Cell[][] };
export type Series = { name: string; values: number[] };
export type Chart = { title: string; type: "bar" | "line" | "pie"; labels: string[]; series: Series[] };
export type LiveKind = "doc" | "sheet" | "chart";
export type Piece = { kind: "md"; text: string } | { kind: LiveKind; source: string; index: number; data: Sheet | Chart | string };

const FENCE = /```(sheet|chart|doc)[^\n]*\n([\s\S]*?)(?:\n```|$(?![\s\S]))/g;

export function normaliseSheet(d: unknown): Sheet | null {
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  let columns = Array.isArray(o.columns) ? o.columns.map(String) : [];
  let rows = Array.isArray(o.rows) ? (o.rows.filter(Array.isArray) as Cell[][]) : [];
  if (!columns.length && rows.length) [columns, rows] = [rows[0].map((c) => String(c ?? "")), rows.slice(1)];
  if (!columns.length) return null;
  rows = rows.map((r) => columns.map((_, i) => (r[i] === undefined ? "" : (r[i] as Cell))));
  return { title: String(o.title || "Sheet"), columns, rows };
}

export function normaliseChart(d: unknown): Chart | null {
  if (!d || typeof d !== "object") return null;
  const o = d as Record<string, unknown>;
  const labels = Array.isArray(o.labels) ? o.labels.map(String) : [];
  const series = (Array.isArray(o.series) ? o.series : []).filter((s) => s && typeof s === "object").map((s, i) => {
    const x = s as Record<string, unknown>;
    return { name: String(x.name || `Series ${i + 1}`), values: (Array.isArray(x.values) ? x.values : []).map((v) => Number(v) || 0) };
  });
  if (!labels.length || !series.length) return null;
  const type = o.type === "line" || o.type === "pie" ? o.type : "bar";
  return { title: String(o.title || "Chart"), type, labels, series };
}

/** Split a reply into Markdown and live blocks. A block that doesn't parse stays as a normal code block. */
export function splitLive(text: string): Piece[] {
  const out: Piece[] = [];
  let last = 0;
  let index = 0;
  for (const m of text.matchAll(FENCE)) {
    const kind = m[1] as LiveKind;
    const body = m[2];
    let data: Sheet | Chart | string | null = null;
    if (kind === "doc") data = body;
    else {
      try {
        const j = JSON.parse(body);
        data = kind === "sheet" ? normaliseSheet(j) : normaliseChart(j);
      } catch { data = null; }
    }
    if (data === null) continue;
    if (m.index! > last) out.push({ kind: "md", text: text.slice(last, m.index) });
    out.push({ kind, source: body, index: index++, data });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ kind: "md", text: text.slice(last) });
  return out.filter((p) => p.kind !== "md" || p.text.trim());
}

// --- formulas ------------------------------------------------------------------------------------------------------------
// Row 1 is the header row, so the first data row is row 2, like a spreadsheet. Supports + - * / ^ &, comparisons,
// cell refs (B2), ranges (B2:B9) and SUM AVERAGE MIN MAX COUNT ROUND ABS IF CONCAT.

export function colName(i: number): string {
  let s = "";
  for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
  return s;
}

function colIndex(s: string): number {
  let n = 0;
  for (const ch of s.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

type Val = number | string | boolean | Val[];
class FormulaError extends Error {}

export function evaluate(sheet: Sheet): (string | number)[][] {
  const memo = new Map<string, Val>();
  const busy = new Set<string>();
  const raw = (r: number, c: number): Cell => (r === 0 ? sheet.columns[c] ?? "" : sheet.rows[r - 1]?.[c] ?? "");

  const cell = (r: number, c: number): Val => {
    const key = `${r}:${c}`;
    if (memo.has(key)) return memo.get(key)!;
    if (busy.has(key)) throw new FormulaError("#CYCLE");
    const v = raw(r, c);
    let out: Val;
    if (typeof v === "string" && v.startsWith("=")) {
      busy.add(key);
      try { out = run(v.slice(1)); } finally { busy.delete(key); }
    } else if (typeof v === "number") out = v;
    else if (v === null || v === "") out = "";
    else out = /^-?\d+(\.\d+)?$/.test(String(v).trim()) ? Number(v) : String(v);
    memo.set(key, out);
    return out;
  };

  const run = (src: string): Val => {
    const toks = src.match(/"[^"]*"|[A-Za-z]+\d+:[A-Za-z]+\d+|[A-Za-z_]+\d*|\d+(?:\.\d+)?%?|<=|>=|<>|[-+*/^&(),=<>]|\S/g) ?? [];
    let i = 0;
    const peek = () => toks[i];
    const eat = (t?: string) => {
      const x = toks[i++];
      if (t !== undefined && x !== t) throw new FormulaError("#ERR");
      return x;
    };
    const num = (v: Val): number => {
      if (Array.isArray(v)) return num(v[0] ?? 0);
      if (typeof v === "boolean") return v ? 1 : 0;
      if (v === "") return 0;
      const n = Number(v);
      if (Number.isNaN(n)) throw new FormulaError("#VALUE!");
      return n;
    };
    const flat = (vs: Val[]): Val[] => vs.flatMap((v) => (Array.isArray(v) ? flat(v) : [v]));
    const nums = (vs: Val[]) => flat(vs).filter((v) => typeof v === "number" || (typeof v === "string" && v !== "" && !Number.isNaN(Number(v)))).map(Number);

    const call = (name: string, args: Val[]): Val => {
      const n = nums(args);
      switch (name) {
        case "SUM": return n.reduce((a, b) => a + b, 0);
        case "AVERAGE": case "AVG": if (!n.length) throw new FormulaError("#DIV/0!"); return n.reduce((a, b) => a + b, 0) / n.length;
        case "MIN": return n.length ? Math.min(...n) : 0;
        case "MAX": return n.length ? Math.max(...n) : 0;
        case "COUNT": return n.length;
        case "ABS": return Math.abs(num(args[0]));
        case "ROUND": { const p = 10 ** num(args[1] ?? 0); return Math.round(num(args[0]) * p) / p; }
        case "IF": return (Array.isArray(args[0]) ? args[0][0] : args[0]) && num(args[0]) !== 0 ? args[1] ?? true : args[2] ?? false;
        case "CONCAT": return flat(args).map(String).join("");
        default: throw new FormulaError("#NAME?");
      }
    };

    const primary = (): Val => {
      const t = eat();
      if (t === undefined) throw new FormulaError("#ERR");
      if (t === "(") { const v = compare(); eat(")"); return v; }
      if (t === "-") return -num(power());
      if (t === "+") return num(power());
      if (t.startsWith('"')) return t.slice(1, -1);
      if (/^\d/.test(t)) return t.endsWith("%") ? Number(t.slice(0, -1)) / 100 : Number(t);
      const range = t.match(/^([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)$/);
      if (range) {
        const [c1, c2] = [colIndex(range[1]), colIndex(range[3])].sort((a, b) => a - b);
        const [r1, r2] = [Number(range[2]) - 1, Number(range[4]) - 1].sort((a, b) => a - b);
        const vs: Val[] = [];
        for (let r = r1; r <= r2; r++) for (let c = c1; c <= c2; c++) vs.push(cell(r, c));
        return vs;
      }
      const ref = t.match(/^([A-Za-z]{1,2})(\d+)$/);
      if (ref && peek() !== "(") return cell(Number(ref[2]) - 1, colIndex(ref[1]));
      const name = t.toUpperCase();
      if (name === "TRUE" || name === "FALSE") return name === "TRUE";
      eat("(");
      const args: Val[] = [];
      if (peek() !== ")") {
        args.push(compare());
        while (peek() === ",") { eat(","); args.push(compare()); }
      }
      eat(")");
      return call(name, args);
    };
    const power = (): Val => {
      let v = primary();
      while (peek() === "^") { eat(); v = num(v) ** num(primary()); }
      return v;
    };
    const term = (): Val => {
      let v = power();
      while (peek() === "*" || peek() === "/") {
        const op = eat();
        const r = num(power());
        if (op === "/" && r === 0) throw new FormulaError("#DIV/0!");
        v = op === "*" ? num(v) * r : num(v) / r;
      }
      return v;
    };
    const sum = (): Val => {
      let v = term();
      while (peek() === "+" || peek() === "-") { const op = eat(); const r = num(term()); v = op === "+" ? num(v) + r : num(v) - r; }
      return v;
    };
    const concat = (): Val => {
      let v = sum();
      while (peek() === "&") { eat(); v = `${show(v)}${show(sum())}`; }
      return v;
    };
    const compare = (): Val => {
      const v = concat();
      const op = peek();
      if (!["=", "<", ">", "<=", ">=", "<>"].includes(op ?? "")) return v;
      eat();
      const r = concat();
      const [a, b] = typeof v === "number" || typeof r === "number" ? [num(v), num(r)] : [String(v), String(r)];
      return op === "=" ? a === b : op === "<>" ? a !== b : op === "<" ? a < b : op === ">" ? a > b : op === "<=" ? a <= b : a >= b;
    };
    const v = compare();
    if (i < toks.length) throw new FormulaError("#ERR");
    return v;
  };

  const show = (v: Val): string | number => {
    if (Array.isArray(v)) return show(v[0] ?? "");
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    if (typeof v === "number") return Number.isFinite(v) ? Math.round(v * 1e10) / 1e10 : "#NUM!";
    return v;
  };

  return sheet.rows.map((row, r) => row.map((_, c) => {
    try { return show(cell(r + 1, c)); } catch (e) { memo.delete(`${r + 1}:${c}`); return e instanceof FormulaError ? e.message : "#ERR"; }
  }));
}

export function fmtCell(v: string | number): string {
  if (typeof v !== "number") return v;
  return Math.abs(v) >= 1000 || !Number.isInteger(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v);
}

export function sheetCsv(sheet: Sheet, values = evaluate(sheet)): string {
  const q = (v: unknown) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [sheet.columns, ...values].map((r) => r.map(q).join(",")).join("\r\n") + "\r\n";
}

export function chartCsv(c: Chart): string {
  const q = (v: unknown) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [["Label", ...c.series.map((s) => s.name)], ...c.labels.map((l, i) => [l, ...c.series.map((s) => s.values[i] ?? 0)])]
    .map((r) => r.map(q).join(",")).join("\r\n") + "\r\n";
}

/** Nice axis maximum and ticks for a chart. */
export function niceScale(max: number, ticks = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((x) => x * mag).find((x) => x >= raw) ?? raw;
  const out = [];
  for (let v = 0; v <= max + step * 0.999; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

/** Pie slices as SVG arc paths on a circle of radius r centred at (r, r). */
export function pieSlices(values: number[], r = 80): { d: string; share: number }[] {
  const total = values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
  let a0 = -Math.PI / 2;
  return values.map((v) => {
    const share = Math.max(0, v) / total;
    const a1 = a0 + share * 2 * Math.PI;
    const p = (a: number) => `${(r + r * Math.cos(a)).toFixed(2)} ${(r + r * Math.sin(a)).toFixed(2)}`;
    const d = share >= 0.9999 ? `M ${r} 0 A ${r} ${r} 0 1 1 ${r - 0.01} 0 Z` : `M ${r} ${r} L ${p(a0)} A ${r} ${r} 0 ${share > 0.5 ? 1 : 0} 1 ${p(a1)} Z`;
    a0 = a1;
    return { d, share };
  });
}

export function liveKey(messageId: number | string, index: number): string {
  return `wb.live.${messageId}.${index}`;
}

// --- jobs board and automations: plain-English labels ---------------------------------------------------------------------
export function jobLabel(status: string, needs = ""): string {
  if (status === "waiting") return needs === "start" ? "Asks before it starts" : "Needs your OK";
  return ({ queued: "In line", running: "Working", paused: "Paused", done: "Done", failed: "Didn't finish", stopped: "Stopped" } as Record<string, string>)[status] ?? status;
}

export function jobControls(status: string, needs = ""): string[] {
  if (status === "running") return ["pause", "stop"];
  if (status === "queued") return ["pause", "stop"];
  if (status === "paused") return ["resume", "stop"];
  if (status === "waiting") return needs === "start" ? ["start", "deny"] : ["stop"];
  return ["retry"];
}
