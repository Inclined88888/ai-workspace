import { ChatEvent, SSEParser, streamPost } from '../sse';

describe('SSEParser', () => {
  it('handles events split across chunks', () => {
    const p = new SSEParser();
    expect(p.push('data: {"type":"start","conversation_id":4}\n\ndata: {"type":"mes')).toEqual([{ type: 'start', conversation_id: 4 }]);
    expect(p.push('sage","content":"hi"}\n\n')).toEqual([{ type: 'message', content: 'hi' }]);
  });
  it('skips comments, [DONE] and malformed JSON', () => {
    const p = new SSEParser();
    expect(p.push(': keepalive\n\ndata: [DONE]\n\ndata: {oops}\n\ndata: {"type":"done","conversation_id":1,"cost_cents":0,"tool_calls":0}\n\n')).toEqual([
      { type: 'done', conversation_id: 1, cost_cents: 0, tool_calls: 0 },
    ]);
  });
  it('accepts CRLF line endings', () => {
    const p = new SSEParser();
    expect(p.push('data: {"type":"thought","content":"x"}\r\n\r\n')).toEqual([{ type: 'thought', content: 'x' }]);
  });
});

class FakeXHR {
  static last: FakeXHR;
  status = 0;
  responseText = '';
  headers: Record<string, string> = {};
  sent = '';
  onprogress: (() => void) | null = null;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    FakeXHR.last = this;
  }
  open() {}
  setRequestHeader(k: string, v: string) {
    // Like a real XHR: a repeated header is appended, not replaced.
    this.headers[k] = this.headers[k] ? `${this.headers[k]}, ${v}` : v;
  }
  send(b: string) {
    this.sent = b;
  }
  abort() {
    this.onabort?.();
  }
  feed(s: string) {
    this.status = 200;
    this.responseText += s;
    this.onprogress?.();
  }
}

describe('streamPost', () => {
  it('streams events as they arrive and resolves on load', async () => {
    const got: ChatEvent[] = [];
    const h = streamPost('http://x/api/mobile/chat', { Authorization: 'Bearer t' }, { message: 'hi' }, (e) => got.push(e), FakeXHR as never);
    const x = FakeXHR.last;
    expect(JSON.parse(x.sent)).toEqual({ message: 'hi' });
    expect(x.headers.Authorization).toBe('Bearer t');
    x.feed('data: {"type":"start","conversation_id":9}\n\n');
    expect(got).toHaveLength(1);
    x.feed('data: {"type":"message","content":"ok"}\n\n');
    x.onload?.();
    await h.done;
    expect(got.map((e) => e.type)).toEqual(['start', 'message']);
  });
  it('sends Content-Type once even when the caller already set it', () => {
    streamPost('http://x', { 'Content-Type': 'application/json', 'X-Workbench': '1' }, {}, () => undefined, FakeXHR as never);
    expect(FakeXHR.last.headers['Content-Type']).toBe('application/json');
    expect(FakeXHR.last.headers.Accept).toBe('text/event-stream');
  });
  it('rejects with the server detail on an HTTP error', async () => {
    const h = streamPost('http://x', {}, {}, () => undefined, FakeXHR as never);
    const x = FakeXHR.last;
    x.status = 401;
    x.responseText = '{"detail":"This phone was unpaired"}';
    x.onload?.();
    await expect(h.done).rejects.toThrow('This phone was unpaired');
  });
  it('rejects when the connection drops', async () => {
    const h = streamPost('http://x', {}, {}, () => undefined, FakeXHR as never);
    FakeXHR.last.onerror?.();
    await expect(h.done).rejects.toThrow(/dropped/);
  });
});
