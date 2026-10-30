/**
 * The live state of one chat turn, folded from the computer's SSE events. Pure, so it is tested without a phone.
 */
import { ChatEvent } from './sse';
import { toolName } from './format';

export type Step = { tool: string; ok: boolean | null; preview: string };
export type HeldAction = { id: number; tool: string; args: string; effect: string; reason: string; denied: boolean };

export type Turn = {
  phase: 'sending' | 'thinking' | 'tool' | 'checking' | 'waiting' | 'answered' | 'done' | 'error';
  conversationId: number | null;
  steps: Step[];
  held: HeldAction[];
  text: string;
  error: string;
  costCents: number;
};

export const initialTurn = (conversationId: number | null): Turn => ({
  phase: 'sending',
  conversationId,
  steps: [],
  held: [],
  text: '',
  error: '',
  costCents: 0,
});

export function reduceTurn(t: Turn, e: ChatEvent): Turn {
  switch (e.type) {
    case 'start':
      return { ...t, phase: 'thinking', conversationId: e.conversation_id };
    case 'thought':
      return { ...t, phase: 'thinking' };
    case 'tool_call':
      return { ...t, phase: 'tool', steps: [...t.steps, { tool: e.name, ok: null, preview: '' }] };
    case 'tool_result': {
      const steps = [...t.steps];
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].tool === e.name && steps[i].ok === null) {
          steps[i] = { ...steps[i], ok: e.ok, preview: e.preview };
          break;
        }
      }
      return { ...t, phase: 'thinking', steps };
    }
    case 'approval_required':
    case 'approval_denied':
      return {
        ...t,
        phase: e.type === 'approval_required' ? 'waiting' : t.phase,
        held: [...t.held.filter((h) => h.id !== e.id), { id: e.id, tool: e.tool, args: e.args, effect: e.effect, reason: e.reason, denied: e.type === 'approval_denied' }],
      };
    case 'claim_check':
      return { ...t, phase: e.retrying ? 'checking' : t.phase };
    case 'message':
      return { ...t, phase: 'answered', text: e.content };
    case 'error':
      return { ...t, phase: 'error', error: e.error };
    case 'done':
      return { ...t, phase: t.phase === 'error' ? 'error' : 'done', conversationId: e.conversation_id ?? t.conversationId, costCents: e.cost_cents };
    default:
      return t;
  }
}

export function phaseLabel(t: Turn): string {
  switch (t.phase) {
    case 'sending':
      return 'Sending to your computer';
    case 'thinking':
      return t.steps.length ? 'Reading the results' : 'Thinking';
    case 'tool': {
      const last = t.steps[t.steps.length - 1];
      return last ? `Using ${toolName(last.tool)}` : 'Working';
    }
    case 'checking':
      return 'Checking its own answer';
    case 'waiting':
      return 'Waiting for your approval';
    case 'answered':
      return 'Writing';
    case 'error':
      return 'Stopped';
    default:
      return 'Done';
  }
}

/** Attachment notes the computer appends to a phone message, shown as small tags instead of raw text. */
export function splitAttachmentNotes(content: string): { text: string; tags: string[] } {
  const tags: string[] = [];
  const text = content
    .replace(/\[(Photo|Voice note) attached from the phone: [^,\]]+, (\d+) KB[^\]]*\]/g, (_m, kind: string, kb: string) => {
      tags.push(`${kind} · ${kb} KB`);
      return '';
    })
    .trim();
  return { text, tags };
}

/** "[I approved request #12: send_email. Result: ...]" -> a receipt line rather than a speech bubble. */
export function decisionNote(content: string): { approved: boolean; id: number; tool: string } | null {
  const m = /^\[I (approved|denied) request #(\d+): (\w+)/.exec(content || '');
  return m ? { approved: m[1] === 'approved', id: Number(m[2]), tool: m[3] } : null;
}
