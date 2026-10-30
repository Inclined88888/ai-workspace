// Plain-English helpers for the "What I know about you" and "Show me how" pages (pure, unit-tested).

export type Fact = {
  id: number; content: string; category: string; category_label: string; source: string; evidence: string;
  confidence: number; pinned: boolean; uses: number; created_at: string; updated_at: string;
};
export type Param = { name: string; label: string; example: string };
export type Step = { n?: number; action?: string; text: string; param?: string; sensitive?: boolean; needs_user?: boolean; url?: string; target?: Record<string, string> };
export type Playbook = {
  id: number; name: string; summary: string; status: string; version: number; runs: number; successes: number;
  job_kind: string; surface: string; start_url: string; app?: string; params: Param[]; steps: Step[]; notes: string[];
  created_at: string; updated_at: string; schedule_id?: number | null;
};
export type Run = { id: number; playbook_id: number; status: string; result: string; goal: string; values: Record<string, string>; rating: number; created_at: string };

/** Step text with {param} placeholders split into parts, so values can be shown as highlighted words. */
export function stepParts(text: string, values: Record<string, string> = {}): { text: string; param?: string }[] {
  const out: { text: string; param?: string }[] = [];
  const re = /\{([a-z0-9_]+)\}/gi;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    out.push({ text: values[m[1]] || m[1].replace(/_/g, " "), param: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

/** Facts grouped by category in the page's order, empty groups dropped, pinned first inside each group. */
export function groupFacts(facts: Fact[], categories: Record<string, string>): { key: string; label: string; facts: Fact[] }[] {
  const keys = [...Object.keys(categories), ...new Set(facts.map((f) => f.category).filter((c) => !(c in categories)))];
  return keys
    .map((key) => ({ key, label: categories[key] || key, facts: facts.filter((f) => f.category === key).sort((a, b) => Number(b.pinned) - Number(a.pinned)) }))
    .filter((g) => g.facts.length);
}

const RUN: Record<string, string> = {
  queued: "Getting ready", running: "Working on it", needs_you: "Waiting for your OK", done: "Done", error: "Got stuck", failed: "Got stuck", cancelled: "Stopped",
};
export const runLabel = (s: string) => RUN[s] || s;
export const runFinished = (s: string) => ["done", "error", "failed", "cancelled"].includes(s);

const SOURCE: Record<string, string> = {
  chat: "from a chat", teach: "from something you showed me", file: "from a file", task: "from a task", site: "from a website",
  feedback: "from your feedback", you: "you told me", user: "you told me", routine: "from a routine", note: "from a note",
};
export const sourceLabel = (s: string) => SOURCE[s] || (s ? `from ${s}` : "");

/** Missing values for a run, in the playbook's order. */
export function missingValues(params: Param[], values: Record<string, string>): Param[] {
  return params.filter((p) => !(values[p.name] || "").trim());
}

/** A recorded event as one short line for the live view. */
export function eventLine(e: { kind: string; url?: string; app?: string; value?: string; secret?: boolean; text?: string; target?: Record<string, string> }): string {
  const t = e.target || {};
  const what = t.name || t.label || t.text || t.placeholder || t.role || "something";
  switch (e.kind) {
    case "navigate": return `Opened ${e.url || "a page"}`;
    case "click": return `Clicked “${what}”`;
    case "type": return e.secret ? `Filled “${what}” (hidden)` : `Typed “${(e.value || "").slice(0, 40)}” in “${what}”`;
    case "select": return `Chose “${e.value || ""}” in “${what}”`;
    case "check": return `Ticked “${what}”`;
    case "submit": return "Submitted a form";
    case "key": return "Pressed Enter";
    case "open_app": return `Opened ${e.app || "an app"}`;
    default: return e.text || e.value || e.kind;
  }
}
