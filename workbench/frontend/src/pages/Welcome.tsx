import { ArrowLeft, ArrowRight, Check, Cpu, Download, ExternalLink, KeyRound, Loader2, ListChecks, ShieldCheck, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, stream } from "../api";

/** First run inside the desktop app: connect one model source, then land in chat. */

type Preset = { name: string; auth_kind: string; protocol: string; base_url: string; note?: string };
type Model = { ref: string; model: string };

const KEY_LINKS: Record<string, string> = {
  openai: "https://platform.openai.com/api-keys",
  anthropic: "https://console.anthropic.com/settings/keys",
  openrouter: "https://openrouter.ai/keys",
  gemini: "https://aistudio.google.com/apikey",
  "opencode-go": "https://opencode.ai/console",
  "opencode-zen": "https://opencode.ai/console",
};
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

function KeyStep({ presets, onBack, onDone }: { presets: Record<string, Preset>; onBack: () => void; onDone: () => void }) {
  const choices = Object.entries(presets).filter(([, p]) => p.auth_kind === "api_key" || p.auth_kind === "openai_compatible");
  const [kind, setKind] = useState(choices[0]?.[0] ?? "openai");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preset = presets[kind];
  const compatible = preset?.auth_kind === "openai_compatible";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    let pid: number | null = null;
    try {
      const p = await api<{ id: number }>("/providers", {
        body: { name: preset.name, auth_kind: preset.auth_kind, protocol: preset.protocol, base_url: (compatible && baseUrl.trim()) || preset.base_url, api_key: key.trim() },
      });
      pid = p.id;
      await finish(p.id);
      onDone();
    } catch (err) {
      if (pid !== null) await api(`/providers/${pid}`, { method: "DELETE" }).catch(() => undefined);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="col gap-lg" onSubmit={submit}>
      <div>
        <h1>Paste an API key</h1>
        <p className="muted">Pick the provider your key is from. You can add more later in Settings.</p>
      </div>
      {error && <div className="alert err">{error}</div>}
      <div className="welcome-providers" role="radiogroup" aria-label="Provider">
        {choices.map(([k, p]) => (
          <button type="button" key={k} role="radio" aria-checked={k === kind} className={`welcome-choice ${k === kind ? "on" : ""}`} onClick={() => setKind(k)}>
            {p.name}
          </button>
        ))}
      </div>
      {preset?.note && <div className="muted small">{preset.note}</div>}
      {compatible && (
        <label className="field">Server address<input className="input mono" value={baseUrl} placeholder={preset.base_url} onChange={(e) => setBaseUrl(e.target.value)} /></label>
      )}
      <label className="field">
        API key {compatible && <span className="hint">Optional for local servers</span>}
        <input className="input mono" type="password" autoFocus autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste your key" />
        {KEY_LINKS[kind] && (
          <a className="small" href={KEY_LINKS[kind]} target="_blank" rel="noreferrer">Get a {preset.name} key <ExternalLink size={11} /></a>
        )}
      </label>
      <div className="row muted small"><ShieldCheck size={14} /> Your key stays on this computer (in the system keychain where available) and is only sent to {preset?.name}.</div>
      <div className="row">
        <button type="button" className="btn ghost" onClick={onBack}><ArrowLeft size={16} /> Back</button>
        <div className="spacer" />
        <button className="btn primary" disabled={busy || (!compatible && key.trim().length < 8)}>
          {busy ? <><Loader2 size={16} className="spin" /> Checking key…</> : <>Connect <ArrowRight size={16} /></>}
        </button>
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
  const [step, setStep] = useState<"choose" | "key" | "ollama">("choose");
  const [presets, setPresets] = useState<Record<string, Preset> | null>(null);
  useEffect(() => {
    api<{ presets: Record<string, Preset> }>("/providers").then((r) => setPresets(r.presets)).catch(() => setPresets({}));
  }, []);

  return (
    <div className="auth welcome">
      <div className="card auth-card welcome-card" key={step}>
        <div className="steps">{[0, 1].map((i) => <span key={i} className={i <= (step === "choose" ? 0 : 1) ? "on" : ""} />)}</div>
        {step === "choose" && (
          <div className="col gap-lg">
            <div className="welcome-hero">
              <div className="brand-mark lg" aria-hidden="true"><Sparkles size={22} /></div>
              <h1>Welcome to AI Workspace</h1>
              <p className="muted">Connect a model to get started. Everything else (notes, tasks, research, the run log) works on this computer.</p>
            </div>
            <div className="col" style={{ gap: 10 }}>
              <button className="welcome-option" onClick={() => setStep("key")} disabled={!presets}>
                <KeyRound size={20} />
                <span className="grow"><strong>Paste an API key</strong><span className="muted small">OpenAI, Anthropic, OpenRouter, Gemini and more.</span></span>
                <ArrowRight size={16} />
              </button>
              <button className="welcome-option" onClick={() => setStep("ollama")} disabled={!presets?.ollama}>
                <Cpu size={20} />
                <span className="grow"><strong>Use local Ollama</strong><span className="muted small">Free, private, runs on your machine.</span></span>
                <ArrowRight size={16} />
              </button>
            </div>
            <div className="welcome-trust">
              <span><ShieldCheck size={13} /> Keys stay on this computer</span>
              <span><ListChecks size={13} /> Every action is logged</span>
            </div>
          </div>
        )}
        {step === "key" && presets && <KeyStep presets={presets} onBack={() => setStep("choose")} onDone={onDone} />}
        {step === "ollama" && presets?.ollama && <OllamaStep preset={presets.ollama} onBack={() => setStep("choose")} onDone={onDone} />}
      </div>
    </div>
  );
}
