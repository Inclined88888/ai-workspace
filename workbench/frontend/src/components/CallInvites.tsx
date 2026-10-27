import { Clock, Phone, PhoneOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

type Invite = { id: number; title: string; briefing: string; expires_at: string; snoozes: number };

/** ROOKS asking to talk: accept opens a voice session that starts with the briefing; decline or let it lapse and the
 * briefing arrives in writing instead, once. Nothing rings or listens until you accept. */
export default function CallInvites() {
  const [invites, setInvites] = useState<Invite[]>([]);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const load = () => api<{ pending: Invite[] }>("/calls").then((r) => setInvites(r.pending)).catch(() => undefined);
  useEffect(() => {
    load();
    const t = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, 30000);
    const onVis = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  if (!invites.length) return null;
  const i = invites[0];
  const act = async (what: "accept" | "decline" | "snooze") => {
    setError("");
    try {
      const r = await api<{ conversation_id: number; speak?: string }>(`/calls/invites/${i.id}/${what}`, { body: what === "snooze" ? { minutes: 30 } : {} });
      setInvites((l) => l.filter((x) => x.id !== i.id));
      if (what === "accept") navigate(`/assistant/${r.conversation_id}`, { state: { voice: true, speak: r.speak } });
    } catch (e) { setError((e as Error).message); load(); }
  };
  return (
    <div className="invite-bar" role="alert" aria-label="ROOKS would like to talk">
      <Phone size={16} aria-hidden="true" />
      <div className="grow">
        <strong>ROOKS would like to talk: {i.title}</strong>
        <div className="small">Accept to hear it and reply by voice; otherwise you get it in writing.
          {invites.length > 1 ? ` ${invites.length - 1} more waiting.` : ""}</div>
        {error && <div className="small" style={{ color: "var(--danger)" }}>{error}</div>}
      </div>
      <button type="button" className="btn primary sm" onClick={() => act("accept")}><Phone size={13} /> Accept</button>
      {i.snoozes < 2 && <button type="button" className="btn sm" onClick={() => act("snooze")}><Clock size={13} /> In 30 minutes</button>}
      <button type="button" className="btn sm" onClick={() => act("decline")}><PhoneOff size={13} /> Decline</button>
    </div>
  );
}
