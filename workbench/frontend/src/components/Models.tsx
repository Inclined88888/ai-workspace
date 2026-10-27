import { AlertTriangle, Check, ChevronDown, Cpu, Globe, KeyRound, Loader2, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, Model } from "../api";
import { useToast } from "../util";
import { PasswordInput } from "./ui";

export type ProviderStatus = {
  id: number; name: string; kind: string; base_url: string; enabled: number; has_key: boolean; key_hint: string;
  checked: boolean; ok: boolean; error: string; recommended: string; free_tier: boolean | null; models_count: number; free_models: number; checked_at: string;
};
export type ModelsStatus = {
  default: { ref: string; source: string; error: string };
  fallbacks: string[]; fallbacks_custom: boolean; saved_default: string; saved_default_broken: boolean; providers: ProviderStatus[];
};
type Report = { ok: boolean; provider: string; recommended: string; free_tier: boolean | null; models: number; free_models: number;
  error: string; tried: { model: string; error: string }[]; provider_id: number; status: ModelsStatus };

// Where to get a key. Root domains only, so the links don't break when a provider moves its settings pages.
const CHOICES = [
  { preset: "openrouter", title: "OpenRouter", tag: "Free models", blurb: "One key for hundreds of models. A free account gets models marked (free).", site: "https://openrouter.ai", icon: Globe },
  { preset: "gemini", title: "Google Gemini", tag: "Free tier", blurb: "Free key from Google AI Studio. Google may use free tier prompts to improve its products.", site: "https://aistudio.google.com", icon: Sparkles },
  { preset: "anthropic", title: "Anthropic (Claude)", tag: "Paid", blurb: "Claude models with your Anthropic API key.", site: "https://console.anthropic.com", icon: KeyRound },
  { preset: "openai", title: "OpenAI", tag: "Paid", blurb: "GPT models with your OpenAI API key.", site: "https://platform.openai.com", icon: KeyRound },
  { preset: "opencode-go", title: "OpenCode Go", tag: "$10/month", blurb: "OpenCode's subscription. Its free models only work inside the OpenCode app.", site: "https://opencode.ai", icon: KeyRound },
] as const;

export function shortRef(ref: string, models: Model[]): string {
  const m = models.find((x) => x.ref === ref);
  return m ? `${m.model} · ${m.provider}` : ref.replace(/^\d+\//, "");
}

function ReportView({ r }: { r: Report }) {
  if (r.ok) {
    return (
      <div className="alert ok" role="status">
        <strong>Connected.</strong> ROOKS answered with <span className="mono">{r.recommended}</span>
        {r.recommended.endsWith(":free") ? " (free)" : ""}, tools included. {r.models} models available{r.free_models ? `, ${r.free_models} free` : ""}.
        {r.free_tier ? " This key has no paid credits, so free models are preferred." : ""}
      </div>
    );
  }
  return (
    <div className="alert err" role="alert">
      <strong>Not working yet.</strong> {r.error}
      {r.tried.length > 1 && (
        <details style={{ marginTop: 6 }}><summary>Models tried ({r.tried.length})</summary>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{r.tried.map((t) => <li key={t.model}><span className="mono">{t.model}</span>: {t.error}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** Paste a key, press Connect: ROOKS checks the key, finds a model that answers with tools, and uses it. */
export function ConnectProvider({ onConnected, localAvailable }: { onConnected: (s: ModelsStatus, r: Report) => void; localAvailable?: boolean }) {
  const [pick, setPick] = useState<string>("openrouter");
  const [key, setKey] = useState("");
  const [allowPaidCheck, setAllowPaidCheck] = useState(false);
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
      const body = pick === "custom" ? { ...custom, api_key: key } : pick === "ollama" ? { preset: "ollama" } : { preset: pick, api_key: key };
      const r = await api<Report>("/providers/connect", { body: { ...body, allow_paid_check: allowPaidCheck } });
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
        {CHOICES.map((c) => (
          <button type="button" key={c.preset} role="radio" aria-checked={pick === c.preset} className="choice compact" onClick={() => { setPick(c.preset); setReport(null); }}>
            <c.icon size={18} className="prio-1" aria-hidden="true" />
            <span className="grow"><strong>{c.title}</strong> <span className={`badge ${c.tag.startsWith("Free") ? "ok" : ""}`}>{c.tag}</span></span>
          </button>
        ))}
        {localAvailable && (
          <button type="button" role="radio" aria-checked={pick === "ollama"} className="choice compact" onClick={() => { setPick("ollama"); setReport(null); }}>
            <Cpu size={18} className="prio-1" aria-hidden="true" /><span className="grow"><strong>Local (Ollama)</strong> <span className="badge ok">Private</span></span>
          </button>
        )}
        <button type="button" role="radio" aria-checked={pick === "custom"} className="choice compact" onClick={() => { setPick("custom"); setReport(null); }}>
          <KeyRound size={18} className="prio-1" aria-hidden="true" /><span className="grow"><strong>Other endpoint</strong> <span className="badge">Advanced</span></span>
        </button>
      </div>

      {choice && <p className="muted small" style={{ margin: 0 }}>{choice.blurb} Get a key at <a href={choice.site} target="_blank" rel="noreferrer">{choice.site.replace("https://", "")}</a>.</p>}
      {pick === "ollama" && <p className="muted small" style={{ margin: 0 }}>Uses models downloaded to this computer's Ollama. Free and private; slower than cloud models and small models can struggle with tools.</p>}
      {pick === "custom" && (
        <div className="grid two">
          <label className="field">Name<input className="input" value={custom.name} onChange={(e) => setCustom({ ...custom, name: e.target.value })} placeholder="Groq, LM Studio…" /></label>
          <label className="field">Type<select className="input" value={custom.kind} onChange={(e) => setCustom({ ...custom, kind: e.target.value })}><option value="openai">OpenAI compatible</option><option value="anthropic">Anthropic</option><option value="ollama">Ollama</option></select></label>
          <label className="field">Base URL<input className="input" required value={custom.base_url} onChange={(e) => setCustom({ ...custom, base_url: e.target.value })} placeholder="https://api.groq.com/openai/v1" /></label>
          <label className="field">Model names <span className="hint">optional, if the endpoint can't list them</span><input className="input" value={custom.models} onChange={(e) => setCustom({ ...custom, models: e.target.value })} placeholder="model-a, model-b" /></label>
        </div>
      )}
      {pick !== "ollama" && (
        <div className="col" style={{ gap: 5 }}>
          <label htmlFor="provider-key" className="field">{pick === "custom" ? "API key (if needed)" : `${choice?.title ?? ""} API key`}</label>
          <PasswordInput id="provider-key" value={key} onChange={setKey} autoComplete="off" />
          <span className="field-msg muted">Stored encrypted on this server. Pasting a new key for the same provider replaces the old one.</span>
        </div>
      )}
      {pick !== "ollama" && (
        <label className="row small" style={{ gap: 8 }}>
          <input type="checkbox" checked={allowPaidCheck} onChange={(e) => setAllowPaidCheck(e.target.checked)} />
          I authorize a bounded paid model verification call, which may incur API charges.
          Leave unchecked to test only known free models.
        </label>
      )}
      <div className="row wrap">
        <button className="btn primary" disabled={busy || (pick !== "ollama" && pick !== "custom" && !key.trim()) || (pick === "custom" && !custom.base_url.trim())}>
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

  const check = async (p: ProviderStatus) => {
    const allowPaid = p.free_models === 0;
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
          <p style={{ margin: 0 }}>ROOKS is using <strong>{shortRef(st.default.ref, models)}</strong>
            <span className="muted"> ({st.default.source === "chosen" ? "your choice" : "picked automatically"})</span>
            {st.fallbacks.length > 0 && <span className="muted">, with {st.fallbacks.length} backup{st.fallbacks.length > 1 ? "s" : ""} if it fails</span>}.</p>
        ) : (
          <div className="alert" role="status"><AlertTriangle size={14} /> {st.default.error}</div>
        )}
        {lastOk && <ReportView r={lastOk} />}
        {st.saved_default_broken && <div className="alert">Your saved default model belongs to a provider that was removed or turned off, so ROOKS picked a working one instead.</div>}
      </div>

      {(working.length === 0 || adding) ? (
        <div className="card col">
          <h3>{none ? "Connect your first AI model" : working.length === 0 ? "Paste a working key, or try another provider" : "Connect another provider"}</h3>
          <ConnectProvider onConnected={(s, r) => { refreshAll(s); if (r.ok) { setLastOk(r); setAdding(false); } }} localAvailable={localAvailable} />
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
                  {p.free_tier && <span className="badge">Free tier</span>}
                </div>
                <div className="muted small">
                  {p.ok ? <>Best model: <span className="mono">{p.recommended}</span> · {p.models_count} models{p.free_models ? `, ${p.free_models} free` : ""}</> : p.error || "Press Check to test it."}
                  {p.key_hint && <> · key {p.key_hint}</>}
                </div>
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
