import { ArrowRight, Check, Cpu, Download, KeyRound, Loader2, Lock, Sparkles } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, stream } from "../api";
import type { Status } from "../App";

const SUGGESTED = [
  { model: "qwen3:4b", note: "Fast all rounder, ~2.5 GB. Good on CPU." },
  { model: "qwen3:8b", note: "Stronger reasoning, ~5 GB. Best with a GPU." },
  { model: "gemma3:4b", note: "Google's small model, ~3 GB." },
  { model: "nomic-embed-text", note: "Embeddings for document search in Chat." },
];

function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/login", { body: { password } });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="col gap-lg" onSubmit={submit}>
      <div className="auth-brand">
        <div className="brand-mark lg" aria-hidden="true"><Sparkles size={22} /></div>
        <div>
          <h1>Welcome back</h1>
          <p className="muted">Sign in to your workspace.</p>
        </div>
      </div>
      {error && <div className="alert err">{error}</div>}
      <label className="field">
        Password
        <input className="input" type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <button className="btn primary" disabled={busy || !password}>{busy ? <Loader2 size={16} className="spin" /> : "Sign in"}</button>
    </form>
  );
}

function ModelsStep({ onNext }: { onNext: () => void }) {
  const [ollama, setOllama] = useState<{ online: boolean; models: string[] } | null>(null);
  const [pulling, setPulling] = useState<string>("");
  const [progress, setProgress] = useState<{ status: string; pct: number }>({ status: "", pct: 0 });
  const [keys, setKeys] = useState({ openai: "", anthropic: "", openrouter: "", gemini: "" });
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const check = () => api<{ online: boolean; models: string[] }>("/ollama/status").then(setOllama).catch(() => setOllama({ online: false, models: [] }));
  useEffect(() => {
    check();
  }, []);

  const pull = async (model: string) => {
    setPulling(model);
    setError("");
    await stream("/ollama/pull", { model }, (e) => {
      if (e.error) setError(String(e.error));
      const total = Number(e.total ?? 0);
      const done = Number(e.completed ?? 0);
      setProgress({ status: String(e.status ?? ""), pct: total ? Math.round((done / total) * 100) : 0 });
    });
    setPulling("");
    check();
  };

  const saveKeys = async () => {
    setError("");
    try {
      const { providers, presets } = await api<{ providers: { kind: string; base_url: string }[]; presets: Record<string, { name: string; base_url: string }> }>("/providers");
      for (const [kind, key] of Object.entries(keys)) {
        if (!key.trim()) continue;
        const preset = presets[kind];
        if (providers.some((p) => p.base_url === preset.base_url)) continue;
        await api("/providers", { body: { name: preset.name, kind: kind === "anthropic" ? "anthropic" : "openai", base_url: preset.base_url, api_key: key.trim() } });
      }
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="col gap-lg">
      <div>
        <h1>Connect models</h1>
        <p className="muted">Local models run on this server through Ollama. Cloud providers are optional and can be added later in Settings.</p>
      </div>
      {error && <div className="alert err">{error}</div>}
      <div className="card col">
        <div className="row">
          <Cpu size={16} />
          <h3>Local models (Ollama)</h3>
          <div className="spacer" />
          {ollama === null ? <Loader2 size={14} className="spin" /> : ollama.online ? <span className="badge ok">Online</span> : <span className="badge danger">Offline</span>}
        </div>
        {ollama && !ollama.online && <div className="alert">Ollama is not reachable yet. It may still be starting. <button className="btn sm" onClick={check}>Retry</button></div>}
        {ollama?.online &&
          SUGGESTED.map((s) => {
            const have = ollama.models.some((m) => m === s.model || m === `${s.model}:latest`);
            return (
              <div key={s.model} className="row">
                <div className="grow">
                  <div className="mono">{s.model}</div>
                  <div className="muted small">{s.note}</div>
                </div>
                {have ? (
                  <span className="badge ok"><Check size={12} /> Installed</span>
                ) : (
                  <button className="btn sm" disabled={!!pulling} onClick={() => pull(s.model)}>
                    {pulling === s.model ? <Loader2 size={14} className="spin" /> : <Download size={14} />} {pulling === s.model ? `${progress.pct}%` : "Download"}
                  </button>
                )}
              </div>
            );
          })}
        {pulling && (
          <div className="col">
            <div className="progress"><div style={{ width: `${progress.pct}%` }} /></div>
            <div className="muted small">{progress.status}</div>
          </div>
        )}
      </div>
      <div className="card col">
        <div className="row"><KeyRound size={16} /><h3>Cloud providers (optional)</h3></div>
        <label className="field">OpenAI API key<input className="input" type="password" value={keys.openai} onChange={(e) => setKeys({ ...keys, openai: e.target.value })} placeholder="sk-…" /></label>
        <label className="field">Anthropic API key<input className="input" type="password" value={keys.anthropic} onChange={(e) => setKeys({ ...keys, anthropic: e.target.value })} placeholder="sk-ant-…" /></label>
        <label className="field">OpenRouter API key<input className="input" type="password" value={keys.openrouter} onChange={(e) => setKeys({ ...keys, openrouter: e.target.value })} placeholder="sk-or-…" /><span className="faint small">Free key. Models ending in :free cost nothing.</span></label>
        <label className="field">Google Gemini API key<input className="input" type="password" value={keys.gemini} onChange={(e) => setKeys({ ...keys, gemini: e.target.value })} placeholder="AIza…" /><span className="faint small">Free tier from aistudio.google.com. Google may use free tier prompts to improve its products.</span></label>
        <div className="row">
          <button className="btn" onClick={saveKeys} disabled={!Object.values(keys).some((k) => k.trim())}>Save keys</button>
          {saved && <span className="badge ok"><Check size={12} /> Saved</span>}
        </div>
        <div className="muted small">Keys are encrypted at rest with your APP_SECRET. They apply to Workbench. For the Chat app, add them in its Admin Settings → Connections, or set them in .env before running setup.sh.</div>
      </div>
      <button className="btn primary" onClick={onNext} disabled={!!pulling}>Continue <ArrowRight size={16} /></button>
    </div>
  );
}

export default function Auth({ status, onDone }: { status: Status; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ password: "", confirm: "", setup_code: "", workspace_name: status.workspace_name });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (status.setup_complete && step === 0) {
    return (
      <div className="auth"><div><div className="card auth-card"><Login onDone={onDone} /></div><div className="auth-foot"><Lock size={12} /> Self-hosted. Your data stays on your server.</div></div></div>
    );
  }

  const createAccount = async (e: FormEvent) => {
    e.preventDefault();
    if (form.password !== form.confirm) return setError("Passwords do not match");
    setBusy(true);
    setError("");
    try {
      await api("/setup", { body: { password: form.password, setup_code: form.setup_code, workspace_name: form.workspace_name } });
      setStep(1);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="card auth-card" style={{ width: step === 1 ? "min(620px, 100%)" : undefined }}>
        <div className="steps">{[0, 1, 2].map((i) => <span key={i} className={i <= step ? "on" : ""} />)}</div>
        {step === 0 && (
          <form className="col gap-lg" onSubmit={createAccount}>
            <div className="auth-brand">
              <div className="brand-mark lg" aria-hidden="true"><Sparkles size={22} /></div>
              <div>
                <h1>Set up your workspace</h1>
                <p className="muted">Create the password that protects Workbench. This is a single owner app.</p>
              </div>
            </div>
            {error && <div className="alert err">{error}</div>}
            {status.setup_code_required && (
              <label className="field">
                Setup code <span className="hint">Printed by ./setup.sh, also stored as SETUP_CODE in .env</span>
                <input className="input mono" value={form.setup_code} onChange={(e) => setForm({ ...form, setup_code: e.target.value })} autoFocus />
              </label>
            )}
            <label className="field">Workspace name<input className="input" value={form.workspace_name} onChange={(e) => setForm({ ...form, workspace_name: e.target.value })} /></label>
            <label className="field">Password <span className="hint">At least 10 characters</span>
              <input className="input" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </label>
            <label className="field">Confirm password<input className="input" type="password" value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} /></label>
            <button className="btn primary" disabled={busy || form.password.length < 10}>{busy ? <Loader2 size={16} className="spin" /> : <>Create workspace <ArrowRight size={16} /></>}</button>
          </form>
        )}
        {step === 1 && <ModelsStep onNext={() => setStep(2)} />}
        {step === 2 && (
          <div className="col gap-lg">
            <h1>You're set</h1>
            <div className="col">
              <div className="row"><Check size={16} className="prio-1" /> Workbench: notes, documents, tasks, calendar, mail, research and model compare.</div>
              <div className="row"><Check size={16} className="prio-1" /> Chat at <a href={status.chat_url} target="_blank" rel="noreferrer">{status.chat_url}</a>. The first account you create there becomes its admin.</div>
              <div className="row"><Check size={16} className="prio-1" /> In Chat, turn on the Workbench tools (the + menu in the message box) so agents can use your notes, tasks and calendar.</div>
            </div>
            <div className="muted small">Connect mail and calendars any time under Settings.</div>
            <button className="btn primary" onClick={onDone}>Open workspace <ArrowRight size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}
