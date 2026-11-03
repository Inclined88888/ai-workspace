// Background jobs board on the phone (feat/v02-automations): plain-English labels and which buttons a job shows.
import type { Approval } from './api';

export type Job = {
  id: number; kind: string; title: string; prompt: string; status: string; needs: string; detail: string; result: string;
  conversation_id: number | null; automation_id: number | null; column: 'running' | 'you' | 'done'; approvals: Approval[];
  created_at: string; updated_at: string; finished_at: string | null;
};
export type Board = { running: Job[]; you: Job[]; done: Job[]; max_parallel: number; pro: boolean; tier: string };
export type JobAction = 'pause' | 'resume' | 'stop' | 'start' | 'deny';
export type Tone = 'ok' | 'warn' | 'bad' | 'live' | 'quiet';

export function jobLabel(status: string, needs = ''): string {
  if (status === 'waiting') return needs === 'start' ? 'Asks before it starts' : 'Needs your OK';
  const m: Record<string, string> = { queued: 'In line', running: 'Working', paused: 'Paused', done: 'Done', failed: "Didn't finish", stopped: 'Stopped' };
  return m[status] ?? status;
}

export function jobTone(status: string): Tone {
  if (status === 'running') return 'live';
  if (status === 'waiting') return 'warn';
  if (status === 'done') return 'ok';
  if (status === 'failed') return 'bad';
  return 'quiet';
}

/** Phone buttons: no "run again" here, the phone controls what is already on the board. */
export function jobActions(status: string, needs = ''): JobAction[] {
  if (status === 'running' || status === 'queued') return ['pause', 'stop'];
  if (status === 'paused') return ['resume', 'stop'];
  if (status === 'waiting') return needs === 'start' ? ['start', 'deny'] : ['stop'];
  return [];
}

export const ACTION_LABEL: Record<JobAction, string> = { pause: 'Pause', resume: 'Resume', stop: 'Stop', start: 'Yes, run it', deny: 'No' };

/** What the Share box sends: a link on its own goes as the url, anything else as text. */
export function shareBody(raw: string): { text: string; url: string } {
  const s = raw.trim();
  if (/^https?:\/\/\S+$/i.test(s)) return { text: '', url: s };
  const m = s.match(/https?:\/\/\S+/i);
  return { text: s, url: m ? m[0] : '' };
}
