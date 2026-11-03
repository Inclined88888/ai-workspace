// feat/grok-style: the Vault. Logins, cards, addresses and notes. Secrets are masked; reveal is for you only.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import "./teammates.css";

type Item = { id: number; kind: string; label: string; fields: Record<string, string>; secret_hints: Record<string, string>; teammates?: number[] };
type Kinds = Record<string, { label: string; fields: { id: string; label: string; secret: boolean }[] }>;
type Listing = { items: Item[]; backend: string; kinds: Kinds };

export default function Vault() {
  const [data, setData] = useState<Listing | null>(null);
  const [kind, setKind] = useState("login");
  const [label, setLabel] = useState("");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [shown, setShown] = useState<Record<number, Record<string, string>>>({});
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Listing>("/vault").then(setData).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  const fields = data?.kinds?.[kind]?.fields ?? [];
  return <div className="tm-page">
    <header><h1>Vault</h1><p className="tm-muted">Saved logins, cards and addresses your teammates can use without ever seeing them.
      Stored {data?.backend === "keychain" ? "in your system keychain" : "encrypted on this computer"}.</p></header>
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Add to Vault</b></div>
      <div className="tm-row">{Object.entries(data?.kinds ?? {}).map(([k, v]) => <button key={k} className={`btn sm ${k === kind ? "primary" : "ghost"}`} onClick={() => { setKind(k); setVals({}); }}>{v.label}</button>)}</div>
      <form onSubmit={async (e) => { e.preventDefault(); setErr("");
        try { await api("/vault", { body: { kind, label, values: vals } }); setLabel(""); setVals({}); load(); } catch (x) { setErr((x as Error).message); } }}>
        <label>Name<input value={label} placeholder="e.g. Supplier portal" onChange={(e) => setLabel(e.target.value)} /></label>
        {fields.map((f) => <label key={f.id}>{f.label}<input type={f.secret ? "password" : "text"} autoComplete="off" value={vals[f.id] ?? ""}
          onChange={(e) => setVals({ ...vals, [f.id]: e.target.value })} /></label>)}
        {err && <p className="tm-err">{err}</p>}
        <button className="btn primary sm" type="submit" disabled={!label.trim()}>Save</button>
      </form>
    </section>
    <ul className="tm-grid">{data?.items.map((it) => <li key={it.id} className="tm-card">
      <div className="tm-card-head"><b>{it.label}</b><span className="tm-pill">{data.kinds[it.kind]?.label ?? it.kind}</span></div>
      {Object.entries(it.fields).filter(([, v]) => v).map(([k, v]) => <p key={k} className="tm-muted">{k}: {v}</p>)}
      {Object.entries(shown[it.id] ? Object.fromEntries(Object.keys(it.secret_hints).map((k) => [k, shown[it.id][k]])) : it.secret_hints).map(([k, v]) =>
        <p key={k} className="tm-muted">{k}: <code>{v}</code></p>)}
      <div className="tm-row">
        <button className="btn ghost sm" onClick={async () => {
          if (shown[it.id]) { const s = { ...shown }; delete s[it.id]; setShown(s); return; }
          const r = await api<{ values: Record<string, string> }>(`/vault/${it.id}/reveal`, { method: "POST" }); setShown({ ...shown, [it.id]: r.values });
        }}>{shown[it.id] ? "Hide" : "Reveal"}</button>
        <button className="btn ghost sm" onClick={async () => { if (confirm(`Delete ${it.label}?`)) { await api(`/vault/${it.id}`, { method: "DELETE" }); load(); } }}>Delete</button>
      </div>
    </li>)}</ul>
    {data && !data.items.length && <p className="tm-empty">Nothing saved yet. When a teammate needs a login it asks with a secure form in the chat.</p>}
  </div>;
}
