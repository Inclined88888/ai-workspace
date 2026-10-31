// Desktop notifications for the jobs board (feat/v02-automations): a job needs your OK, finished, or failed.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import "./v02.css";

type Notice = { id: number; job_id: number; kind: "needs_you" | "finished" | "failed"; title: string; body: string; at: string };

export default function JobNotifier() {
  const last = useRef<number | null>(null);
  const [pill, setPill] = useState<Notice | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let stop = false;
    const poll = async () => {
      try {
        const list = await api<Notice[]>(`/jobs/notices?after=${last.current ?? 0}`);
        if (last.current === null) { // first look: only remember where we are
          last.current = list.length ? list[list.length - 1].id : 0;
          if (list.length === 50) last.current = (await api<Notice[]>(`/jobs/notices?after=${last.current}`)).pop()?.id ?? last.current;
          return;
        }
        for (const n of list) {
          last.current = Math.max(last.current, n.id);
          if (stop) return;
          setPill(n);
          if (typeof Notification !== "undefined" && Notification.permission === "granted" && (document.hidden || !document.hasFocus())) {
            const note = new Notification(n.title, { body: n.body, tag: `job-${n.job_id}` });
            note.onclick = () => { window.focus(); navigate("/jobs"); note.close(); };
          }
        }
      } catch { /* signed out or offline: try again later */ }
    };
    void poll();
    const t = setInterval(poll, 5000);
    return () => { stop = true; clearInterval(t); };
  }, [navigate]);

  useEffect(() => {
    if (!pill) return;
    const t = setTimeout(() => setPill(null), 8000);
    return () => clearTimeout(t);
  }, [pill]);

  if (!pill) return null;
  return (
    <div className={`job-pill ${pill.kind}`} role="status">
      <button className="job-pill-main" onClick={() => { navigate("/jobs"); setPill(null); }}>
        <strong>{pill.title}</strong>{pill.body && <span className="small">{pill.body}</span>}
      </button>
      <button className="btn ghost sm icon" aria-label="Dismiss" onClick={() => setPill(null)}>×</button>
    </div>
  );
}
