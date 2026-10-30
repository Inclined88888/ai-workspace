/** Display helpers shared by the feeds. */

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, Math.round((now.getTime() - t) / 1000));
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(t).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

export type Tone = 'ok' | 'warn' | 'bad' | 'live' | 'quiet';

const OUTCOMES: Record<string, [string, Tone]> = {
  running: ['Running', 'live'],
  backed: ['Done · checked', 'ok'],
  no_claim: ['Done', 'ok'],
  fixed: ['Done · corrected', 'ok'],
  verified: ['Verified', 'ok'],
  ok: ['Done', 'ok'],
  approved: ['Approved', 'ok'],
  unbacked: ['Not verified', 'warn'],
  held: ['Waiting for you', 'warn'],
  budget_paused: ['Paused · budget', 'warn'],
  interrupted: ['Interrupted', 'warn'],
  step_limit: ['Stopped · step limit', 'warn'],
  duplicate: ['Skipped · duplicate', 'quiet'],
  denied: ['Denied', 'bad'],
  rejected: ['Rejected', 'bad'],
  model_error: ['Model error', 'bad'],
  error: ['Error', 'bad'],
  tool_error: ['Tool error', 'bad'],
  check_failed: ['Check failed', 'bad'],
  bad_args: ['Bad arguments', 'bad'],
  unknown_tool: ['Unknown tool', 'bad'],
  empty_answer: ['Empty answer', 'warn'],
};

export function outcomeLabel(outcome: string): { label: string; tone: Tone } {
  const hit = OUTCOMES[outcome];
  if (hit) return { label: hit[0], tone: hit[1] };
  const label = (outcome || 'unknown').replace(/_/g, ' ');
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'quiet' };
}

export function money(usd: number | null | undefined): string {
  if (usd == null) return 'price unknown';
  if (usd === 0) return 'free';
  if (usd < 0.01) return '<$0.01';
  return `$${usd.toFixed(2)}`;
}

export function toolName(tool: string): string {
  return (tool || '').replace(/_/g, ' ');
}

/** "{"to": "a@b.c", "subject": "Hi"}" -> [["to", "a@b.c"], ["subject", "Hi"]], for the approval card. */
export function argPairs(args: string, max = 6): [string, string][] {
  try {
    const o = JSON.parse(args || '{}');
    if (o && typeof o === 'object' && !Array.isArray(o)) {
      return Object.entries(o)
        .slice(0, max)
        .map(([k, v]) => [k.replace(/_/g, ' '), typeof v === 'string' ? when(v) : JSON.stringify(v)]);
    }
  } catch {
    /* shown raw */
  }
  return args ? [['details', args]] : [];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;

/** "2026-11-03T10:00" -> "Tue 3 Nov, 10:00"; anything that is not an ISO date comes back unchanged. */
export function when(v: string): string {
  if (!ISO_DATE.test(v)) return v;
  const d = new Date(v.length === 10 ? `${v}T00:00` : v);
  if (Number.isNaN(d.getTime())) return v;
  const day = d.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' }).replace(/,/g, '');
  if (v.length === 10) return day;
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return `${day}, ${time}`;
}

/** "3/editor-large" -> "editor-large": the provider id in a model ref means nothing on the phone. */
export function modelName(ref: string | null | undefined): string {
  return (ref || '').replace(/^\d+\//, '');
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`;
}

const KIND_TITLES: Record<string, string> = { schedule: 'Routine', agent: 'Agent job', research: 'Research', telegram: 'Telegram chat', cli: 'Terminal chat' };

/** A run's headline: the tool for a single tool call, else its conversation, else what kind of job it was. */
export function runTitle(r: { conversation_title?: string | null; tool?: string; kind: string; channel?: string }): string {
  const ch = r.channel || '';
  if (ch.startsWith('learn')) return 'Learning from a chat';
  const tool = r.tool ? toolName(r.tool) : '';
  if (tool && (r.kind === 'tool_call' || !r.conversation_title)) return tool.charAt(0).toUpperCase() + tool.slice(1);
  if (r.conversation_title) return r.conversation_title;
  if (KIND_TITLES[ch]) return KIND_TITLES[ch];
  return r.kind === 'turn' ? 'Chat turn' : r.kind.replace(/_/g, ' ');
}

export function masthead(now: Date = new Date()): string {
  return now.toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long' });
}
