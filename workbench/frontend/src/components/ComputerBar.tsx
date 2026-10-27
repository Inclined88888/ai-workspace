import { Eye, Hand, MonitorPlay, OctagonX } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type S = { settings: { enabled: boolean; stopped: boolean; paused: boolean }; current: { kind: string; summary: string } | null;
  screen_shared?: { app?: string; destination?: string } };

/** Shown on every page while ROOKS is acting on your computer: what it is doing, and a Stop that works at once. */
export default function ComputerBar() {
  const [s, setS] = useState<S | null>(null);
  useEffect(() => {
    let stop = false;
    let t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try { setS(await api<S>("/computer")); } catch { /* signed out or offline: show nothing */ }
      if (!stop) t = setTimeout(tick, 2000);
    };
    tick();
    return () => { stop = true; clearTimeout(t); };
  }, []);
  if (s?.screen_shared?.app && !s.current) {
    // the screenshot indicator: shown for 15 seconds after any screenshot leaves for the vision model
    return <div className="computer-bar" role="status" aria-live="polite"><Eye size={15} aria-hidden="true" />
      <span className="grow truncate">Screen shared: {s.screen_shared.app} went to {s.screen_shared.destination}</span>
      <Link to="/computer" className="btn ghost sm">Details</Link></div>;
  }
  if (!s || !s.settings.enabled || !s.current || s.settings.stopped) return null;
  const act = async (path: string) => { try { await api(`/computer/${path}`, { method: "POST" }); setS(await api<S>("/computer")); } catch { /* the Computer page shows errors */ } };
  return (
    <div className="computer-bar" role="status" aria-live="polite">
      <MonitorPlay size={15} aria-hidden="true" />
      <span className="grow truncate">ROOKS is using your computer: <strong>{s.current.kind}</strong> {s.current.summary}
        {s.screen_shared?.app ? ` · screen shared with ${s.screen_shared.destination}` : ""}</span>
      <Link to="/computer" className="btn ghost sm">Watch</Link>
      <button className="btn sm" onClick={() => act("take-control")}><Hand size={13} /> Take control</button>
      <button className="btn danger sm" onClick={() => act("stop")}><OctagonX size={13} /> Stop</button>
    </div>
  );
}
