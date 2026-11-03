import { ApiError } from '../api';
import { byDay, friendlyError, itemsOf, needsConfirm, UPGRADE, verdictTone, HistoryEntry, ProjectDetail } from '../v02';

const e = (id: string, at: string): HistoryEntry => ({ id, at, who: 'Assistant', category: 'tasks', title: id, detail: '', status: 'done',
  conversation_id: null, fix: '', fix_prompt: '', undo: { available: true } });

test('history groups by day, newest first', () => {
  const now = new Date(2026, 9, 30, 12);
  const g = byDay([e('a', new Date(2026, 9, 30, 9).toISOString()), e('b', new Date(2026, 9, 30, 8).toISOString()),
    e('c', new Date(2026, 9, 29, 20).toISOString()), e('d', new Date(2026, 9, 20, 9).toISOString())], now);
  expect(g.map(([k, xs]) => [k, xs.length])).toEqual([['Today', 2], ['Yesterday', 1], [expect.any(String), 1]]);
});

test('Pro gates read as an upgrade line; undo confirmations are spotted', () => {
  expect(friendlyError(new ApiError('HTTP 402', 402), 'checked_answers')).toBe(UPGRADE.checked_answers);
  expect(friendlyError(new ApiError('No such project', 404))).toBe('No such project');
  expect(needsConfirm(new ApiError('HTTP 409', 409))).toBe(true);
  expect(needsConfirm(new ApiError('HTTP 400', 400))).toBe(false);
});

test('verdict tones and deadline order', () => {
  expect([verdictTone('supported'), verdictTone('partly'), verdictTone('unsupported')]).toEqual(['ok', 'warn', 'bad']);
  const p = { items: { deadline: [
    { id: 1, kind: 'deadline', title: 'late', body: '', ref: '', due: '2026-12-01', done: false },
    { id: 2, kind: 'deadline', title: 'done', body: '', ref: '', due: '2026-11-01', done: true },
    { id: 3, kind: 'deadline', title: 'soon', body: '', ref: '', due: '2026-11-05', done: false },
  ], person: [{ id: 4, kind: 'person', title: 'Jane', body: '', ref: '', due: null, done: false }] } } as unknown as ProjectDetail;
  expect(itemsOf(p, 'deadline').map((i) => i.title)).toEqual(['soon', 'late', 'done']);
});
