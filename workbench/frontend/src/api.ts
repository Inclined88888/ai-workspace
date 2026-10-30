export class ApiError extends Error {
  status: number;
  info?: Record<string, unknown>;
  constructor(status: number, message: string, info?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.info = info;
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e as { msg?: string }).msg ?? JSON.stringify(e)).join("; ");
    if (d && typeof d === "object" && typeof (d as { message?: unknown }).message === "string") return (d as { message: string }).message;
  }
  return fallback;
}

export async function api<T = unknown>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = options.method ?? (options.body === undefined ? "GET" : "POST");
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Workbench": "1" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/login") && !path.startsWith("/password")) {
      window.dispatchEvent(new Event("wb:unauthorized"));
    }
    const info = (body as { detail?: unknown } | null)?.detail;
    const obj = info && typeof info === "object" && !Array.isArray(info) ? (info as Record<string, unknown>) : undefined;
    // A Pro feature on a Free plan: the app shows one friendly upgrade card (ProGate.tsx), wherever the call came from.
    if (res.status === 402 && obj?.error === "upgrade_required") window.dispatchEvent(new CustomEvent("wb:upgrade", { detail: obj }));
    throw new ApiError(res.status, detail(body, `${res.status} ${res.statusText}`), obj);
  }
  return body as T;
}

export type StreamEvent = {
  delta?: string;
  reasoning?: string;
  error?: string;
  done?: boolean;
  usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
  ms?: number;
  first_token_ms?: number | null;
  [key: string]: unknown;
};

/** POST to an SSE endpoint and call onEvent for every `data:` message. */
export async function stream(path: string, body: unknown, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Workbench": "1" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      msg = detail(await res.json(), msg);
    } catch {
      /* keep status text */
    }
    onEvent({ error: msg });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data:")) {
          try {
            onEvent(JSON.parse(line.slice(5).trim()));
          } catch {
            /* ignore malformed line */
          }
        }
      }
    }
  }
}

export type Reliability = { calls: number; success_rate: number | null; false_claims_caught?: number; false_claims_left?: number };
export type Model = { ref: string; model: string; provider: string; provider_id: number; free?: boolean; reliability?: Reliability | null };

export type Doc = {
  id: number;
  kind: "note" | "document";
  title: string;
  body: string;
  tags: string;
  pinned: number;
  created_at: string;
  updated_at: string;
  preview?: string;
};

export type Task = {
  id: number;
  title: string;
  notes: string;
  status: "todo" | "doing" | "done";
  priority: number;
  due: string | null;
  project: string;
  created_at: string;
  completed_at: string | null;
};

export type CalEvent = {
  id: number;
  calendar_id: number;
  title: string;
  start: string;
  end: string;
  all_day: number;
  location: string;
  description: string;
  calendar_name: string;
  color: string;
  calendar_kind: string;
};

export type Calendar = {
  id: number;
  name: string;
  kind: "local" | "ics" | "caldav";
  url: string;
  username: string;
  color: string;
  enabled: number;
  last_synced: string | null;
  last_error: string | null;
};
