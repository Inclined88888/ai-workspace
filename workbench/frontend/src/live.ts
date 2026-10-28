/** One live connection to ROOKS (GET /api/v1/events) for the whole app. It opens with a complete baseline and then sends
 *  what changed; each part of the interface listens for the window event it cares about and reloads its own data:
 *    rooks:convs   the conversation list changed (a chat started in the terminal or desktop app shows up here)
 *    wb:attention  something needs your review
 *    rooks:work    work changed; detail is the changed items in their typed phase
 *  The browser reconnects by itself; every reconnect starts with a new baseline, so nothing is missed or replayed. */
export type WorkItem = { id: string; kind: string; title: string; phase: string; status_text: string; native_status: string;
  progress: string; error: string; terminal: boolean; conversation_id: number | null };

export function startLive(): () => void {
  if (typeof EventSource === "undefined") return () => undefined;
  const es = new EventSource("/api/v1/events");
  const fire = (name: string, detail?: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
  let reconnect = false;  // the first baseline matches what the page just loaded; later ones follow a reconnect
  es.onmessage = (m) => {
    let d: { type: string; items?: WorkItem[]; work?: WorkItem[] };
    try { d = JSON.parse(m.data); } catch { return; }
    if (d.type === "baseline") {
      if (reconnect) { fire("rooks:convs"); fire("wb:attention"); }
      fire("rooks:work", d.work ?? []);
      reconnect = true;
    } else if (d.type === "conversations") fire("rooks:convs");
    else if (d.type === "needs_review") fire("wb:attention");
    else if (d.type === "work") fire("rooks:work", d.items ?? []);
  };
  return () => es.close();
}
