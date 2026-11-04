import { ArrowLeft, ArrowRight, Check, Cloud, Cpu, Download, ExternalLink, KeyRound, Loader2, ListChecks, ShieldCheck, Sparkles } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, stream } from "../api";
import { BrandMark } from "../brand";
import PlanCard from "./PlanCard";
import { ChatGPTCard, KEY_ORDER, KeyField, KeyInfo, Preset, Sub, Validation } from "./ModelSignIn";
import { ThemeGrid, TypeChoices } from "../ThemePicker";
import { Tracer } from "../Tracer";
import { saveAppearanceRemote } from "../themeSync";
import { readAppearance } from "../appearance";

/** First run: "Choose how to think". Cloud is the default (nothing heavy runs on this computer); the smart router
 * then uses whatever was connected and falls back when a free tier runs out. */

type Model = { ref: string; model: string };

const STARTER_MODEL = "qwen3:4b";

async function modelsFor(pid: number): Promise<Model[]> {
  const { models } = await api<{ models: Model[] }>("/models?fresh=true");
  return models.filter((m) => m.ref.startsWith(`${pid}/`));
}

async function finish(pid: number, prefer?: string) {
  const models = await modelsFor(pid);
  if (!models.length) throw new Error("Connected, but no models were listed. Check the key, or try again in a moment.");
  const pick = models.find((m) => m.model === prefer || m.model === `${prefer}:latest`) ?? models[0];
  const { default: current } = await api<{ default: string }>("/models");
  if (!current.startsWith(`${pid}/`)) await api("/settings", { method: "PUT", body: { default_model: pick.ref } });
  await api("/desktop/welcome-done", { method: "POST" });
}

/** Cloud connections all go behind the smart router: "auto" picks a model per task and falls back on limits. */
async function finishCloud() {
  await api("/settings", { method: "PUT", body: { default_model: "auto" } });
  await api("/desktop/welcome-done", { method: "POST" });
}

function Back({ onBack }: { onBack: () => void }) {
  return <button type="button" className="btn ghost" onClick={onBack}><ArrowLeft size={16} /> Back</button>;
}

const FREE = [
  { id: "gemini", name: "Google Gemini", blurb: "Flash and Flash-Lite. A Google account is all you need.", steps: "Sign in, then Create API key." },
  { id: "groq", name: "Groq", blurb: "Very fast open models (gpt-oss-120b). No card.", steps: "Sign up, then Create API Key." },
];

function FreeStep({ info, onBack, onDone }: { info: Record<string, KeyInfo>; onBack: () => void; onDone: () => void }) {
  const [pick, setPick] = useState("gemini");
  const [key, setKey] = useState("");
  const [opened, setOpened] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState<string[]>([]);
  const p = FREE.find((f) => f.id === pick)!;
  const onDetected = useCallback((preset: string | null) => { if (preset === "gemini" || preset === "groq") setPick(preset); }, []);

  const connect = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<Validation>("/providers/validate", { body: { api_key: key.trim(), preset: pick, save: true } });
      if (!r.ok) throw new Error(`${r.name} didn't accept that key (${r.detail}). Copy it again from the key page.`);
      setConnected((c) => [...new Set([...c, pick])]);
      setKey("");
      setOpened(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const other = FREE.find((f) => f.id !== pick && !connected.includes(f.id));
  const done = connected.length > 0;

  return (
    <form className="col gap-lg" onSubmit={connect}>
      <div>
        <div className="kicker">Free cloud</div>
        <h1>A free key, two minutes</h1>
        <p className="muted">Both are free with daily limits. Add both and the app switches to the other one when a limit is hit.</p>
      </div>
      {error && <div className="alert err">{error}</div>}
      <div className="free-grid" role="radiogroup" aria-label="Free provider">
        {FREE.map((f) => (
          <button type="button" key={f.id} role="radio" aria-checked={pick === f.id} className={`free-card ${pick === f.id ? "on" : ""}`}
            onClick={() => { setPick(f.id); setKey(""); setOpened(false); }}>
            <span className="row" style={{ gap: 8 }}><strong>{f.name}</strong>{connected.includes(f.id) && <span className="badge ok"><Check size={11} /> Connected</span>}</span>
            <span className="small muted">{f.blurb}</span>
            <span className="tiny muted">{info[f.id]?.limits}</span>
          </button>
        ))}
      </div>
      {!connected.includes(pick) && (
        <ol className="free-steps">
          <li className={opened ? "done" : ""}>
            <span className="free-n">1</span>
            <span className="grow">Open the {p.name} key page. {p.steps}</span>
            <a className="btn sm" href={info[pick]?.url} target="_blank" rel="noreferrer" onClick={() => setOpened(true)}>Open <ExternalLink size={12} /></a>
          </li>
          <li>
            <span className="free-n">2</span>
            <span className="grow col" style={{ gap: 8 }}>
              <span>Copy the key, come back, and paste it here.</span>
              <KeyField value={key} onChange={setKey} expect={info[pick]?.prefix} onDetected={onDetected} />
            </span>
          </li>
        </ol>
      )}
      <div className="row muted small"><ShieldCheck size={14} /> Keys stay in this computer's keychain and only go to the provider they're for.</div>
      <div className="row">
        <Back onBack={onBack} />
        <div className="spacer" />
        {done && other && !key && <button type="button" className="btn ghost" onClick={() => setPick(other.id)}>Add {other.name} too</button>}
        {key.trim().length >= 8 ? (
          <button className="btn primary" disabled={busy}>{busy ? <><Loader2 size={16} className="spin" /> Checking key…</> : <>Check & connect <ArrowRight size={16} /></>}</button>
        ) : (
          <button type="button" className="btn primary" disabled={!done || busy} onClick={async () => { await finishCloud(); onDone(); }}>Start chatting <ArrowRight size={16} /></button>
        )}
      </div>
    </form>
  );
}

/** Right after the PIN: the company's own sign-in, not another password typed in here. */
export function AccountsStep({ onNext }: { onNext: () => void }) {
  const [subs, setSubs] = useState<Sub[] | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => { api<{ options: Sub[] }>("/auth/subscriptions").then((r) => setSubs(r.options)).catch(() => setSubs([])); }, []);
  const chatgpt = subs?.find((s) => s.id === "chatgpt");
  const claude = subs?.find((s) => s.id === "claude" && s.available && s.via === "claude_code");
  const openWindow = async (who: string) => {
    setNote("");
    try {
      await api("/mail/mac/settings", { method: "POST" });
      setNote(`A window from your Mac is open. Choose Add Account, pick ${who}, and sign in as usual. Your password stays with ${who}.`);
    } catch (err) {
      setNote((err as Error).message);
    }
  };
  const go = async () => {
    await api("/desktop/signins-done", { method: "POST" }).catch(() => undefined);
    onNext();
  };
  return (
    <div className="col gap-lg">
      <div className="accounts-head">
        <div>
          <h1>Sign in with the accounts you already use</h1>
          <p className="muted">Optional. Each one opens that company's own window. You can connect them later.</p>
        </div>
        <button type="button" className="btn" onClick={go}>Skip for now</button>
      </div>
      {note && <div className="alert">{note}</div>}
      <div className="signin-picks">
        <button type="button" className="btn" onClick={() => openWindow("Google")}><BrandMark name="google" /> Gmail</button>
        <button type="button" className="btn" onClick={() => openWindow("Microsoft")}><BrandMark name="microsoft" /> Microsoft</button>
        <button type="button" className="btn" onClick={() => openWindow("Apple")}><BrandMark name="apple" /> Apple</button>
      </div>
      {chatgpt && <ChatGPTCard sub={chatgpt} onConnected={go} cta="Continue" />}
      {claude && <PlanCard agent="claude" title="Claude" onConnected={go} cta="Continue"
        blurb="Opens Claude's own sign-in. Use the plan you already pay for." />}
      <div className="row">
        <div className="spacer" />
        <button type="button" className="btn" onClick={go}>Skip for now</button>
      </div>
    </div>
  );
}

function SubscriptionStep({ onBack, onDone, onKey }: { onBack: () => void; onDone: () => void; onKey: (preset: string) => void }) {
  const [subs, setSubs] = useState<Sub[] | null>(null);
  useEffect(() => {
    api<{ options: Sub[] }>("/auth/subscriptions").then((r) => setSubs(r.options)).catch(() => setSubs([]));
  }, []);
  const chatgpt = subs?.find((s) => s.id === "chatgpt");
  const claude = subs?.find((s) => s.id === "claude" && s.available && s.via === "claude_code");
  const copilot = subs?.find((s) => s.id === "copilot" && s.available);
  const start = async () => { await finishCloud(); onDone(); };

  return (
    <div className="col gap-lg">
      <div>
        <div className="kicker">Your subscription</div>
        <h1>Sign in with your plan</h1>
        <p className="muted">No API key needed. Use the plan you already pay for, where the provider allows it.</p>
      </div>
      {subs === null && <Loader2 size={16} className="spin" />}
      {claude && <PlanCard agent="claude" title="Claude" onConnected={start}
        blurb="Use the Claude plan you already pay for. Sign-in opens in Anthropic's own app." />}
      {chatgpt && <ChatGPTCard sub={chatgpt} onConnected={start} />}
      {copilot && <PlanCard agent="copilot" title="GitHub Copilot" onConnected={start}
        blurb="Use the Copilot plan you already pay for. Sign-in opens in GitHub's own app." />}
      {!!subs?.some((s) => !s.available) && (
        <details className="other-plans">
          <summary>Other plans: not available yet</summary>
          <div className="col" style={{ gap: 0 }}>
            {subs?.filter((s) => !s.available).map((s) => (
              <div key={s.id} className="sub-card off">
                <div className="row">
                  <strong className="sub-name">{s.name}</strong>
                  <span className="badge">Not available yet</span>
                  <div className="spacer" />
                  <span className="tiny muted">{s.plans}</span>
                </div>
                <p className="small muted">{s.detail} <a href={s.source} target="_blank" rel="noreferrer">Why</a></p>
                <div><button className="btn sm ghost" onClick={() => onKey(s.id === "claude" ? "anthropic" : s.id === "grok" ? "xai" : "gemini")}>Use a {s.name === "Gemini" ? "free Gemini" : s.name} key instead <ArrowRight size={13} /></button></div>
              </div>
            ))}
          </div>
        </details>
      )}
      <div className="row"><Back onBack={onBack} /></div>
    </div>
  );
}

function KeyStep({ presets, info, initial, onBack, onDone }: {
  presets: Record<string, Preset>; info: Record<string, KeyInfo>; initial?: string; onBack: () => void; onDone: () => void;
}) {
  const choices = KEY_ORDER.filter((k) => presets[k]).map((k) => [k, presets[k]] as const);
  const [kind, setKind] = useState(initial && presets[initial] ? initial : "openai");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  const preset = presets[kind];
  const compatible = preset?.auth_kind === "openai_compatible";
  const onDetected = useCallback((p: string | null) => { if (p && presets[p]) setKind(p); }, [presets]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (compatible) {
        const p = await api<{ id: number }>("/providers", {
          body: { name: preset.name, auth_kind: preset.auth_kind, protocol: preset.protocol, base_url: baseUrl.trim() || preset.base_url, api_key: key.trim() },
        });
        const models = await modelsFor(p.id);
        if (!models.length) {
          await api(`/providers/${p.id}`, { method: "DELETE" });
          throw new Error("That server answered with no models. Check the address.");
        }
      } else {
        const r = await api<Validation>("/providers/validate", { body: { api_key: key.trim(), preset: kind, save: true } });
        if (!r.ok) throw new Error(`${r.name} didn't accept that key (${r.detail}).`);
      }
      setAdded((a) => [...a, preset.name]);
      setKey("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="col gap-lg" onSubmit={submit}>
      <div>
        <div className="kicker">API keys</div>
        <h1>Paste your keys</h1>
        <p className="muted">Paste any key and the provider is recognised. Add as many as you like; the app uses them in turn.</p>
      </div>
      {error && <div className="alert err">{error}</div>}
      {added.length > 0 && <div className="row small"><Check size={14} /> Connected: {added.join(", ")}</div>}
      <div className="welcome-providers" role="radiogroup" aria-label="Provider">
        {choices.map(([k, p]) => (
          <button type="button" key={k} role="radio" aria-checked={k === kind} className={`welcome-choice ${k === kind ? "on" : ""}`} onClick={() => setKind(k)}>
            {k === "compatible" ? "Any OpenAI-compatible URL" : p.name}
          </button>
        ))}
      </div>
      {preset?.note && <div className="muted small">{preset.note}</div>}
      {compatible && (
        <label className="field">Server address<input className="input mono" value={baseUrl} placeholder={preset.base_url} onChange={(e) => setBaseUrl(e.target.value)} /></label>
      )}
      <label className="field">
        <span>API key {compatible && <span className="hint">Optional for local servers</span>}</span>
        <KeyField value={key} onChange={setKey} autoFocus onDetected={compatible ? undefined : onDetected} />
        {info[kind]?.url && <a className="small" href={info[kind].url} target="_blank" rel="noreferrer">Get {/^[AEIOU]/i.test(preset.name) ? "an" : "a"} {preset.name} key <ExternalLink size={11} /></a>}
      </label>
      <div className="row muted small"><ShieldCheck size={14} /> Your key stays on this computer (in the system keychain where available) and is only sent to {preset?.name}.</div>
      <div className="row">
        <Back onBack={onBack} />
        <div className="spacer" />
        {key.trim().length >= 8 || (compatible && baseUrl) ? (
          <button className="btn primary" disabled={busy}>{busy ? <><Loader2 size={16} className="spin" /> Checking key…</> : <>Check & connect <ArrowRight size={16} /></>}</button>
        ) : (
          <button type="button" className="btn primary" disabled={!added.length} onClick={async () => { await finishCloud(); onDone(); }}>Start chatting <ArrowRight size={16} /></button>
        )}
      </div>
    </form>
  );
}

function OllamaStep({ preset, onBack, onDone }: { preset: Preset; onBack: () => void; onDone: () => void }) {
  const [state, setState] = useState<{ online: boolean; models: string[] } | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const check = () => api<{ online: boolean; models: string[] }>("/ollama/status").then(setState).catch(() => setState({ online: false, models: [] }));
  useEffect(() => {
    check();
  }, []);

  const pull = async () => {
    setError("");
    setPct(0);
    await stream("/ollama/pull", { model: STARTER_MODEL }, (e) => {
      if (e.error) setError(String(e.error));
      const total = Number(e.total ?? 0);
      if (total) setPct(Math.round((Number(e.completed ?? 0) / total) * 100));
    });
    setPct(null);
    check();
  };

  const connect = async () => {
    setBusy(true);
    setError("");
    try {
      const p = await api<{ id: number }>("/providers", { body: { name: preset.name, auth_kind: preset.auth_kind, protocol: preset.protocol, base_url: preset.base_url } });
      await finish(p.id, state?.models.includes(STARTER_MODEL) ? STARTER_MODEL : state?.models[0]);
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="col gap-lg">
      <div>
        <h1>Use Ollama on this computer</h1>
        <p className="muted">Free and private: models run on your own machine and nothing leaves it.</p>
      </div>
      {error && <div className="alert err">{error}</div>}
      <div className="card col">
        <div className="row">
          <Cpu size={16} /> <strong>Ollama</strong>
          <div className="spacer" />
          {state === null ? <Loader2 size={14} className="spin" /> : state.online ? <span className="badge ok">Running</span> : <span className="badge danger">Not found</span>}
        </div>
        {state && !state.online && (
          <div className="col">
            <div className="muted small">Install the free Ollama app, open it, then check again.</div>
            <div className="row">
              <a className="btn sm" href="https://ollama.com/download" target="_blank" rel="noreferrer"><Download size={14} /> Get Ollama</a>
              <button className="btn sm ghost" onClick={check}>Check again</button>
            </div>
          </div>
        )}
        {state?.online && !state.models.length && (
          <div className="col">
            <div className="muted small">No models yet. {STARTER_MODEL} is a good first one (about 2.5 GB).</div>
            {pct === null ? (
              <button className="btn sm" onClick={pull}><Download size={14} /> Download {STARTER_MODEL}</button>
            ) : (
              <div className="progress" aria-label="Download progress"><div style={{ width: `${pct}%` }} /></div>
            )}
          </div>
        )}
        {state?.online && state.models.length > 0 && <div className="row small"><Check size={14} /> {state.models.length} model{state.models.length > 1 ? "s" : ""} installed</div>}
      </div>
      <div className="row">
        <button className="btn ghost" onClick={onBack}><ArrowLeft size={16} /> Back</button>
        <div className="spacer" />
        <button className="btn primary" disabled={busy || !state?.online || !state.models.length} onClick={connect}>
          {busy ? <Loader2 size={16} className="spin" /> : <>Start chatting <ArrowRight size={16} /></>}
        </button>
      </div>
    </div>
  );
}

export default function Welcome({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"accounts" | "look" | "choose" | "free" | "subscription" | "key" | "ollama">("accounts");
  const [keyPreset, setKeyPreset] = useState<string>();
  const [presets, setPresets] = useState<Record<string, Preset> | null>(null);
  const [info, setInfo] = useState<Record<string, KeyInfo>>({});
  useEffect(() => {
    api<{ presets: Record<string, Preset>; key_info: Record<string, KeyInfo> }>("/providers")
      .then((r) => { setPresets(r.presets); setInfo(r.key_info ?? {}); })
      .catch(() => setPresets({}));
  }, []);
  const back = () => setStep("choose");
  const welcomeWide = step === "look";

  return (
    <div className="auth welcome">
      <div className={`card auth-card welcome-card${welcomeWide ? " wide" : ""}`} key={step}>
        <div className="steps">{[0, 1, 2].map((i) => <span key={i} className={i <= (step === "accounts" ? 0 : step === "look" ? 1 : 2) ? "on" : ""} />)}</div>
        {step === "accounts" && <AccountsStep onNext={() => setStep("look")} />}
        {step === "look" && (
          <div className="col gap-lg">
            <div className="welcome-hero">
              <div className="kicker">First run · 1 of 3</div>
              <div className="wordmark xl">Rooks</div>
              <p className="muted" style={{ margin: "8px 0 0" }}>Your own workspace and assistant.</p>
              <h1 className="welcome-title">Pick your look</h1>
              <p className="muted">Tap a theme and everything changes straight away, including the browser, the terminal and your phone. You can change it any time in Appearance.</p>
            </div>
            <ThemeGrid />
            <TypeChoices />
            <div className="composer has-tracer look-sample">
              <div className="fake-input">This is how it looks while I'm working…</div>
              <Tracer active />
            </div>
            <div className="look-actions">
              <span className="small muted">Standard is the everyday look. The others are here if you want them.</span>
              <button className="btn primary" onClick={() => { saveAppearanceRemote(readAppearance()); setStep("choose"); }}>Continue <ArrowRight size={14} /></button>
            </div>
          </div>
        )}
        {step === "choose" && (
          <div className="col gap-lg">
            <div className="welcome-hero">
              <div className="kicker">First run · 2 of 3</div>
              <div className="wordmark xl">Rooks</div>
              <p className="muted" style={{ margin: "8px 0 0" }}>Your own workspace and assistant.</p>
              <h1 className="welcome-title">Choose how to think</h1>
              <p className="muted">The thinking happens in the cloud, so your computer stays cool and quiet. You can add more ways later.</p>
            </div>
            <div className="col" style={{ gap: 10 }}>
              <button className="welcome-option recommended" onClick={() => setStep("free")} disabled={!presets}>
                <Cloud size={20} />
                <span className="grow">
                  <span className="row" style={{ gap: 10 }}><strong>Free cloud</strong><span className="tag-rec">Recommended</span></span>
                  <span className="muted small">A free Gemini or Groq key. Two minutes, no card.</span>
                </span>
                <ArrowRight size={16} />
              </button>
              <button className="welcome-option" onClick={() => setStep("subscription")} disabled={!presets}>
                <Sparkles size={20} />
                <span className="grow"><strong>Sign in with your subscription</strong><span className="muted small">ChatGPT, Claude, or GitHub Copilot: sign in with the plan you already pay for. No key needed.</span></span>
                <ArrowRight size={16} />
              </button>
              <button className="welcome-option" onClick={() => { setKeyPreset(undefined); setStep("key"); }} disabled={!presets}>
                <KeyRound size={20} />
                <span className="grow"><strong>Paste API keys</strong><span className="muted small">OpenAI, Anthropic, xAI, Google, Groq, OpenRouter, Mistral, DeepSeek, or any compatible URL.</span></span>
                <ArrowRight size={16} />
              </button>
              <button className="welcome-option quiet" onClick={() => setStep("ollama")} disabled={!presets?.ollama}>
                <Cpu size={20} />
                <span className="grow">
                  <span className="row" style={{ gap: 10 }}><strong>Local Ollama</strong><span className="tag-adv">Advanced</span></span>
                  <span className="muted small">Private and offline. Models run on your machine. Needs a strong computer; it will run warm.</span>
                </span>
                <ArrowRight size={16} />
              </button>
            </div>
            <div className="welcome-trust">
              <span><ShieldCheck size={13} /> Keys stay on this computer</span>
              <span><ListChecks size={13} /> Every action is logged</span>
            </div>
          </div>
        )}
        {step === "free" && <FreeStep info={info} onBack={back} onDone={onDone} />}
        {step === "subscription" && <SubscriptionStep onBack={back} onDone={onDone} onKey={(p) => { setKeyPreset(p); setStep(p === "gemini" ? "free" : "key"); }} />}
        {step === "key" && presets && <KeyStep presets={presets} info={info} initial={keyPreset} onBack={back} onDone={onDone} />}
        {step === "ollama" && presets?.ollama && <OllamaStep preset={presets.ollama} onBack={back} onDone={onDone} />}
      </div>
    </div>
  );
}
