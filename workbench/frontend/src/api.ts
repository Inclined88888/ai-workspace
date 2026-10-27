export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e as { msg?: string }).msg ?? JSON.stringify(e)).join("; ");
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
    throw new ApiError(res.status, detail(body, `${res.status} ${res.statusText}`));
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

export type Model = { ref: string; model: string; provider: string; provider_id: number; free?: boolean; free_basis?: string; tools?: boolean | null;
  price_in?: number; price_out?: number; allowed?: boolean; blocked?: string };

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

export type FileMeta = { id: number; name: string; mime: string; size: number; note: string; created_at: string; chars: number; excerpt?: string };

/** Upload one file as the raw request body (the server keeps no multipart parser). */
export async function uploadFile(file: File): Promise<FileMeta> {
  const res = await fetch(`/api/files?name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": file.type || "application/octet-stream", "X-Workbench": "1" },
    body: file,
  });
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) throw new ApiError(res.status, detail(body, `${res.status} ${res.statusText}`));
  return body as FileMeta;
}

export function fileSize(n: number): string {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}

/** Send recorded speech for recognition. The audio goes as the raw body and is not kept by ROOKS. */
export async function transcribe(audio: Blob, conversationId: number | null): Promise<string> {
  const res = await fetch(`/api/voice/transcribe${conversationId ? `?conversation_id=${conversationId}` : ""}`, {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": audio.type || "audio/webm", "X-Workbench": "1" }, body: audio,
  });
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) throw new ApiError(res.status, detail(body, `${res.status} ${res.statusText}`));
  return String((body as { text?: string })?.text ?? "");
}

/** Speech for text from the connected speech provider, as audio. */
export async function speakAudio(text: string, conversationId: number | null, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch("/api/voice/speak", {
    method: "POST", credentials: "same-origin", signal,
    headers: { "Content-Type": "application/json", "X-Workbench": "1" }, body: JSON.stringify({ text, conversation_id: conversationId }),
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { msg = detail(await res.json(), msg); } catch { /* keep status text */ }
    throw new ApiError(res.status, msg);
  }
  return res.blob();
}
