// feat/v02-automations: the phone's jobs board labels, buttons, push routing and the Share box.
import { jobActions, jobLabel, jobTone, shareBody } from '../jobs';
import { routeFor } from '../push';
import { Client } from '../api';

jest.mock('expo-notifications', () => ({ setNotificationHandler: () => undefined }));
jest.mock('expo-device', () => ({}));
jest.mock('expo-constants', () => ({}));

describe('jobs board', () => {
  it('reads in plain English', () => {
    expect(jobLabel('waiting', 'approval')).toBe('Needs your OK');
    expect(jobLabel('waiting', 'start')).toBe('Asks before it starts');
    expect(jobLabel('failed')).toBe("Didn't finish");
    expect(jobTone('running')).toBe('live');
    expect(jobTone('waiting')).toBe('warn');
  });
  it('shows the right buttons', () => {
    expect(jobActions('running')).toEqual(['pause', 'stop']);
    expect(jobActions('paused')).toEqual(['resume', 'stop']);
    expect(jobActions('waiting', 'start')).toEqual(['start', 'deny']);
    expect(jobActions('waiting', 'approval')).toEqual(['stop']);
    expect(jobActions('done')).toEqual([]);
  });
  it('a job push opens the board', () => {
    expect(routeFor({ kind: 'job', id: 4 })).toBe('/jobs');
  });
  it('the Share box sends links as urls', () => {
    expect(shareBody(' https://example.com/a ')).toEqual({ text: '', url: 'https://example.com/a' });
    expect(shareBody('Look at https://x.io/r now')).toEqual({ text: 'Look at https://x.io/r now', url: 'https://x.io/r' });
    expect(shareBody('just words')).toEqual({ text: 'just words', url: '' });
  });
  it('the client calls the phone routes', async () => {
    const calls: [string, RequestInit][] = [];
    const f = (async (url: string, init: RequestInit) => {
      calls.push([url, init]);
      return { ok: true, status: 200, json: async () => ({ fired: 1 }) } as Response;
    }) as unknown as typeof fetch;
    const c = new Client('http://pc:8000', 'wbd_x', f);
    await c.jobAction(3, 'pause');
    await c.share({ text: 'hi', url: '' });
    expect(calls[0][0]).toBe('http://pc:8000/api/mobile/jobs/3/pause');
    expect(calls[0][1].method).toBe('POST');
    expect(calls[1][0]).toBe('http://pc:8000/api/mobile/share');
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ text: 'hi', url: '' });
  });
});
