// Pure helpers for the pack pages (feat/v02-packs). Tested in tests/packs.test.mjs.

export type Line = { kind: "labour" | "materials" | "other"; description: string; qty: number; unit: string; unit_cost: number; estimate?: boolean; markup?: number };
export type TradeSettings = {
  tax_registered: boolean; tax_name: string; tax_rate: number; prices_include_tax: boolean; materials_markup: number; labour_markup: number;
  currency: string; currency_symbol: string;
};

const round2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

/** Same rules as the server (packs/trade.totals), for live totals while typing. The server's numbers are the ones saved. */
export function previewTotals(lines: Line[], s: TradeSettings) {
  let sum = 0;
  const byKind = { labour: 0, materials: 0, other: 0 };
  for (const l of lines) {
    const markup = l.markup ?? (l.kind === "materials" ? s.materials_markup : l.kind === "labour" ? s.labour_markup : 0);
    const amount = round2((Number(l.qty) || 0) * (Number(l.unit_cost) || 0) * (1 + (Number(markup) || 0) / 100));
    byKind[l.kind] = round2(byKind[l.kind] + amount);
    sum = round2(sum + amount);
  }
  const rate = s.tax_registered ? (Number(s.tax_rate) || 0) / 100 : 0;
  if (s.prices_include_tax) {
    const tax = round2((sum * rate) / (1 + rate));
    return { ...byKind, subtotal_ex_tax: round2(sum - tax), tax, total: sum };
  }
  const tax = round2(sum * rate);
  return { ...byKind, subtotal_ex_tax: sum, tax, total: round2(sum + tax) };
}

export function money(n: number, symbol = "$", currency = "AUD"): string {
  const v = (Math.round(n * 100) / 100).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return n < 0 ? `-${symbol}${v.replace("-", "")}` : `${symbol}${v}${currency && currency !== "AUD" && symbol === "$" ? ` ${currency}` : ""}`;
}

/** Australian Business Number checksum (ato.gov.au): subtract 1 from the first digit, weight, sum divisible by 89. */
export function abnValid(abn: string): boolean {
  const d = abn.replace(/\s/g, "");
  if (!/^\d{11}$/.test(d)) return false;
  const w = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19];
  const n = d.split("").map(Number);
  n[0] -= 1;
  return n.reduce((a, x, i) => a + x * w[i], 0) % 89 === 0;
}

export function tierLabel(tier: string, allowed: boolean): string {
  if (tier === "free") return "Free";
  return allowed ? "Pro" : "Pro · upgrade to use";
}

export function statusLabel(kind: string, status: string, overdue: number): string {
  if (kind === "invoice" && status !== "paid" && overdue > 0) return `Overdue ${overdue} day${overdue === 1 ? "" : "s"}`;
  return ({ draft: "Draft", sent: "Sent", accepted: "Accepted", declined: "Declined", paid: "Paid" } as Record<string, string>)[status] ?? status;
}

export const GRADES = [
  { key: "again", label: "Again", hint: "Forgot", shortcut: "1" },
  { key: "hard", label: "Hard", hint: "Got it, slowly", shortcut: "2" },
  { key: "good", label: "Good", hint: "Got it", shortcut: "3" },
  { key: "easy", label: "Easy", hint: "Instant", shortcut: "4" },
] as const;

export function gradeForKey(key: string): string | null {
  return GRADES.find((g) => g.shortcut === key)?.key ?? null;
}

/** Plain lines of working → the list the checker wants (drops blanks and "=>" arrows people type). */
export function workingLines(text: string): string[] {
  return text.split(/\r?\n/).map((l) => l.replace(/^\s*(=>|⇒|→|∴|therefore)\s*/i, "").trim()).filter(Boolean);
}

/** Step to show: "$$latex$$" so the Markdown component renders it with KaTeX. */
export function mathBlock(latex: string): string {
  return `$$${latex}$$`;
}

export function authorsText(a: { family: string; given: string }[]): string {
  if (!a.length) return "";
  const names = a.map((x) => (x.given ? `${x.family}, ${x.given[0]}.` : x.family));
  return names.length > 3 ? `${names.slice(0, 3).join("; ")} et al.` : names.join("; ");
}

export function parseAuthors(text: string): string[] {
  return text.split(/;|\band\b|\n/).map((s) => s.trim()).filter(Boolean);
}

export function deckProgress(cards: number, due: number): string {
  if (!cards) return "No cards yet";
  if (!due) return `${cards} card${cards === 1 ? "" : "s"} · all caught up`;
  return `${due} of ${cards} due now`;
}
