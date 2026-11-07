// Your work: pick a professional workspace once (Trade, Legal, Study and research or General). It adds the right tools
// and tells the assistant how that work is done, in Chat, the terminal (`rooks work`) and chat apps alike.
// One memory either way: nothing is split or hidden when you switch.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check } from "lucide-react";
import { api, ApiError } from "../api";
import { tierLabel } from "../packs/logic";
import { useToast } from "../util";
import "../packs/packs.css";

export type Workspace = {
  id: string; name: string; for: string; pack: string | null; project_kind: string; starters: string[];
  active: boolean; tier: string; allowed: boolean; pack_added: boolean; page: string | null;
};

export default function Work() {
  const [list, setList] = useState<Workspace[] | null>(null);
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const navigate = useNavigate();
  const load = () => api<Workspace[]>("/workspaces").then(setList).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = async (w: Workspace) => {
    setBusy(w.id);
    try {
      const got = await api<Workspace>("/workspaces/active", { method: "POST", body: { id: w.id } });
      toast.ok(got.pack ? `Now set up for ${got.name}. Its tools passed the security check and are ready.` : `Now set up for ${got.name}.`);
      await load();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 402)) toast.err(e); // 402: the app-wide upgrade card is already showing
    }
    setBusy("");
  };

  const active = list?.find((w) => w.active);
  return (
    <div className="page" style={{ maxWidth: 1120 }}>
      <div className="page-head">
        <div>
          <p className="kicker">Your work</p>
          <h1>What kind of work do you do?</h1>
          <p className="muted">
            Pick one and your assistant works that way everywhere: here, in the terminal and in your chat apps. It keeps one
            memory, so nothing you told it is lost when you switch.
          </p>
        </div>
      </div>
      {active && active.pack && !active.pack_added && (
        <p className="card muted" role="status">The {active.name} tools were removed. Choose {active.name} again to add them back.</p>
      )}
      <div className="pk-gallery">
        {(list ?? []).map((w) => (
          <section key={w.id} className="pk-tile" aria-labelledby={`ws-${w.id}`}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              {w.active ? <span className="badge ok"><Check size={12} /> In use</span> : <span />}
              <span className={`badge ${w.tier === "free" ? "ok" : "accent"}`}>{tierLabel(w.tier, w.allowed)}</span>
            </div>
            <h2 id={`ws-${w.id}`} className="pk-serif">{w.name}</h2>
            <p className="muted" style={{ margin: 0 }}>{w.for}</p>
            <p className="label" style={{ margin: 0 }}>Things to ask</p>
            <ul>{w.starters.map((s) => <li key={s}>{s}</li>)}</ul>
            <div className="pk-foot">
              {w.active ? (<>
                <button className="btn primary" onClick={() => navigate("/assistant", { state: { draft: w.starters[0] ?? "" } })}>Ask something</button>
                {w.page && w.pack_added && <Link className="btn ghost" to={w.page}>Open {w.name} tools</Link>}
              </>) : (
                <button className="btn primary" disabled={!!busy} onClick={() => choose(w)}>
                  {busy === w.id ? (w.pack ? "Checking the tools…" : "Switching…") : "Use this"}
                </button>
              )}
            </div>
          </section>
        ))}
      </div>
      {toast.node}
    </div>
  );
}
