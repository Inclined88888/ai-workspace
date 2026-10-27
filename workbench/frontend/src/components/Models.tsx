import { AlertTriangle, Check, ChevronDown, Cpu, Globe, KeyRound, Loader2, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, Model } from "../api";
import { useToast } from "../util";
import { PasswordInput } from "./ui";

export type ProviderStatus = {
  id: number; name: string; kind: string; base_url: string; enabled: number; has_key: boolean; key_hint: string;
  checked: boolean; ok: boolean; error: string; recommended: string; free_tier: boolean | null; models_count: number; free_models: number; checked_at: string;
  free_tool_models?: number | null; local?: boolean; can_confirm_free?: boolean; free_confirmed?: boolean;
};
export type ModelsStatus = {
  default: { ref: string; source: string; error: string; free_basis?: string; local?: boolean; price_in?: number | null; price_out?: number | null };
  fallbacks: string[]; fallbacks_custom: boolean; saved_default: string; saved_default_broken: boolean; saved_default_blocked?: string;
  mode?: string; providers: ProviderStatus[];
};
export type Report = { ok: boolean; provider: string; recommended: string; free_tier: boolean | null; models: number; free_models: number;
  free_tool_models?: number; mode?: string; error: string; tried: { model: string; error: string }[]; provider_id: number; status: ModelsStatus };

export const FREE_BASIS: Record<string, string> = {
  price: "free: OpenRouter lists its price as $0",
  confirmed: "free: you confirmed this provider cannot bill you",
  local: "free: runs on your own computer",
};

// Where to get a key. Root domains only, so the links don't break when a provider moves its settings pages.
const CHOICES = [
  { preset: "openrouter", title: "OpenRouter", tag: "Free models", blurb: "One key for hundreds of models. Only models OpenRouter lists at $0 count as free, with daily limits.", site: "https://openrouter.ai", icon: Globe },
  { preset: "gemini", title: "Google Gemini", tag: "Free tier", blurb: "Key from Google AI Studio. Free only while its Google project has no billing account; Google uses free tier prompts to improve its products.", site: "https://aistudio.google.com", icon: Sparkles },
  { preset: "anthropic", title: "Anthropic (Claude)", tag: "Paid", blurb: "Claude models with your Anthropic API key.", site: "https://console.anthropic.com", icon: KeyRound },
  { preset: "openai", title: "OpenAI", tag: "Paid", blurb: "GPT models with your OpenAI API key.", site: "https://platform.openai.com", icon: KeyRound },
  { preset: "opencode-go", title: "OpenCode Go", tag: "$10/month", blurb: "OpenCode's subscription. Its free models only work inside the OpenCode app.", site: "https://opencode.ai", icon: KeyRound },
] as const;

export function shortRef(ref: string, models: Model[]): string {
  const m = models.find((x) => x.ref === ref);
  return m ? `${m.model} · ${m.provider}` : ref.replace(/^\d+\//, "");
}

// What to try for the errors people actually hit, matched on the provider's explained message
const FIXES: [RegExp, string][] = [
  [/rejected the API key|401|403/i, "Copy the key again from the provider's site, with no spaces before or after, and paste it here. A key that was deleted or rotated stops working at once."],
  [/limiting requests|429/i, "The provider is rate limiting. Free OpenRouter models allow 20 requests a minute and 50 a day (1,000 a day once you have bought $10 of credits). Wait, or try again tomorrow."],
  [/no credits|402/i, "This model needs credits. In Free Cloud AI mode ROOKS only uses models verified as free; pick one of those."],
  [/can't use tools|tool use/i, "The assistant needs a model that accepts tools. ROOKS tries several; if none work, choose another provider."],
  [/Can't reach|did not answer|unreachable/i, "Check this server's internet connection. For a local server, check it is running and the address is right."],
  [/Confirm it below|cannot tell from/i, "Google does not report whether a key can be billed. Open AI Studio: if the key's project shows Free (no billing account), confirm it here."],
  [/no model verified as free|not verified as free/i, "This provider has no model ROOKS can verify as free. Use OpenRouter or Gemini for free cloud AI, or change the AI mode."],
];

export function Troubleshoot({ error }: { error: string }) {
  const hit = FIXES.filter(([re]) => re.test(error)).map(([, fix]) => fix);
  if (!hit.length) return null;
  return <ul className="small" style={{ margin: "6px 0 0", paddingLeft: 18 }}>{hit.map((f) => <li key={f}>{f}</li>)}</ul>;
}

function ReportView({ r }: { r: Report }) {
  if (r.ok) {
    return (
      <div className="alert ok" role="status">
        <strong>Connected and tested.</strong> A real request to <span className="mono">{r.recommended}</span> was answered, with tools attached
        as the assistant sends them. {r.models} models available{r.free_models ? `, ${r.free_models} verified free` : ""}
        {typeof r.free_tool_models === "number" && r.free_models ? ` (${r.free_tool_models} of them list tool support)` : ""}.
        {r.free_tier ? " This key has never bought credits." : ""}
      </div>
    );
  }
  return (
    <div className="alert err" role="alert">
      <strong>Not working yet.</strong> {r.error}
      <Troubleshoot error={`${r.error} ${r.tried.map((t) => t.error).join(" ")}`} />
      {r.tried.length > 1 && (
        <details style={{ marginTop: 6 }}><summary>Models tried ({r.tried.length})</summary>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{r.tried.map((t) => <li key={t.model}><span className="mono">{t.model}</span>: {t.error}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** Paste a key, press Connect: ROOKS checks the key, finds a model that answers with tools, and uses it. */
export function ConnectProvider({ onConnected, localAvailable, presets, mode = "", start }: {
  onConnected: (s: ModelsStatus, r: Report) => void; localAvailable?: boolean;
  presets?: string[]; mode?: string; start?: string;
}) {
  const shown = presets ? CHOICES.filter((c) => presets.includes(c.preset)) : CHOICES;
  const offer = (k: string) => !presets || presets.includes(k);
  const [pick, setPick] = useState<string>(start ?? shown[0]?.preset ?? "custom");
  const [key, setKey] = useState("");
  const [allowPaidCheck, setAllowPaidCheck] = useState(false);
  const [confirmFree, setConfirmFree] = useState(false);
  const free = mode === "free";
  const paidOk = mode !== "free" && mode !== "local";  // these two modes never send a request that could be charged
  const localUrl = /^https?:\/\/(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|host\.docker\.internal|[a-z0-9-]+(:|\/|$))/i;
  const [custom, setCustom] = useState({ name: "", base_url: "", kind: "openai", models: "" });
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const choice = CHOICES.find((c) => c.preset === pick);

  const connect = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setReport(null);
    try {
      const body = pick === "custom" ? { ...custom, api_key: key } : pick === "ollama" || pick === "llamacpp" ? { preset: pick } : { preset: pick, api_key: key };
      const r = await api<Report>("/providers/connect", { body: { ...body, allow_paid_check: paidOk && allowPaidCheck, confirm_free: confirmFree } });
      setReport(r);
      if (r.ok) setKey("");
      onConnected(r.status, r);
    } catch (err) {
      setReport({ ok: false, error: (err as Error).message, tried: [] } as unknown as Report);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="col gap-lg" onSubmit={connect}>
      <div className="provider-grid" role="radiogroup" aria-label="Provider">
        {shown.map((c) => (
          <button type="button" key={c.preset} role="radio" aria-checked={pick === c.preset} className="choice compact" onClick={() => { setPick(c.preset); setReport(null); }}>
            <c.icon size={18} className="prio-1" aria-hidden="true" />
            <span className="grow"><strong>{c.title}</strong> <span className={`badge ${c.tag.startsWith("Free") ? "ok" : ""}`}>{c.tag}</span></span>
          </button>
        ))}
        {localAvailable && offer("ollama") && (
          <button type="button" role="radio" aria-checked={pick === "ollama"} className="choice compact" onClick={() => { setPick("ollama"); setReport(null); }}>
            <Cpu size={18} className="prio-1" aria-hidden="true" /><span className="grow"><strong>Local (Ollama)</strong> <span className="badge ok">Private</span></span>
          </button>
        )}
        {offer("llamacpp") && (
          <button type="button" role="radio" aria-checked={pick === "llamacpp"} className="choice compact" onClick={() => { setPick("llamacpp"); setReport(null); }}>
            <Cpu size={18} className="prio-1" aria-hidden="true" /><span className="grow"><strong>llama.cpp</strong> <span className="badge ok">Private</span></span>
          </button>
        )}
        {offer("custom") && (
          <button type="button" role="radio" aria-checked={pick === "custom"} className="choice compact" onClick={() => { setPick("custom"); setReport(null); }}>
            <KeyRound size={18} className="prio-1" aria-hidden="true" /><span className="grow"><strong>Other endpoint</strong> <span className="badge">Advanced</span></span>
          </button>
        )}
      </div>

      {choice && <p className="muted small" style={{ margin: 0 }}>{choice.blurb} Get a key at <a href={choice.site} target="_blank" rel="noreferrer">{choice.site.replace("https://", "")}</a>.</p>}
      {pick === "ollama" && <p className="muted small" style={{ margin: 0 }}>Uses models downloaded to this computer's Ollama. Free and private; slower than cloud models and small models can struggle with tools.</p>}
      {pick === "llamacpp" && <p className="muted small" style={{ margin: 0 }}>Connects to a llama.cpp <span className="mono">llama-server</span> you already run on port 8080 (set LLAMACPP_URL for another address). Start it with <span className="mono">--jinja</span> so tool calls work. ROOKS does not install or start it.</p>}
      {pick === "custom" && (
        <div className="grid two">
          <label className="field">Name<input className="input" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} placeholder="Groq, LM Studio…" /></label>
          <label className="field">Type<select className="input" value={custom.kind} onChange={(e) => setCustom({ ...custom, kind: e.target.value })}><option value="openai">OpenAI compatible</option><option value="anthropic">Anthropic</option><option value="ollama">Ollama</option></select></label>
          <label className="field">Base URL<input className="input" required value={custom.base_url} onChange={(e) => setCustom({ ...custom, base_url: e.target.value })} placeholder="https://api.groq.com/openai/v1" /></label>
          <label className="field">Model names <span className="hint">optional, if the endpoint can't list them</span><input className="input" value={custom.models} onChange={(e) => setCustom({ ...custom, models: e.target.value })} placeholder="model-a, model-b" /></label>
        </div>
      )}
      {pick !== "ollama" && pick !== "llamacpp" && (
        <div className="col" style={{ gap: 5 }}>
          <label htmlFor="provider-key" className="field">{pick === "custom" ? "API key (if needed)" : `${choice?.title ?? ""} API key`}</label>
          <PasswordInput id="provider-key" value={key} onChange={setKey} autoComplete="off" />
          <span className="field-msg muted">Stored encrypted on this server. Pasting a new key for the same provider replaces the old one.</span>
        </div>
      )}
      {pick === "gemini" && (
        <label className="row small" style={{ gap: 8, alignItems: "flex-start" }}>
          <input type="checkbox" checked={confirmFree} onChange={(e) => setConfirmFree(e.target.checked)} />
          <span>This key comes from a Google project with <strong>no billing account</strong> (AI Studio shows its plan as Free), so Google cannot charge for it.
            Google gives no way for ROOKS to check this.{free ? " Free Cloud AI mode needs this to use Gemini." : ""}</span>
        </label>
      )}
      {(pick === "llamacpp" || (pick === "custom" && localUrl.test(custom.base_url))) && free && (
        <label className="row small" style={{ gap: 8, alignItems: "flex-start" }}>
          <input type="checkbox" checked={confirmFree} onChange={(e) => setConfirmFree(e.target.checked)} />
          <span>This server runs models itself, with no paid API behind it, so it cannot cost money.</span>
        </label>
      )}
      {paidOk && pick !== "ollama" && pick !== "llamacpp" && (
        <label className="row small" style={{ gap: 8 }}>
          <input type="checkbox" checked={allowPaidCheck} onChange={(e) => setAllowPaidCheck(e.target.checked)} />
          I authorize a bounded paid model verification call, which may incur API charges.
          Leave unchecked to test only known free models.
        </label>
      )}
      {!paidOk && pick !== "ollama" && pick !== "llamacpp" && <p className="small muted" style={{ margin: 0 }}>The connection test only sends a request to a model verified as free. Nothing that could cost money is sent.</p>}
      <div className="row wrap">
        <button className="btn primary" disabled={busy || (pick !== "ollama" && pick !== "llamacpp" && pick !== "custom" && !key.trim()) || (pick === "custom" && !custom.base_url.trim()) || (free && pick === "gemini" && !confirmFree)}>
          {busy ? <><Loader2 size={15} className="spin" /> Checking the key and finding a model that works…</> : "Connect"}
        </button>
      </div>
      {report && <ReportView r={report} />}
    </form>
  );
}

/** Full model settings: what is in use, providers with health, default and backups, connect another. */
export function ModelsPanel({ models, reloadModels, localAvailable }: { models: Model[]; reloadModels: () => void; localAvailable: boolean }) {
  const [st, setSt] = useState<ModelsStatus | null>(null);
  const [checking, setChecking] = useState(0);
  const [adding, setAdding] = useState(false);
  const [defaultPick, setDefaultPick] = useState("");
  const [lastOk, setLastOk] = useState<Report | null>(null);
  const [backups, setBackups] = useState<string[]>([]);
  const toast = useToast();

  const load = () => api<ModelsStatus>("/models/status").then((s) => { setSt(s); setDefaultPick(s.saved_default_broken ? "" : s.saved_default); setBackups(s.fallbacks); }).catch(toast.err);
  useEffect(() => { load(); }, []);
  const refreshAll = (s: ModelsStatus) => { setSt(s); setDefaultPick(s.saved_default_broken ? "" : s.saved_default); setBackups(s.fallbacks); reloadModels(); };

  const confirmFree = async (p: ProviderStatus, confirmed: boolean) => {
    try { const r = await api<Report>(`/providers/${p.id}/free-confirm`, { method: "PUT", body: { confirmed } }); refreshAll(r.status); if (confirmed) { r.ok ? toast.ok(`${p.name} works`) : toast.err(r.error); } }
    catch (e) { toast.err(e); }
  };
  const check = async (p: ProviderStatus) => {
    const allowPaid = p.free_models === 0 && st?.mode !== "free" && st?.mode !== "local";
    if (allowPaid && !window.confirm("This provider has no known free models. A small verification request may incur API charges. Proceed?")) return;
    setChecking(p.id);
    try { const r = await api<Report>(`/providers/${p.id}/check?allow_paid=${allowPaid}`, { method: "POST" }); refreshAll(r.status); r.ok ? toast.ok(`${p.name} works`) : toast.err(r.error); }
    catch (e) { toast.err(e); } finally { setChecking(0); }
  };
  const remove = async (p: ProviderStatus) => {
    if (!confirm(`Remove ${p.name}? Its key is deleted from this server.`)) return;
    try { await api(`/providers/${p.id}`, { method: "DELETE" }); load(); reloadModels(); toast.ok(`${p.name} removed`); } catch (e) { toast.err(e); }
  };
  const saveDefault = async () => {
    try { await api("/settings", { method: "PUT", body: { default_model: defaultPick } }); load(); toast.ok(defaultPick ? "Default model saved" : "ROOKS picks the best working model automatically"); }
    catch (e) { toast.err(e); }
  };
  const saveBackups = async (refs: string[] | null) => {
    try { refreshAll(await api<ModelsStatus>("/models/fallbacks", { method: "PUT", body: { refs } })); toast.ok(refs ? "Backups saved" : "Backups are automatic again"); }
    catch (e) { toast.err(e); }
  };

  if (!st) return <div className="card"><Loader2 size={16} className="spin" aria-label="Loading" /></div>;
  const none = st.providers.length === 0;
  const working = st.providers.filter((p) => p.ok);

  return (
    <div className="col gap-lg">
      <div className={`card col ${st.default.error ? "card-warn" : ""}`}>
        <div className="row"><Sparkles size={16} aria-hidden="true" /><h2 className="grow">AI models</h2></div>
        {st.default.ref ? (
          <>
            <p style={{ margin: 0 }}>ROOKS is using <strong>{shortRef(st.default.ref, models)}</strong>
              <span className="muted"> ({st.default.source === "chosen" ? "your choice" : "picked automatically"})</span>
              {st.fallbacks.length > 0 && <span className="muted">, with {st.fallbacks.length} backup{st.fallbacks.length > 1 ? "s" : ""} if it fails</span>}.</p>
            <div className="row wrap small" style={{ gap: 6 }}>
              {st.default.free_basis ? <span className="badge ok">{FREE_BASIS[st.default.free_basis]}</span>
                : typeof st.default.price_in === "number" ? <span className="badge">${st.default.price_in} in, ${st.default.price_out} out per million tokens (OpenRouter's list)</span>
                : <span className="badge">price not reported by the provider</span>}
              {st.default.local && <span className="badge">stays on your network</span>}
            </div>
          </>
        ) : (
          <div className="alert" role="status"><AlertTriangle size={14} /> {st.default.error}</div>
        )}
        {lastOk && <ReportView r={lastOk} />}
        {st.saved_default_broken && <div className="alert">Your saved default model belongs to a provider that was removed or turned off, so ROOKS picked a working one instead.</div>}
        {st.saved_default_blocked && <div className="alert">Your saved default, {shortRef(st.saved_default, models)}, is kept but not used in this AI mode: {st.saved_default_blocked}</div>}
      </div>

      {(working.length === 0 || adding) ? (
        <div className="card col">
          <h3>{none ? "Connect your first AI model" : working.length === 0 ? "Paste a working key, or try another provider" : "Connect another provider"}</h3>
          <ConnectProvider onConnected={(s, r) => { refreshAll(s); if (r.ok) { setLastOk(r); setAdding(false); } }} localAvailable={localAvailable} mode={st.mode}
            presets={st.mode === "free" ? ["openrouter", "gemini", "ollama", "llamacpp", "custom"] : st.mode === "local" ? ["ollama", "llamacpp", "custom"] : undefined} />
          {working.length > 0 && <div><button type="button" className="btn ghost sm" onClick={() => setAdding(false)}>Cancel</button></div>}
        </div>
      ) : null}

      {!none && (
        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 14px 0" }}><h3 className="grow">Connected providers</h3>
            {!adding && working.length > 0 && <button className="btn sm" onClick={() => setAdding(true)}>Connect another</button>}</div>
          {st.providers.map((p) => (
            <div key={p.id} className="list-item" style={{ cursor: "default", alignItems: "flex-start" }}>
              <div className="grow">
                <div className="row wrap" style={{ gap: 6 }}>
                  <strong>{p.name}</strong>
                  {!p.enabled ? <span className="badge">Off</span> : !p.checked ? <span className="badge">Not checked</span> : p.ok ? <span className="badge ok"><Check size={11} /> Working</span> : <span className="badge danger">Problem</span>}
                  {p.free_confirmed && <span className="badge ok">You confirmed: cannot bill</span>}
                </div>
                <div className="muted small">
                  {p.ok ? <>Best model: <span className="mono">{p.recommended}</span> · {p.models_count} models{p.free_models ? `, ${p.free_models} verified free` : ""}</> : p.error || "Press Check to test it."}
                  {p.key_hint && <> · key {p.key_hint}</>}
                </div>
                {!p.ok && p.error && <Troubleshoot error={p.error} />}
                {p.can_confirm_free && (
                  <label className="row small" style={{ gap: 8, marginTop: 6, alignItems: "flex-start" }}>
                    <input type="checkbox" checked={!!p.free_confirmed} onChange={(e) => confirmFree(p, e.target.checked)} />
                    <span>{p.local ? "This server runs models itself, with no paid API behind it." : "This key's Google project has no billing account (AI Studio shows Free)."} ROOKS cannot check this; it is your statement.</span>
                  </label>
                )}
              </div>
              <button className="btn sm" onClick={() => check(p)} disabled={checking === p.id} aria-label={`Check ${p.name}`}>
                {checking === p.id ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />} Check
              </button>
              <button className="btn ghost sm icon" onClick={() => remove(p)} aria-label={`Remove ${p.name}`} title="Remove"><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      )}

      {working.length > 0 && (
        <details className="card col">
          <summary className="row" style={{ cursor: "pointer" }}><ChevronDown size={15} aria-hidden="true" /><h3>Default model and backups</h3><span className="muted small">optional</span></summary>
          <div className="col gap-lg" style={{ marginTop: 12 }}>
            <label className="field">Default model <span className="hint">Automatic picks the best model that passed its check. A model you choose is used strictly: if it fails you'll see why, nothing switches silently.</span>
              <select className="input" value={defaultPick} onChange={(e) => setDefaultPick(e.target.value)}>
                <option value="">Automatic{st.default.source === "auto" && st.default.ref ? ` (now ${shortRef(st.default.ref, models)})` : ""}</option>
                {models.map((m) => <option key={m.ref} value={m.ref}>{m.model}{m.free ? " (free)" : ""} · {m.provider}</option>)}
              </select>
            </label>
            <div><button className="btn" onClick={saveDefault}>Save default</button></div>
            <div className="col" style={{ gap: 6 }}>
              <span className="field">Backups <span className="hint">Tried in order when the default fails (out of credits, rate limited, down). {st.fallbacks_custom ? "You set these." : "Automatic: the best model of each other working provider."}</span></span>
              {[0, 1, 2].map((i) => (
                <select key={i} className="input" aria-label={`Backup ${i + 1}`} value={backups[i] ?? ""} onChange={(e) => { const b = [...backups]; b[i] = e.target.value; setBackups(b); }}>
                  <option value="">{i === 0 ? "No backup" : "None"}</option>
                  {models.map((m) => <option key={m.ref} value={m.ref}>{m.model}{m.free ? " (free)" : ""} · {m.provider}</option>)}
                </select>
              ))}
              <div className="row wrap"><button className="btn" onClick={() => saveBackups(backups.filter(Boolean))}>Save backups</button>{st.fallbacks_custom && <button className="btn ghost" onClick={() => saveBackups(null)}>Use automatic backups</button>}</div>
            </div>
          </div>
        </details>
      )}
      {toast.node}
    </div>
  );
}
