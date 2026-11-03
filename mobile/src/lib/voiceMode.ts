/**
 * Voice mode for the phone (feat/v02-anywhere-voice): say something, it's sent, the answer is read out, and anything
 * that needs your OK is shown as the usual card and read out too. Only a clear "yes, send it" approves; the computer
 * checks the words again before anything runs.
 *
 * spokenDecision mirrors workbench/backend/app/voice.py (the computer's check is the one that counts).
 */
export type Decision = 'approve' | 'deny' | 'unclear';

const NEGATIVE = new Set(['no', 'nope', 'nah', "don't", 'dont', 'not', 'never', 'stop', 'cancel', 'wait', 'hold', 'deny', 'denied',
  'reject', 'abort', 'negative', 'wrong', 'undo']);
const HEDGE = new Set(['maybe', 'perhaps', 'probably', 'possibly', 'but', 'unless', 'later', 'depends', 'think', 'guess', 'sure?',
  'what', 'why', 'how', 'which', 'who', 'when', 'hmm', 'um', 'umm', 'uh', 'er', 'erm', 'if', 'change', 'edit',
  'first', 'instead', 'actually', 'kind', 'sort', 'might', 'should', 'could', 'would']);
const AFFIRM = new Set(['yes', 'yeah', 'yep', 'yup', 'yea', 'approve', 'approved', 'confirm', 'confirmed', 'affirmative']);
const ACTION_PHRASES = ['send it', 'do it', 'go ahead', 'post it', 'pay it', 'book it', 'delete it', 'run it', 'submit it',
  'save it', 'go for it', 'proceed'];
const FILLER = new Set(['please', 'ok', 'okay', 'sure', 'fine', 'it', 'that', "that's", 'thats', 'now', 'the', 'this', 'email', 'message',
  'go', 'ahead', 'send', 'do', 'post', 'pay', 'book', 'delete', 'run', 'submit', 'save', 'for', 'proceed',
  'right', 'correct', 'absolutely', 'definitely', 'thanks', 'thank', 'you', 'i', 'approve', 'is', 'good', 'looks',
  'and', 'on', 'one', 'number', 'request', 'yes,']);

export function spokenDecision(text: string): Decision {
  const w: string[] = text.toLowerCase().replace(/’/g, "'").match(/[a-z']+|\?/g) ?? [];
  if (!w.length) return 'unclear';
  const joined = w.join(' ');
  if (w.some((x) => NEGATIVE.has(x)) || /\bhold on\b|\bnot yet\b/.test(joined)) return 'deny';
  if (w.includes('?') || w.some((x) => HEDGE.has(x)) || w.length > 8) return 'unclear';
  const affirmed = w.some((x) => AFFIRM.has(x)) || ACTION_PHRASES.some((p) => new RegExp(`\\b${p}\\b`).test(joined));
  if (!affirmed) return 'unclear';
  if (w.some((x) => !AFFIRM.has(x) && !FILLER.has(x))) return 'unclear';
  return 'approve';
}

/** What to read out: no markdown, code or links. */
export function speakable(text: string, max = 1200): string {
  const s = text
    .replace(/```[\s\S]*?```/g, ' (code on screen) ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#+\s*/gm, '')
    .replace(/^\s*[-*•]\s+/gm, '')
    .replace(/[*_~>|]/g, '')
    .replace(/https?:\/\/\S+/g, 'the link on screen')
    .replace(/\s+/g, ' ')
    .trim();
  if (s.length <= max) return s;
  const cut = s.lastIndexOf('. ', max);
  return `${s.slice(0, cut > 200 ? cut + 1 : max)} The rest is on screen.`;
}

export function approvalPrompt(tool: string): string {
  const what = (tool || 'do this').replace(/_/g, ' ').trim();
  return `I need your OK to ${what}. It's on screen. Say "yes, send it" to go ahead, or "no" to cancel.`;
}

// ---- Speaking (expo-speech; a native module, so it's quiet where it isn't in the build) ---------------------------
type SpeechMod = { speak: (t: string, o: Record<string, unknown>) => void; stop: () => void };
let speech: SpeechMod | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  speech = require('expo-speech') as SpeechMod;
} catch {
  speech = null;
}

export const canSpeak = () => !!speech;

export function say(text: string, lang = 'en-AU'): Promise<void> {
  const t = speakable(text);
  if (!speech || !t) return Promise.resolve();
  return new Promise((done) => {
    try {
      speech!.stop();
      speech!.speak(t, { language: lang, onDone: done, onStopped: done, onError: done });
    } catch {
      done();
    }
  });
}

export function stopSaying() {
  try {
    speech?.stop();
  } catch {
    /* nothing to stop */
  }
}
