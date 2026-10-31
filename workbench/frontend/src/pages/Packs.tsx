// Packs gallery: one-click add, through the skills security gate (feat/v02-packs).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api } from "../api";
import { tierLabel } from "../packs/logic";
import { useToast } from "../util";
import "../packs/packs.css";

type Gate = Record<string, { risk: string; findings: number; extra_permissions: string[] }>;
export type Pack = {
  id: string; name: string; tagline: string; for: string; tier: string; page: string; includes: string[]; version: string;
  skills: { name: string; description: string }[]; installed: boolean; allowed: boolean; gate: Gate;
};

export default function Packs() {
  const [packs, setPacks] = useState<Pack[] | null>(null);
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const load = () => api<Pack[]>("/packs").then(setPacks).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (p: Pack, what: "add" | "remove") => {
    setBusy(p.id);
    try {
      await api(`/packs/${p.id}/${what}`, { method: "POST" });
      toast.ok(what === "add" ? `${p.name} added. Every skill passed the security check.` : `${p.name} removed. Your saved work is kept.`);
      await load();
    } catch (e) { toast.err(e); }
    setBusy("");
  };

  return (
    <div className="page" style={{ maxWidth: 1120 }}>
      <div className="page-head">
        <div>
          <p className="kicker">Your work</p>
          <h1>Packs</h1>
          <p className="muted">Shortcuts for the work you do. Add one in a click, remove it any time. Your assistant works the same without them.</p>
        </div>
      </div>
      <div className="pk-gallery">
        {(packs ?? []).map((p, i) => (
          <section key={p.id} className="pk-tile" aria-labelledby={`pk-${p.id}`}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="pk-num" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
              <span className={`badge ${p.tier === "free" ? "ok" : "accent"}`}>{tierLabel(p.tier, p.allowed)}</span>
            </div>
            <h2 id={`pk-${p.id}`} className="pk-serif">{p.name}</h2>
            <p className="muted" style={{ margin: 0 }}>{p.tagline}</p>
            <p className="label" style={{ margin: 0 }}>For {p.for.toLowerCase()}</p>
            <ul>{p.includes.map((x) => <li key={x}>{x}</li>)}</ul>
            {p.installed && (
              <div className="pk-gate-result" aria-label="Security check">
                <span className="ok"><ShieldCheck size={12} /> {Object.keys(p.gate).length} skills passed the security check</span>
                {Object.entries(p.gate).map(([n, g]) => <span key={n}>{n}: {g.risk} risk, {g.findings} findings</span>)}
              </div>
            )}
            <div className="pk-foot">
              {p.installed ? <>
                <Link className="btn primary" to={p.page}>Open</Link>
                <button className="btn ghost" disabled={busy === p.id} onClick={() => act(p, "remove")}>Remove</button>
              </> : (
                <button className="btn primary" disabled={!!busy} onClick={() => act(p, "add")}>{busy === p.id ? "Checking…" : "Add"}</button>
              )}
            </div>
          </section>
        ))}
      </div>
      {toast.node}
    </div>
  );
}
