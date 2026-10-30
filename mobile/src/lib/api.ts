/**
 * The phone's client for Workbench's /api/mobile/* routes (workbench/backend/app/mobile.py).
 * Every call carries the device token; writes also carry X-Workbench: 1 (Workbench's CSRF rule).
 * A pairing holds several addresses (Wi-Fi, the address Settings was opened on, a relay); the first that answers wins.
 */
import { ChatEvent, streamPost, StreamHandle } from './sse';

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

export type Approval = {
  id: number;
  conversation_id: number | null;
  tool: string;
  args: string;
  effect: string;
  reason: string;
  status: 'pending' | 'approved' | 'denied' | 'done' | 'failed';
  result: string;
  created_at: string;
  decided_at: string | null;
};

export type RunCard = {
  id: number;
  ts: string;
  kind: string;
  channel: string;
  outcome: string;
  model: string;
  tool: string;
  conversation_id: number | null;
  conversation_title: string | null;
  steps: number;
  tool_calls: number;
  failures: number;
  total_usd: number | null;
  total_tokens_in: number;
  total_tokens_out: number;
  latency_ms: number;
  failure_reason: string;
};

export type TraceNode = RunCard & { args: string; evidence: string; detail: string; verified: number | null; effect: string; children: TraceNode[] };
export type Conversation = { id: number; title: string; channel: string; updated_at: string };
export type Message = { id: number; role: 'user' | 'assistant' | 'tool'; content: string; tool_calls: string[]; name: string; created_at: string };
export type Task = { id: number; title: string; status: 'todo' | 'doing' | 'done'; priority: number; due: string | null; project: string };
export type Home = {
  workspace_name: string;
  approvals: Approval[];
  approvals_count: number;
  running: number;
  runs: RunCard[];
  tasks: Task[];
  notes: { id: number; title: string; excerpt: string; pinned: number; updated_at: string }[];
};
export type Model = { ref: string; name?: string; model?: string; provider?: string; free?: boolean };
export type Me = { id: number; name: string; platform: string; push: boolean; workspace_name: string; push_enabled_on_desktop: boolean };

type FetchLike = typeof fetch;

/* Called through a wrapper: browsers throw "Illegal invocation" when fetch is called as a method of another object. */
const defaultFetch: FetchLike = (input, init) => fetch(input, init);

export async function probe(url: string, f: FetchLike = defaultFetch, timeoutMs = 2500): Promise<string | null> {
  const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = setTimeout(() => ctl?.abort(), timeoutMs);
  try {
    const r = await f(`${url}/api/mobile/ping`, { signal: ctl?.signal });
    if (!r.ok) return null;
    const j = await r.json();
    return j && j.app === 'ai-workspace' ? url : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Try every address at once; resolve with the first in the caller's order that answers. */
export async function firstReachable(urls: string[], f: FetchLike = defaultFetch, timeoutMs = 2500): Promise<string | null> {
  const results = await Promise.all(urls.map((u) => probe(u, f, timeoutMs)));
  return results.find((r) => r) ?? null;
}

export async function claim(base: string, code: string, name: string, platform: string, f: FetchLike = defaultFetch) {
  const r = await f(`${base}/api/mobile/pair/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Workbench': '1' },
    body: JSON.stringify({ code, name, platform }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(typeof j.detail === 'string' ? j.detail : `Pairing failed (HTTP ${r.status})`, r.status);
  return j as { device_token: string; device_id: number; workspace_name: string };
}

export type DesktopAppearance = { theme: string; resolved: string; dark: boolean; font: string; size: number; motion: string; accent: string };

export class Client {
  constructor(public base: string, private token: string, private f: FetchLike = defaultFetch) {}

  private headers(write: boolean): Record<string, string> {
    const h: Record<string, string> = { Authorization: `Bearer ${this.token}`, Accept: 'application/json' };
    if (write) {
      h['X-Workbench'] = '1';
      h['Content-Type'] = 'application/json';
    }
    return h;
  }

  async req<T>(method: string, path: string, body?: unknown): Promise<T> {
    const write = method !== 'GET';
    let r: Response;
    try {
      r = await this.f(`${this.base}/api/mobile${path}`, {
        method,
        headers: this.headers(write),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new ApiError('Your computer is not reachable. Is it awake and on the same Wi-Fi?', 0);
    }
    const j = await r.json().catch(() => null);
    if (!r.ok) {
      const d = j && typeof j.detail === 'string' ? j.detail : `HTTP ${r.status}`;
      throw new ApiError(d, r.status);
    }
    return j as T;
  }

  me = () => this.req<Me>('GET', '/me');
  /** The look chosen on the computer (theme, typeface, text size), for "Same as computer". */
  appearance = () => this.req<DesktopAppearance>('GET', '/appearance');
  home = () => this.req<Home>('GET', '/home');
  approvals = () => this.req<Approval[]>('GET', '/approvals');
  decide = (id: number, approve: boolean) => this.req<Approval>('POST', `/approvals/${id}`, { approve });
  runs = (before?: number, failed = false) =>
    this.req<{ runs: RunCard[] }>('GET', `/runs?limit=30${before ? `&before=${before}` : ''}${failed ? '&failed=true' : ''}`);
  trace = (id: number) => this.req<TraceNode>('GET', `/runs/${id}`);
  conversations = () => this.req<Conversation[]>('GET', '/conversations');
  conversation = (id: number) => this.req<Conversation & { messages: Message[]; model: string | null }>('GET', `/conversations/${id}`);
  models = () => this.req<{ models: Model[]; default: string }>('GET', '/models');
  tasks = () => this.req<Task[]>('GET', '/tasks');
  setTask = (id: number, status: Task['status']) => this.req<Task>('PATCH', `/tasks/${id}`, { status });
  pushToken = (token: string) => this.req<{ ok: boolean; push_enabled_on_desktop: boolean }>('PUT', '/push-token', { token });
  unpair = () => this.req<{ ok: boolean }>('DELETE', '/me');
  // feat/v02-automations: background jobs board and the Share box
  jobs = () => this.req<import('./jobs').Board>('GET', '/jobs');
  jobAction = (id: number, action: import('./jobs').JobAction) => this.req<import('./jobs').Job>('POST', `/jobs/${id}/${action}`);
  share = (b: { text: string; url: string; title?: string }) => this.req<{ fired: number }>('POST', '/share', b);
  attach = (a: { kind: 'photo' | 'voice'; mime: string; name: string; duration_ms?: number; data: string }) =>
    this.req<{ id: string; kind: string; file: string; bytes: number }>('POST', '/attachments', a);

  chat(
    body: { message: string; conversation_id?: number | null; model?: string | null; attachments?: string[] },
    onEvent: (e: ChatEvent) => void,
    idempotencyKey?: string,
    XHR?: typeof XMLHttpRequest,
  ): StreamHandle {
    const h = this.headers(true);
    if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey;
    return streamPost(`${this.base}/api/mobile/chat`, h, body, onEvent, XHR);
  }
}

export function newKey(): string {
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
