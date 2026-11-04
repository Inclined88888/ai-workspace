// A real terminal in the app window. Each tab is its own shell and stays running when you switch tabs.
// The server allows six at once (terminal_pty.MAX_SESSIONS). xterm.js talks to /api/terminal/ws.
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Plus, SquareTerminal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import "../work.css";

const MAX = 6;
const REASON: Record<number, string> = {
  4401: "You are signed out. Sign in again, then reopen the terminal.",
  4403: "The terminal only opens on the computer running Rooks, and only when it is switched on in Settings.",
  4429: "Too many terminals are open. Close one and try again.",
};

type Phase = "connecting" | "open" | "closed";

function Shell({ active, label, onPhase, onSend }: {
  active: boolean;
  label: string;
  onPhase: (phase: Phase, why: string) => void;
  onSend: (send: (cmd: string) => void) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [why, setWhy] = useState("");
  const [gen, setGen] = useState(0);

  useEffect(() => {
    if (!box.current) return;
    const css = getComputedStyle(document.documentElement);
    const term = new Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 5000, screenReaderMode: true,
      fontFamily: css.getPropertyValue("--mono").trim() || "ui-monospace, Menlo, monospace",
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(box.current);
    termRef.current = term;
    fitRef.current = fit;
    try { fit.fit(); } catch { /* hidden or not laid out yet */ }
    setPhase("connecting");
    setWhy("");
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/ws`);
    const send = (m: object) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m)); };
    const run = (cmd: string) => send({ type: "input", data: cmd + "\r" });
    onSend(run);
    ws.onopen = () => {
      setPhase("open");
      send({ type: "resize", cols: term.cols, rows: term.rows });
      if (active) term.focus();
    };
    ws.onmessage = (e) => term.write(typeof e.data === "string" ? e.data : "");
    ws.onclose = (e) => { setPhase("closed"); setWhy(REASON[e.code] ?? (e.reason || "The shell exited.")); };
    const input = term.onData((d) => send({ type: "input", data: d }));
    const resized = term.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
    const ro = new ResizeObserver(() => { try { fit.fit(); } catch { /* hidden */ } });
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      input.dispose();
      resized.dispose();
      ws.close();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [gen]);

  const report = useRef(onPhase);
  report.current = onPhase;
  useEffect(() => { if (active) report.current(phase, why); }, [phase, why, active]);

  useEffect(() => {
    if (!active) return;
    try { fitRef.current?.fit(); } catch { /* not open yet */ }
    termRef.current?.focus();
  }, [active, phase]);

  return (
    <div className={`terminal-pane${active ? "" : " is-hidden"}`} aria-hidden={!active}>
      <div role="status" aria-live="polite" className="small">{phase === "connecting" ? "Connecting…" : phase === "closed" ? why : ""}</div>
      <div ref={box} className="terminal-box" aria-label={label} />
      {phase === "closed" && (
        <button type="button" className="btn sm" onClick={() => setGen((n) => n + 1)}>Reopen this terminal</button>
      )}
    </div>
  );
}

function TabChip({ pane, active, over, onPick, onRename, onClose, onDragStart, onDragOver, onDrop, onDragEnd }: {
  pane: { id: number; name: string }; active: boolean; over: boolean;
  onPick: () => void; onRename: (name: string) => void; onClose: () => void;
  onDragStart: () => void; onDragOver: () => void; onDrop: () => void; onDragEnd: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(pane.name);
  const commit = () => { setEditing(false); onRename(draft); setDraft((d) => d.trim() || pane.name); };
  return (
    <div className={`tab terminal-tab${active ? " active" : ""}${over ? " is-over" : ""}`}
      draggable={!editing}
      onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(pane.id)); e.dataTransfer.effectAllowed = "move"; onDragStart(); }}
      onDragOver={(e) => { e.preventDefault(); onDragOver(); }}
      onDrop={(e) => { e.preventDefault(); onDrop(); }}
      onDragEnd={onDragEnd}>
      {editing ? (
        <input className="terminal-tab-rename" autoFocus aria-label="Terminal name" value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } if (e.key === "Escape") { setDraft(pane.name); setEditing(false); } }} />
      ) : (
        <button type="button" role="tab" aria-selected={active} className="terminal-tab-name" title="Double-click to rename. Drag to move."
          onClick={onPick} onDoubleClick={() => { setDraft(pane.name); setEditing(true); }}>{pane.name}</button>
      )}
      <button type="button" className="terminal-tab-x" aria-label={`Close ${pane.name}`} title="Close this terminal" onClick={onClose}><X size={12} /></button>
    </div>
  );
}

type Pane = { id: number; name: string };
const STORE = "aiw.terminals";

function readSaved(): { panes: Pane[]; active: number; next: number } | null {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || "null");
    if (!raw || !Array.isArray(raw.panes) || !raw.panes.length) return null;
    const panes = raw.panes.filter((p: Pane) => p && typeof p.id === "number" && typeof p.name === "string").slice(0, MAX);
    if (!panes.length) return null;
    const active = panes.some((p: Pane) => p.id === raw.active) ? raw.active : panes[0].id;
    const next = Math.max(raw.next || 1, ...panes.map((p: Pane) => p.id)) + 1;
    return { panes, active, next };
  } catch {
    return null;
  }
}

export default function TerminalPage() {
  const saved = useRef(readSaved());
  const [panes, setPanes] = useState<Pane[]>(saved.current?.panes ?? [{ id: 1, name: "Terminal 1" }]);
  const [active, setActive] = useState(saved.current?.active ?? 1);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [over, setOver] = useState<number | null>(null);
  const next = useRef(saved.current?.next ?? 2);
  const dragId = useRef<number | null>(null);
  const senders = useRef(new Map<number, (cmd: string) => void>());
  const full = panes.length >= MAX;

  useEffect(() => {
    localStorage.setItem(STORE, JSON.stringify({ panes, active, next: next.current }));
  }, [panes, active]);

  const add = () => {
    if (full) return;
    const id = next.current++;
    setPanes((list) => [...list, { id, name: `Terminal ${list.length + 1}` }]);
    setActive(id);
    setPhase("connecting");
  };
  const close = (id: number) => {
    senders.current.delete(id);
    const rest = panes.filter((p) => p.id !== id);
    if (!rest.length) {
      const fresh = next.current++;
      setPanes([{ id: fresh, name: "Terminal 1" }]);
      setActive(fresh);
      return;
    }
    setPanes(rest);
    if (active === id) setActive(rest[rest.length - 1].id);
  };
  const rename = (id: number, name: string) => {
    const clean = name.trim().slice(0, 40);
    if (!clean) return;
    setPanes((list) => list.map((p) => (p.id === id ? { ...p, name: clean } : p)));
  };
  const move = (from: number, to: number) => {
    if (from === to) return;
    setPanes((list) => {
      const nextList = list.slice();
      const i = nextList.findIndex((p) => p.id === from);
      const j = nextList.findIndex((p) => p.id === to);
      if (i < 0 || j < 0) return list;
      const [item] = nextList.splice(i, 1);
      nextList.splice(j, 0, item);
      return nextList;
    });
  };

  return (
    <div className="page terminal-page">
      <div className="page-head row">
        <SquareTerminal size={18} aria-hidden />
        <h1 className="grow">Terminal</h1>
        <button className="btn sm" onClick={() => senders.current.get(active)?.("rooks")} disabled={phase !== "open"} title="Start the rooks coding agent in this terminal">Start rooks</button>
      </div>
      <div className="muted small">Drag a name to move it. Double-click a name to rename it. Each one keeps running when you switch. Up to six.</div>
      <div className="terminal-tabs">
        <div className="tabs" role="tablist" aria-label="Terminals">
          {panes.map((p) => (
            <TabChip key={p.id} pane={p} active={p.id === active} over={over === p.id}
              onPick={() => setActive(p.id)} onRename={(name) => rename(p.id, name)} onClose={() => close(p.id)}
              onDragStart={() => { dragId.current = p.id; }}
              onDragOver={() => setOver(p.id)}
              onDrop={() => { if (dragId.current != null) move(dragId.current, p.id); dragId.current = null; setOver(null); }}
              onDragEnd={() => { dragId.current = null; setOver(null); }} />
          ))}
        </div>
        <button type="button" className="btn sm" onClick={add} disabled={full} title={full ? "Six terminals can run at once. Close one to open another." : "Open another shell. The ones already open keep running."}>
          <Plus size={14} /> New terminal
        </button>
      </div>
      <div className="terminal-stage">
        {panes.map((p) => (
          <Shell key={p.id} active={p.id === active} label={p.name}
            onPhase={(ph, _why) => { if (p.id === active) setPhase(ph); }}
            onSend={(send) => { senders.current.set(p.id, send); }} />
        ))}
      </div>
    </div>
  );
}
