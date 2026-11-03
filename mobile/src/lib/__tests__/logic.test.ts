import { revealSteps } from '../reveal';
import { argPairs, modelName, money, outcomeLabel, plural, relativeTime, runTitle, when } from '../format';
import { decisionNote, initialTurn, phaseLabel, reduceTurn, splitAttachmentNotes } from '../turn';
import { base64Bytes, bytesToBase64, kindFor } from '../attach';

jest.mock('expo-image-picker', () => ({}));
jest.mock('expo-document-picker', () => ({}));
jest.mock('expo-file-system', () => ({ File: class {} }));

describe('reveal', () => {
  it('ends at the full text, on word boundaries, within the time cap', () => {
    const text = 'The quick brown fox jumps over the lazy dog.';
    const s = revealSteps(text);
    expect(s[s.length - 1]).toBe(text.length);
    for (const i of s.slice(0, -1)) expect(text[i - 1]).toBe(' ');
    const long = 'word '.repeat(5000);
    expect(revealSteps(long, 2200, 16).length).toBeLessThanOrEqual(Math.floor(2200 / 16) + 1);
    expect(revealSteps('')).toEqual([]);
  });
});

describe('format', () => {
  const now = new Date('2026-10-30T10:00:00Z');
  it('relative times', () => {
    expect(relativeTime('2026-10-30T09:59:50Z', now)).toBe('just now');
    expect(relativeTime('2026-10-30T09:40:00Z', now)).toBe('20m ago');
    expect(relativeTime('2026-10-30T05:00:00Z', now)).toBe('5h ago');
    expect(relativeTime('2026-10-28T10:00:00Z', now)).toBe('2d ago');
    expect(relativeTime(null, now)).toBe('');
  });
  it('outcomes and money', () => {
    expect(outcomeLabel('held')).toEqual({ label: 'Waiting for you', tone: 'warn' });
    expect(outcomeLabel('some_new_thing')).toEqual({ label: 'Some new thing', tone: 'quiet' });
    expect(money(null)).toBe('price unknown');
    expect(money(0)).toBe('free');
    expect(money(0.004)).toBe('<$0.01');
    expect(money(1.234)).toBe('$1.23');
  });
  it('approval arguments', () => {
    expect(argPairs('{"to":"a@b.c","cc_list":["x"]}')).toEqual([['to', 'a@b.c'], ['cc list', '["x"]']]);
    expect(argPairs('not json')).toEqual([['details', 'not json']]);
    expect(argPairs('')).toEqual([]);
  });
});

describe('turn reducer', () => {
  it('folds a full turn with a tool and a held action', () => {
    let t = initialTurn(null);
    t = reduceTurn(t, { type: 'start', conversation_id: 7 });
    expect(t).toMatchObject({ phase: 'thinking', conversationId: 7 });
    t = reduceTurn(t, { type: 'tool_call', name: 'search_notes', args: '{}' });
    expect(phaseLabel(t)).toBe('Using search notes');
    t = reduceTurn(t, { type: 'tool_result', name: 'search_notes', ok: true, preview: '3 hits' });
    expect(t.steps).toEqual([{ tool: 'search_notes', ok: true, preview: '3 hits' }]);
    t = reduceTurn(t, { type: 'approval_required', id: 41, tool: 'send_email', args: '{}', effect: 'external', reason: 'r' });
    expect(phaseLabel(t)).toBe('Waiting for your approval');
    expect(t.held).toHaveLength(1);
    t = reduceTurn(t, { type: 'message', content: 'Drafted.' });
    t = reduceTurn(t, { type: 'done', conversation_id: 7, cost_cents: 0.2, tool_calls: 2 });
    expect(t).toMatchObject({ phase: 'done', text: 'Drafted.', costCents: 0.2 });
  });
  it('keeps an error through done', () => {
    let t = reduceTurn(initialTurn(1), { type: 'error', error: 'No model' });
    t = reduceTurn(t, { type: 'done', conversation_id: 1, cost_cents: 0, tool_calls: 0 });
    expect(t).toMatchObject({ phase: 'error', error: 'No model' });
  });
  it('reads the notes the computer writes into the transcript', () => {
    expect(decisionNote('[I approved request #12: send_email. Result: ok]')).toEqual({ approved: true, id: 12, tool: 'send_email' });
    expect(decisionNote('hello')).toBeNull();
    const s = splitAttachmentNotes('Look\n\n[Photo attached from the phone: ab.jpg, 120 KB, saved in mobile_uploads. Automatic ...]');
    expect(s).toEqual({ text: 'Look', tags: ['Photo · 120 KB'] });
  });
});

describe('attachments', () => {
  it('base64 matches the platform encoder', () => {
    const cases: [string, string][] = [['', ''], ['a', 'YQ=='], ['ab', 'YWI='], ['abc', 'YWJj'], ['hello world!', 'aGVsbG8gd29ybGQh'], ['\u00ff\u0000\u0010', '/wAQ']];
    for (const [s, want] of cases) {
      const bytes = Uint8Array.from(s, (ch) => ch.charCodeAt(0));
      const b64 = bytesToBase64(bytes);
      expect(b64).toBe(want);
      expect(base64Bytes(b64)).toBe(bytes.length);
    }
  });
  it('only accepts what the computer accepts', () => {
    expect(kindFor('image/png')).toBe('photo');
    expect(kindFor('audio/m4a')).toBe('voice');
    expect(kindFor('application/pdf')).toBeNull();
  });
});

describe('display helpers', () => {
  it('formats ISO dates in approval arguments and leaves other text alone', () => {
    expect(when('2026-11-03T10:00')).toBe('Tue 3 Nov, 10:00');
    expect(when('2026-11-03')).toBe('Tue 3 Nov');
    expect(when('Launch review')).toBe('Launch review');
    expect(when('2026-13-45T99:00')).toBe('2026-13-45T99:00');
  });
  it('drops the provider id from model refs', () => {
    expect(modelName('12/gpt-x')).toBe('gpt-x');
    expect(modelName('local/llama3')).toBe('local/llama3');
    expect(modelName(null)).toBe('');
  });
  it('pluralises', () => {
    expect(plural(1, 'step')).toBe('1 step');
    expect(plural(3, 'step')).toBe('3 steps');
  });
  it('titles runs by conversation, tool, then kind of job', () => {
    expect(runTitle({ conversation_title: 'Plan', kind: 'turn' })).toBe('Plan');
    expect(runTitle({ tool: 'create_event', kind: 'tool_call', conversation_title: 'Plan' })).toBe('Create event');
    expect(runTitle({ kind: 'turn', channel: 'schedule' })).toBe('Routine');
    expect(runTitle({ kind: 'turn', channel: 'learn:memory' })).toBe('Learning from a chat');
    expect(runTitle({ kind: 'turn', channel: 'web' })).toBe('Chat turn');
  });
});
