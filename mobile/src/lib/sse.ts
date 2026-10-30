/** Server-sent events: an incremental parser (pure, tested) and a streaming POST that works on iOS, Android and web. */

export type ChatEvent =
  | { type: 'start'; conversation_id: number }
  | { type: 'thought'; content: string }
  | { type: 'tool_call'; name: string; args: string; from_text?: boolean }
  | { type: 'tool_result'; name: string; ok: boolean; preview: string; receipt?: { verified: boolean | null; evidence: string } }
  | { type: 'approval_required' | 'approval_denied'; id: number; tool: string; args: string; effect: string; reason: string }
  | { type: 'claim_check'; backed: boolean; retrying: boolean; claim: string }
  | { type: 'message'; content: string }
  | { type: 'error'; error: string; budget?: boolean }
  | { type: 'done'; conversation_id: number; cost_cents: number; tool_calls: number };

/** Feed it text as it arrives; it returns every complete event and keeps the partial tail for the next call. */
export class SSEParser {
  private buf = '';

  push(chunk: string): unknown[] {
    this.buf += chunk.replace(/\r\n/g, '\n');
    const out: unknown[] = [];
    let i: number;
    while ((i = this.buf.indexOf('\n\n')) >= 0) {
      const block = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 2);
      const data = block
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).replace(/^ /, ''))
        .join('\n');
      if (!data || data === '[DONE]') continue;
      try {
        out.push(JSON.parse(data));
      } catch {
        // a malformed event is skipped, never fatal
      }
    }
    return out;
  }
}

export type StreamHandle = { abort: () => void; done: Promise<void> };

/**
 * POST and read an event stream. XMLHttpRequest exposes partial responseText on progress in React Native and
 * browsers alike, which makes it the most portable way to stream without native modules.
 */
export function streamPost(
  url: string,
  headers: Record<string, string>,
  body: unknown,
  onEvent: (e: ChatEvent) => void,
  XHR: typeof XMLHttpRequest = XMLHttpRequest,
): StreamHandle {
  const xhr = new XHR();
  const parser = new SSEParser();
  let seen = 0;
  const drain = () => {
    const text = xhr.responseText || '';
    if (text.length > seen) {
      const chunk = text.slice(seen);
      seen = text.length;
      for (const e of parser.push(chunk)) onEvent(e as ChatEvent);
    }
  };
  const done = new Promise<void>((resolve, reject) => {
    xhr.open('POST', url);
    // setRequestHeader appends, so a header given twice would be sent as "a, b" (a doubled Content-Type is a 422).
    const all: Record<string, string> = { ...headers, 'Content-Type': 'application/json', Accept: 'text/event-stream' };
    for (const [k, v] of Object.entries(all)) xhr.setRequestHeader(k, v);
    xhr.onprogress = drain;
    xhr.onload = () => {
      if (xhr.status >= 400) {
        let detail = `HTTP ${xhr.status}`;
        try {
          const j = JSON.parse(xhr.responseText);
          detail = typeof j.detail === 'string' ? j.detail : detail;
        } catch {
          /* not JSON */
        }
        reject(new Error(detail));
        return;
      }
      drain();
      parser.push('\n\n');
      resolve();
    };
    xhr.onerror = () => reject(new Error('The connection to your computer dropped'));
    xhr.onabort = () => resolve();
    xhr.send(JSON.stringify(body));
  });
  return { abort: () => xhr.abort(), done };
}
