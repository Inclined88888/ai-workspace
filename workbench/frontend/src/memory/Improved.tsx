// feat/memory-core: "What I improved" on Home - last night's note, suggestions you approve, and the on/off switch.
import { Check, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import "./memory.css";

type Item = { id: number; day: string; title: string; detail: string; status: string };
type Data = { enabled: boolean; idle_minutes: number; last_run: string; note: Item | null; proposals: Item[] };

export function ImprovedCard() {
  const [d, setD] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api<Data>("/knowledge/improvements").then(setD).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  if (!d) return null;
  const toggle = async () => setD(await api<Data>("/knowledge/improvements/settings", { method: "PUT", body: { enabled: !d.enabled } }));
  const decide = async (p: Item, choice: "approve" | "decline") => { await api(`/knowledge/improvements/${p.id}/${choice}`, { body: {} }); load(); };
  const runNow = async () => { setBusy(true); try { await api("/knowledge/improvements/run", { body: {} }); await load(); } finally { setBusy(false); } };
  return (
    <section className="card kn-improved kn-enter" aria-label="What I improved" style={{ marginBottom: 24 }}>
      <div className="row" style={{ alignItems: "center", gap: 10 }}>
        <Sparkles size={17} style={{ color: "var(--accent-text)" }} />
        <h3 style={{ margin: 0 }}>What I improved</h3>
        <span className="muted small">{d.note ? `overnight, ${d.note.day}` : ""}</span>
        <div className="spacer" style={{ flex: 1 }} />
        <label className="row small muted" style={{ gap: 6, alignItems: "center" }} title={`Runs once a night while your computer is on and you've been away ${d.idle_minutes} minutes`}>
          <input type="checkbox" checked={d.enabled} onChange={toggle} /> Get better overnight
        </label>
      </div>
      <p style={{ margin: "10px 0 0" }}>{d.note ? d.note.detail : d.enabled ? "Tonight, while you're away, I'll look back over the day and get a little better at your work." : "Overnight improvement is off."}</p>
      {d.proposals.map((p) => (
        <div key={p.id} className="kn-proposal kn-enter">
          <div style={{ flex: 1 }}><b>{p.title}?</b><div className="muted small">{p.detail} This changes how I act for you, so it's your call.</div></div>
          <button className="btn sm primary" onClick={() => decide(p, "approve")}><Check size={14} /> Yes</button>
          <button className="btn sm ghost" onClick={() => decide(p, "decline")}><X size={14} /> No thanks</button>
        </div>
      ))}
      {!d.note && d.enabled && <button className="btn sm ghost" style={{ marginTop: 8 }} disabled={busy} onClick={runNow}>{busy ? "Looking back…" : "Do it now"}</button>}
    </section>
  );
}
