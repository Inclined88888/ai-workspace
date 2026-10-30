/**
 * feat/v02-core on the phone: projects, action history with undo, and checked answers.
 * Calls the /api/mobile/* wrappers in workbench/backend/app/v02_mobile.py with the paired device's client.
 */
import { ApiError, Client } from './api';

export type Project = {
  id: number; name: string; kind: string; kind_label?: string; description: string; keywords: string; status: string;
  active?: boolean; counts?: Record<string, number>; next_deadline?: { title: string; due: string } | null;
};
export type ProjectItem = { id: number; kind: string; title: string; body: string; ref: string; due: string | null; done: boolean; auto?: boolean; overdue?: boolean; reason?: string };
export type ProjectDetail = Project & { items: Record<string, ProjectItem[]>; facts?: { content: string }[] };
export type ProjectList = { projects: Project[]; active_id: number | null; limit: { active: number; free_limit: number; unlimited: boolean } };

export type HistoryEntry = {
  id: string; at: string; who: string; category: string; title: string; detail: string; status: string;
  conversation_id: number | null; fix: string; fix_prompt: string; undone_at?: string | null;
  undo: { available: boolean; confirm?: string; note?: string };
};
export type HistoryPage = { entries: HistoryEntry[]; categories: Record<string, string>; next_before: string | null };

export type Claim = { text: string; verdict: 'supported' | 'partly' | 'unsupported' | 'contradicted'; label: string; source: string | null; quote: string; note: string };
export type Source = { id: string; kind: string; label: string; url?: string };
export type Checked = { summary: string; claims: Claim[]; sources: Source[]; segments: { text: string; claim?: number }[] };
export type CheckMode = { on: boolean; allowed: boolean };

export const v02 = {
  projects: (c: Client) => c.req<ProjectList>('GET', '/projects'),
  project: (c: Client, id: number) => c.req<ProjectDetail>('GET', `/projects/${id}`),
  setActive: (c: Client, id: number | null) => c.req<{ active_id: number | null }>('PUT', '/projects/active', { project_id: id }),
  projectFor: (c: Client, cid: number) => c.req<{ project: Project | null }>('GET', `/projects/for-conversation/${cid}`),
  moveChat: (c: Client, cid: number, id: number | null) => c.req<unknown>('PUT', `/projects/for-conversation/${cid}`, { project_id: id }),
  history: (c: Client, before?: string | null, q = '') =>
    c.req<HistoryPage>('GET', `/history?limit=60${before ? `&before=${encodeURIComponent(before)}` : ''}${q ? `&q=${encodeURIComponent(q)}` : ''}`),
  undo: (c: Client, id: string, confirm = false) => c.req<{ undo: { note?: string } }>('POST', `/history/${id}/undo`, { confirm }),
  checkMode: (c: Client, cid: number | null) => c.req<CheckMode>('GET', `/checked/mode${cid ? `?conversation_id=${cid}` : ''}`),
  setCheckMode: (c: Client, cid: number | null, on: boolean) => c.req<{ on: boolean }>('PUT', '/checked/mode', { conversation_id: cid, on, default: false }),
  check: (c: Client, cid: number, content: string) => c.req<Checked>('POST', '/checked/check', { conversation_id: cid, content }),
};

export const UPGRADE = {
  projects_unlimited: 'The free plan keeps one project going at a time. Archive one on your computer, or upgrade to Pro for unlimited projects.',
  checked_answers: 'Checked answers are part of Pro: every claim gets a source and a second look.',
} as const;

/** A Pro gate (402) becomes the plain upgrade line; anything else keeps its own message. */
export function friendlyError(e: unknown, feature?: keyof typeof UPGRADE): string {
  if (e instanceof ApiError && e.status === 402) return feature ? UPGRADE[feature] : 'That is part of Pro.';
  return e instanceof Error ? e.message : String(e);
}

/** True when undo needs a second yes because it changes something outside the app (a shared calendar). */
export const needsConfirm = (e: unknown) => e instanceof ApiError && e.status === 409;

export function verdictTone(v: Claim['verdict']): 'ok' | 'warn' | 'bad' {
  return v === 'supported' ? 'ok' : v === 'partly' ? 'warn' : 'bad';
}

/** Group history entries by day, newest first: [["Today", [...]], ["Yesterday", [...]], ["Mon 26 Oct", [...]]]. */
export function byDay(entries: HistoryEntry[], now: Date = new Date()): [string, HistoryEntry[]][] {
  const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  const out: [string, HistoryEntry[]][] = [];
  for (const e of entries) {
    const d = new Date(e.at);
    const k = key(d) === key(now) ? 'Today' : key(d) === key(y) ? 'Yesterday'
      : d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const last = out[out.length - 1];
    if (last && last[0] === k) last[1].push(e);
    else out.push([k, [e]]);
  }
  return out;
}

/** Items of one kind, open deadlines first by date. */
export function itemsOf(p: ProjectDetail | null, kind: string): ProjectItem[] {
  const xs = [...(p?.items?.[kind] || [])];
  if (kind === 'deadline') xs.sort((a, b) => Number(!!a.done) - Number(!!b.done) || (a.due || '9').localeCompare(b.due || '9'));
  return xs;
}
