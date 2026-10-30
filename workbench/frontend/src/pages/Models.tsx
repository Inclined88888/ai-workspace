import { ArrowDown, ArrowUp, Cloud, Cpu, KeyRound, Loader2, Plus, RotateCcw, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { quotaText, resetTime, RouterQuota, useToast } from "../util";

/** The smart router, laid out: who answers first, which model each provider uses for quick and for hard jobs,
 * and how much free quota is left. */

type Row = {
  id: number; name: string; label: string; auth_kind: string; free: boolean; in_router: boolean; disabled: boolean; local: boolean;
  fast: string | null; strong: string | null; models: string[]; quota: RouterQuota; state: string;
};
type Status = { providers: Row[]; settings: { order: number[]; disabled: number[]; models: Record<string, { fast?: string; strong?: string }>; local_fallback: boolean };
  tiers: { fast: string | null; strong: string | null } };

function kindLabel(r: Row) {
  if (r.auth_kind === "oauth_subscription") return "Your plan";
  if (r.local) return "On this computer";
  if (r.free) return "Free tier";
  return r.auth_kind === "openai_compatible" ? "Server" : "API key";
}

function Meter({ q }: { q: RouterQuota }) {
  if (q.limit == null || q.remaining == null || q.window !== "day") return null;
  const pct = Math.max(0, Math.min(100, (q.remaining / q.limit) * 100));
  return <div className="meter" aria-label={`${Math.round(pct)}% left`}><div style={{ width: `${pct}%` }} /></div>;
}

export default function Models() {
  const [s, setS] = useState<Status | null>(null);
  const [def, setDef] = useState("");
  const toast = useToast();
  const load = () => api<Status>("/router").then(setS).catch(toast.err);
  useEffect(() => {
    load();
    api<{ default: string }>("/models").then((m) => setDef(m.default)).catch(() => undefined);
  }, []);

  const save = async (body: Partial<Status["settings"]>) => {
    try { setS(await api<Status>("/router", { method: "PUT", body })); } catch (e) { toast.err(e); }
  };
  const routed = s?.providers.filter((p) => !p.local || s.settings.local_fallback) ?? [];
  const move = (i: number, d: number) => {
    const ids = routed.map((p) => p.id);
    const j = i + d;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    save({ order: ids });
  };
  const toggle = (r: Row) => save({ disabled: r.disabled ? s!.settings.disabled.filter((x) => x !== r.id) : [...s!.settings.disabled, r.id] });
  const pickModel = (r: Row, tier: "fast" | "strong", model: string) => {
    const m = { ...s!.settings.models, [String(r.id)]: { ...(s!.settings.models[String(r.id)] ?? {}), [tier]: model } };
    if (!model) delete m[String(r.id)][tier];
    save({ models: m });
  };
  const useAuto = async () => {
    await api("/settings", { method: "PUT", body: { default_model: "auto" } });
    setDef("auto");
    toast.ok("New chats use the smart router");
  };

  return (
    <div className="page models-page" style={{ maxWidth: 980 }}>
      <div className="page-head col" style={{ alignItems: "flex-start", gap: 6 }}>
        <div className="kicker">The smart router</div>
        <h1>Models</h1>
        <p className="lede">Quick jobs go to a fast model, real work to a strong one. When a provider says “slow down” or its free day runs out, the next one picks up.</p>
      </div>
      {def && !def.startsWith("auto") && (
        <div className="alert row">Chats use a fixed model right now. <div className="spacer" /><button className="btn sm primary" onClick={useAuto}>Use the smart router</button></div>
      )}
      {!s && <Loader2 className="spin" size={18} />}
      {s && (
        <>
          <div className="tier-strip">
            <div><span className="kicker">Quick jobs</span><strong>{s.tiers.fast ?? "Nothing connected"}</strong><span className="muted small">summaries, titles, memory</span></div>
            <div><span className="kicker">Real work</span><strong>{s.tiers.strong ?? "Nothing connected"}</strong><span className="muted small">chat with tools, research, browser, Rooks Code</span></div>
          </div>
          <ol className="route-list">
            {routed.map((r, i) => (
              <li key={r.id} className={`route-item ${r.disabled ? "off" : ""} ${r.quota.cooling_until ? "cooling" : ""}`}>
                <div className="route-n">{String(i + 1).padStart(2, "0")}</div>
                <div className="route-main">
                  <div className="row" style={{ gap: 10 }}>
                    {r.auth_kind === "oauth_subscription" ? <Sparkles size={15} /> : r.local ? <Cpu size={15} /> : r.free ? <Cloud size={15} /> : <KeyRound size={15} />}
                    <strong className="route-name">{r.name}</strong>
                    <span className={`tag-kind ${r.free ? "free" : ""}`}>{kindLabel(r)}</span>
                    {r.quota.cooling_until && <span className="badge warn">Resting until {resetTime(r.quota.cooling_until)} · {r.quota.cooling_reason}</span>}
                    {r.state === "needs_auth" && <span className="badge danger">Sign in again</span>}
                  </div>
                  <div className="route-models">
                    {(["fast", "strong"] as const).map((tier) => (
                      <label key={tier} className="route-model">
                        <span className="kicker">{tier === "fast" ? "Fast" : "Strong"}</span>
                        <select className="input sm" value={s.settings.models[String(r.id)]?.[tier] ?? ""} onChange={(e) => pickModel(r, tier, e.target.value)} disabled={r.disabled}>
                          <option value="">{(r[tier] ?? "none").split("/").pop()} (auto)</option>
                          {r.models.map((m) => <option key={m} value={m}>{m}</option>)}
                        </select>
                      </label>
                    ))}
                  </div>
                  <div className="route-quota">
                    <span>{quotaText(r.quota, r.auth_kind === "oauth_subscription")}</span>
                    <Meter q={r.quota} />
                    {r.free && <span className="faint">resets {resetTime(r.quota.resets_at)}</span>}
                    {r.quota.note && <span className="faint">{r.quota.note}</span>}
                  </div>
                </div>
                <div className="route-actions">
                  <button className="btn ghost sm icon" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={14} /></button>
                  <button className="btn ghost sm icon" aria-label="Move down" disabled={i === routed.length - 1} onClick={() => move(i, 1)}><ArrowDown size={14} /></button>
                  {r.quota.cooling_until && (
                    <button className="btn ghost sm icon" aria-label="Try again now" title="Try again now" onClick={async () => setS(await api<Status>(`/router/reset/${r.id}`, { method: "POST" }))}><RotateCcw size={14} /></button>
                  )}
                  <label className="switch-label small"><input type="checkbox" checked={!r.disabled} onChange={() => toggle(r)} /> Use</label>
                </div>
              </li>
            ))}
          </ol>
          {routed.length === 0 && <div className="muted">Nothing connected yet.</div>}
          <div className="row wrap" style={{ gap: 14 }}>
            <Link className="btn" to="/settings"><Plus size={14} /> Add a provider</Link>
            {s.providers.some((p) => p.local) && (
              <label className="switch-label small"><input type="checkbox" checked={s.settings.local_fallback} onChange={(e) => save({ local_fallback: e.target.checked })} />
                Fall back to local Ollama when every cloud provider is out</label>
            )}
          </div>
          <p className="faint small">Rooks Code and Rooks Browser use the same router: <span className="mono">-P workbench -m auto</span>.</p>
        </>
      )}
    </div>
  );
}
