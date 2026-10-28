import { Check, Loader2, Minus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type Cap = { label: string; state: "passed" | "failed" | "not_tested"; at: string; detail: string };
type Entry = {
  id: string; category: "A" | "B" | "C" | "D" | "E"; name: string; status: string; status_detail: string; billing: string; billing_detail: string;
  model?: string; capabilities?: Record<string, Cap>; mode_block?: string; provider_id?: number; kind?: string; engine?: string; engine_detail?: string;
  powers_chat?: boolean; setup_text?: string; experimental?: boolean; limits?: string; licence?: string; host?: string;
};
type Dir = { mode: string; groups: Record<string, string>; entries: Entry[]; note: string };

const STATUS: Record<string, [string, string]> = {
  connected: ["Working", "ok"], ready: ["Ready", "ok"], installed: ["Installed", "ok"], failed: ["Check failed", "danger"],
  not_checked: ["Not checked", ""], off: ["Off", ""], not_connected: ["Not connected", ""], not_found: ["Not found", ""],
  needs_setup: ["Needs setup", "warn"], not_installed: ["Not installed", ""],
};
const BILLING: Record<string, string> = { free: "Free", paid: "Paid", local: "Local", subscription: "Subscription", unknown: "Billing unknown", none: "" };
const DEFAULT_CAPS = ["chat", "tools", "tool_roundtrip", "streaming", "json"];

function CapRow({ caps }: { caps: Record<string, Cap> }) {
  return (
    <ul className="cap-row" aria-label="Capability tests">
      {Object.entries(caps).map(([k, c]) => (
        <li key={k} className={`cap ${c.state}`} title={c.state === "not_tested" ? `${c.label}: not tested` : `${c.label}: ${c.detail} (${c.at})`}>
          {c.state === "passed" ? <Check size={11} aria-hidden="true" /> : c.state === "failed" ? <X size={11} aria-hidden="true" /> : <Minus size={11} aria-hidden="true" />}
          <span>{c.label}</span><span className="sr-only">{c.state === "passed" ? " passed" : c.state === "failed" ? " failed" : " not tested"}</span>
        </li>
      ))}
    </ul>
  );
}

function Row({ e, reload }: { e: Entry; reload: () => void }) {
  const [busy, setBusy] = useState(""), [err, setErr] = useState("");
  const st = STATUS[e.status] ?? [e.status, ""];
  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what); setErr("");
    try { await fn(); reload(); } catch (x) { setErr((x as Error).message); } finally { setBusy(""); }
  };
  const test = (caps: string[]) => run("test", async () => {
    let allow = false;
    if (e.billing === "paid" || e.billing === "unknown") {
      if (!confirm(`Testing ${e.model} sends ${caps.length + 1} or so small requests, which ${e.name} may charge for (output is capped at a few tokens). Run the tests?`)) return;
      allow = true;
    }
    await api("/connection-directory/test", { method: "POST", body: { ref: e.model, caps, allow_paid: allow } });
  });
  return (
    <li className="conn-row">
      <div className="row wrap" style={{ alignItems: "baseline" }}>
        <strong className="grow">{e.name}{e.experimental && <span className="badge warn" style={{ marginLeft: 6 }}>Experimental</span>}</strong>
        {BILLING[e.billing] && <span className="badge">{BILLING[e.billing]}</span>}
        <span className={`badge ${st[1]}`}>{st[0]}</span>
      </div>
      <div className="small muted">{e.status_detail}</div>
      {e.billing_detail && <div className="small faint">{e.billing_detail}</div>}
      {e.limits && <div className="small">{e.limits}</div>}
      {e.setup_text && <div className="small">{e.setup_text}</div>}
      {e.mode_block && <div className="small" style={{ color: "var(--warn)" }}>{e.mode_block}</div>}
      {e.model && e.capabilities && <><div className="small faint">Tests for <span className="mono">{e.model.replace(/^\d+\//, "")}</span>:</div><CapRow caps={e.capabilities} /></>}
      {e.engine && <div className="small">Engine: {e.engine_detail}</div>}
      <div className="row wrap" style={{ gap: 6 }}>
        {e.model && !e.mode_block && <button type="button" className="btn sm" disabled={!!busy} onClick={() => test(DEFAULT_CAPS)}>
          {busy === "test" && <Loader2 size={12} className="spin" />} Test chat, tools, streaming and JSON</button>}
        {e.model && !e.mode_block && <button type="button" className="btn ghost sm" disabled={!!busy} onClick={() => test(["vision"])}>Test images</button>}
        {e.model && !e.mode_block && <button type="button" className="btn ghost sm" disabled={!!busy} onClick={() => test(["embeddings"])}>Test embeddings</button>}
        {e.kind === "anthropic" && e.provider_id && <button type="button" className="btn ghost sm" disabled={!!busy}
          onClick={() => run("engine", () => api(`/providers/${e.provider_id}/engine`, { method: "PUT", body: { engine: e.engine === "any-llm" ? "" : "any-llm" } }))}>
          {e.engine === "any-llm" ? "Use the compatibility endpoint" : "Use the native API (any-llm)"}</button>}
        {e.category === "B" && <button type="button" className="btn ghost sm" disabled={!!busy} onClick={() => run("detect", () => api("/coding/agents?fresh=true"))}>
          {busy === "detect" && <Loader2 size={12} className="spin" />} Check the paired computer</button>}
        {e.category === "B" && <Link className="btn ghost sm" to="/computer">Computer</Link>}
      </div>
      {err && <div className="small" role="alert" style={{ color: "var(--danger)" }}>{err}</div>}
    </li>
  );
}

/** Every way ROOKS can reach a model, grouped by what it is, with billing and what has really been tested. */
export default function ConnectionDirectory() {
  const [d, setD] = useState<Dir | null>(null);
  const [error, setError] = useState("");
  const load = () => api<Dir>("/connection-directory").then((x) => { setD(x); setError(""); }).catch((e) => setError((e as Error).message));
  useEffect(() => { load(); }, []);
  if (error) return <div className="alert err" role="alert">{error}</div>;
  if (!d) return <div className="skeleton" style={{ height: 120 }} />;
  return (
    <div className="card col" id="connection-directory">
      <h3>All model connections</h3>
      <p className="muted small" style={{ margin: 0 }}>{d.note}</p>
      {(["A", "B", "C", "E", "D"] as const).map((cat) => {
        const list = d.entries.filter((e) => e.category === cat);
        if (!list.length) return null;
        return (
          <div key={cat} className="col" style={{ gap: 6 }}>
            <div className="section-label">{d.groups[cat]}</div>
            <ul className="conn-list">{list.map((e) => <Row key={e.id} e={e} reload={load} />)}</ul>
          </div>
        );
      })}
    </div>
  );
}
