// #7: a real terminal in the app window (and in a browser on the same computer). xterm.js on /api/terminal/ws, which
// runs your login shell on this machine. `rooks` runs here like in any terminal, linked to the same conversations.
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Plus, RotateCcw, SquareTerminal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import "../work.css";

const REASON: Record<number, string> = {
  4401: "You are signed out. Sign in again, then reopen the terminal.",
  4403: "The terminal only opens on the computer running AI Workspace, and only when it is switched on in Settings.",
  4429: "Too many terminals are open. Close one and try again.",
};

export default function TerminalPage() {
  const box = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"connecting" | "open" | "closed">("connecting");
  const [why, setWhy] = useState("");
  const [gen, setGen] = useState(0);
  const sock = useRef<WebSocket | null>(null);

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
    fit.fit();
    setState("connecting");
    setWhy("");
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal/ws`);
    sock.current = ws;
    const send = (m: object) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m)); };
    ws.onopen = () => { setState("open"); send({ type: "resize", cols: term.cols, rows: term.rows }); term.focus(); };
    ws.onmessage = (e) => term.write(typeof e.data === "string" ? e.data : "");
    ws.onclose = (e) => { setState("closed"); setWhy(REASON[e.code] ?? (e.reason || "The shell exited.")); };
    const input = term.onData((d) => send({ type: "input", data: d }));
    const resized = term.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
    const ro = new ResizeObserver(() => { try { fit.fit(); } catch { /* hidden */ } });
    ro.observe(box.current);
    return () => { ro.disconnect(); input.dispose(); resized.dispose(); ws.close(); term.dispose(); };
  }, [gen]);

  const run = (cmd: string) => sock.current?.readyState === WebSocket.OPEN && sock.current.send(JSON.stringify({ type: "input", data: cmd + "\r" }));

  return (
    <div className="page terminal-page">
      <div className="page-head row">
        <SquareTerminal size={18} aria-hidden />
        <h1 className="grow">Terminal</h1>
        <button className="btn sm" onClick={() => run("rooks")} disabled={state !== "open"} title="Start the rooks coding agent in this terminal">Start rooks</button>
        <button className="btn sm ghost" onClick={() => setGen(gen + 1)} title="Close this shell and open a new one">{state === "closed" ? <><RotateCcw size={14} /> Reopen</> : <><Plus size={14} /> New shell</>}</button>
      </div>
      <div className="muted small">Your own shell on this computer, in your home folder. It runs with your account's rights and without the app's keys. Closing the page ends it and anything it started.</div>
      <div role="status" aria-live="polite" className="small">{state === "connecting" ? "Connecting…" : state === "closed" ? why : ""}</div>
      <div ref={box} className="terminal-box" aria-label="Terminal" />
    </div>
  );
}
