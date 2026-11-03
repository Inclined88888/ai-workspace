/**
 * Pure helpers for Ask from anywhere, voice and meetings (feat/v02-anywhere-voice). No browser APIs, so `npm test`
 * can check them in Node.
 *
 * spokenDecision mirrors backend/app/voice.py. The page only uses it to decide what to say next; the server parses the
 * words again before anything is approved, and the approval card stays on screen the whole time.
 */
export type Decision = "approve" | "deny" | "unclear";

const NEGATIVE = new Set(["no", "nope", "nah", "don't", "dont", "not", "never", "stop", "cancel", "wait", "hold", "deny", "denied",
  "reject", "abort", "negative", "wrong", "undo"]);
const HEDGE = new Set(["maybe", "perhaps", "probably", "possibly", "but", "unless", "later", "depends", "think", "guess", "sure?",
  "what", "why", "how", "which", "who", "when", "hmm", "um", "umm", "uh", "er", "erm", "if", "change", "edit",
  "first", "instead", "actually", "kind", "sort", "might", "should", "could", "would"]);
const AFFIRM = new Set(["yes", "yeah", "yep", "yup", "yea", "approve", "approved", "confirm", "confirmed", "affirmative"]);
const ACTION_PHRASES = ["send it", "do it", "go ahead", "post it", "pay it", "book it", "delete it", "run it", "submit it",
  "save it", "go for it", "proceed"];
const FILLER = new Set(["please", "ok", "okay", "sure", "fine", "it", "that", "that's", "thats", "now", "the", "this", "email", "message",
  "go", "ahead", "send", "do", "post", "pay", "book", "delete", "run", "submit", "save", "for", "proceed",
  "right", "correct", "absolutely", "definitely", "thanks", "thank", "you", "i", "approve", "is", "good", "looks",
  "and", "on", "one", "number", "request", "yes,"]);

export function words(text: string): string[] {
  return text.toLowerCase().replace(/’/g, "'").match(/[a-z']+|\?/g) ?? [];
}

export function spokenDecision(text: string): Decision {
  const w = words(text);
  if (!w.length) return "unclear";
  const joined = w.join(" ");
  if (w.some((x) => NEGATIVE.has(x)) || /\bhold on\b|\bnot yet\b/.test(joined)) return "deny";
  if (w.includes("?") || w.some((x) => HEDGE.has(x)) || w.length > 8) return "unclear";
  const affirmed = w.some((x) => AFFIRM.has(x)) || ACTION_PHRASES.some((p) => new RegExp(`\\b${p}\\b`).test(joined));
  if (!affirmed) return "unclear";
  if (w.some((x) => !AFFIRM.has(x) && !FILLER.has(x))) return "unclear";
  return "approve";
}

/**
 * Wake word: "hey workspace, what's on today" → "what's on today"; "" when only the wake phrase was said; null when it
 * wasn't said at the start. Loose about how engines hear it ("hey work space", "a workspace", "OK workspace").
 */
export function afterWake(transcript: string, phrase: string): string | null {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim();
  const loose = (s: string) => s.replace(/\s/g, "").replace(/^(hey|hi|ok|okay|a|hay)(?=.{3})/, "");
  const target = loose(norm(phrase || "hey workspace"));
  if (target.length < 3) return null;
  const tw = norm(transcript).split(" ").filter(Boolean);
  const span = norm(phrase || "hey workspace").split(" ").length + 1;
  for (let i = 0; i <= Math.min(1, tw.length - 1); i++) {
    for (let j = i + 1; j <= Math.min(tw.length, i + span); j++) {
      if (loose(tw.slice(i, j).join(" ")) === target) return tw.slice(j).join(" ");
    }
  }
  return null;
}

/** Split a reply into sentences so speaking can start at once and stop cleanly when you talk over it. */
export function sentences(text: string, max = 280): string[] {
  const plain = text
    .replace(/```[\s\S]*?```/g, " (code on screen) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/^\s*[-*•]\s+/gm, "")
    .replace(/[*_~>|]/g, "")
    .replace(/https?:\/\/\S+/g, "the link on screen")
    .replace(/\s+/g, " ")
    .trim();
  if (!plain) return [];
  const out: string[] = [];
  for (const s of plain.match(/[^.!?;:]+[.!?;:]*\s*/g) ?? [plain]) {
    let rest = s.trim();
    while (rest.length > max) {
      const cut = rest.lastIndexOf(" ", max);
      out.push(rest.slice(0, cut > 40 ? cut : max));
      rest = rest.slice(cut > 40 ? cut + 1 : max);
    }
    if (rest) out.push(rest);
  }
  return out;
}

// ---- Shortcuts ------------------------------------------------------------------------------------------------------
type KeyLike = { key: string; code?: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean };

/** A key press in the shortcut recorder → a Tauri accelerator ("Alt+Space", "CmdOrCtrl+Shift+K"). null until a real key. */
export function accelFromEvent(e: KeyLike): string | null {
  const mods = ["Control", "Alt", "Shift", "Meta", "OS"];
  if (mods.includes(e.key)) return null;
  let key = "";
  const code = e.code ?? "";
  if (code === "Space" || e.key === " ") key = "Space";
  else if (/^Key[A-Z]$/.test(code)) key = code.slice(3);
  else if (/^Digit\d$/.test(code)) key = code.slice(5);
  else if (/^F\d{1,2}$/.test(e.key)) key = e.key;
  else if (["Enter", "Tab", "Backspace", "Escape", "Delete", "Home", "End", "PageUp", "PageDown"].includes(e.key)) key = e.key;
  else if (e.key.startsWith("Arrow")) key = e.key.slice(5);
  else if (e.key.length === 1) key = e.key.toUpperCase();
  if (!key) return null;
  const parts: string[] = [];
  if (e.metaKey) parts.push("Super");
  if (e.ctrlKey) parts.push("Control");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  // A shortcut that works from any app needs a modifier (bare F-keys are fine)
  if (!parts.length && !/^F\d{1,2}$/.test(key)) return null;
  return [...parts, key].join("+");
}

/** How a shortcut reads to a person: "Option Space" on a Mac, "Alt + Space" on Windows. */
export function accelLabel(accel: string, mac: boolean): string {
  const names: Record<string, string> = mac
    ? { Alt: "⌥ Option", Super: "⌘", Command: "⌘", Cmd: "⌘", CmdOrCtrl: "⌘", Control: "⌃ Control", Ctrl: "⌃ Control", Shift: "⇧ Shift" }
    : { Alt: "Alt", Super: "Win", Command: "Win", Cmd: "Win", CmdOrCtrl: "Ctrl", Control: "Ctrl", Ctrl: "Ctrl", Shift: "Shift" };
  return accel.split("+").map((p) => names[p] ?? p).join(mac ? " " : " + ");
}

// ---- Meetings -------------------------------------------------------------------------------------------------------
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export type Action = { task: string; owner: string; due: string; task_id?: number };

/** "Sam · Fri 6 Nov" style line for an action item; owner and date only when the meeting said them. */
export function actionMeta(a: Action, today = new Date()): string {
  const bits: string[] = [];
  if (a.owner) bits.push(a.owner);
  if (a.due && /^\d{4}-\d{2}-\d{2}$/.test(a.due)) {
    const d = new Date(a.due + "T12:00:00");
    const sameYear = d.getFullYear() === today.getFullYear();
    bits.push(d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) }));
  }
  return bits.join(" · ") || "No owner or date said";
}

/** Plain-text notes for Copy: summary, decisions, actions with owners and dates. */
export function notesText(title: string, n: { summary: string; notes: string[]; decisions: string[]; actions: Action[] }): string {
  const lines = [title, "", n.summary];
  if (n.notes.length) lines.push("", "Notes", ...n.notes.map((x) => `- ${x}`));
  if (n.decisions.length) lines.push("", "Decisions", ...n.decisions.map((x) => `- ${x}`));
  if (n.actions.length) lines.push("", "Actions", ...n.actions.map((a) => `- ${a.task}${a.owner ? ` (${a.owner}` : ""}${a.owner && a.due ? `, ${a.due})` : a.owner ? ")" : a.due ? ` (by ${a.due})` : ""}`));
  return lines.join("\n").trim();
}

/** Where the ask bar hands a question to the main window ("Do it in Chat"). */
export const HANDOFF_KEY = "wb-ask-handoff";
export function handoffDraft(question: string, text: string, answer: string): string {
  const parts = [question.trim() || "Help me with this."];
  if (text.trim()) parts.push(`\n\nHere's the text:\n"""\n${text.trim().slice(0, 20000)}\n"""`);
  if (answer.trim()) parts.push(`\n\nYou suggested:\n${answer.trim().slice(0, 8000)}`);
  return parts.join("");
}
