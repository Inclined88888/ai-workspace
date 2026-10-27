import { ArrowLeft, ArrowRight, Check, CheckSquare, Cpu, Download, Loader2, MessageSquare, NotebookPen, Rocket, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, stream } from "../api";
import type { Status } from "../App";
import RookMark from "../components/RookMark";
import { PasswordInput, Steps } from "../components/ui";
import { ConnectProvider } from "../components/Models";

const MIN_PASSWORD = 10; // matches the server's rule for setup and password changes

const SUGGESTED = [
  { model: "qwen3:4b", note: "Fast all rounder, about 2.5 GB. Fine without a graphics card." },
  { model: "qwen3:8b", note: "Better reasoning, about 5 GB. Best with a graphics card." },
  { model: "gemma3:4b", note: "Google's small model, about 3 GB." },
];

function Brand() {
  return <div className="row"><div className="brand-mark"><RookMark size={18} /></div><span className="wordmark">ROOKS</span></div>;
}

function Login({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api("/login", { body: { password } });
      onDone();
    } catch (err) {
      const msg = (err as Error).message;
      // One owner, one password: a generic message reveals nothing extra
      setError(msg === "Wrong password" ? "That password didn't work. Check Caps Lock and try again." : msg);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="col gap-lg" onSubmit={submit} noValidate>
      <Brand />
      <div>
        <h1>Welcome back</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>Your move. ROOKS delivers.</p>
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="col" style={{ gap: 5 }}>
        <label htmlFor="login-password" className="field">Password</label>
        <PasswordInput id="login-password" value={password} onChange={setPassword} autoFocus autoComplete="current-password" invalid={!!error} />
      </div>
      <button className="btn primary lg" disabled={busy || !password}>{busy ? <><Loader2 size={16} className="spin" /> Signing in…</> : "Sign in"}</button>
    </form>
  );
}

type Readiness = { providers: number };
type TestResult = { ok: boolean; model?: string; reply?: string; error?: string; ms?: number };

export function ModelTest({ auto = false }: { auto?: boolean }) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [r, setR] = useState<TestResult | null>(null);
  const run = async () => {
    setState("busy");
    try { setR(await api<TestResult>("/models/test", { body: {} })); } catch (e) { setR({ ok: false, error: (e as Error).message }); }
    setState("done");
  };
  useEffect(() => { if (auto) run(); }, [auto]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="col" style={{ gap: 6 }} aria-live="polite">
      <div className="row wrap">
        <button type="button" className="btn sm" onClick={run} disabled={state === "busy"}>
          {state === "busy" ? <><Loader2 size={14} className="spin" /> Checking…</> : "Check the assistant can answer"}
        </button>
        {r?.ok && <span className="badge ok"><Check size={12} /> Working: {r.model} answered in {((r.ms ?? 0) / 1000).toFixed(1)} s</span>}
      </div>
      {r && !r.ok && <div className="alert err">The assistant could not reach a model: {r.error}. Check the key, or pick another default model in Settings.</div>}
    </div>
  );
}

function ModelsStep() {
  const [ollama, setOllama] = useState<{ online: boolean; models: string[]; configured?: boolean } | null>(null);
  const [pulling, setPulling] = useState("");
  const [progress, setProgress] = useState<{ status: string; pct: number }>({ status: "", pct: 0 });
  const [error, setError] = useState("");
  const check = () => api<{ online: boolean; models: string[]; configured?: boolean }>("/ollama/status").then(setOllama).catch(() => setOllama({ online: false, models: [] }));
  useEffect(() => { check(); }, []);

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

  return (
    <div className="col gap-lg">
      <div>
        <h1>Connect an AI model</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>The assistant, research and agents need a model to think with. Pick one way below; you can change it any time in Settings.</p>
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      <div className="card col">
        <h3>Paste a key from one provider</h3>
        <p className="muted small" style={{ margin: 0 }}>ROOKS checks the key, finds a model that works (free ones first if the account has no credits) and tests it before using it.</p>
        <ConnectProvider onConnected={() => undefined} localAvailable={!!ollama?.online} />
      </div>
      <div className="card col">
        <div className="row">
          <Cpu size={16} aria-hidden="true" /><h3 className="grow">Or run models on this computer</h3>
          {ollama === null ? <Loader2 size={14} className="spin" aria-label="Checking" /> : ollama.online ? <span className="badge ok">Ollama found</span> : null}
        </div>
        {ollama && !ollama.online && ollama.configured === false && (
          <p className="muted small" style={{ margin: 0 }}>Local models are off. On a Mac, install the Ollama app and run setup again; on a server, run <span className="mono">./setup.sh --with local-models</span>. Free and private, but slower than cloud models.</p>
        )}
        {ollama && !ollama.online && ollama.configured !== false && (
          <div className="alert">Ollama is set up but not answering yet. It may still be starting. <button type="button" className="btn sm" onClick={check}>Try again</button></div>
        )}
        {ollama?.online && SUGGESTED.map((s) => {
          const have = ollama.models.some((m) => m === s.model || m === `${s.model}:latest`);
          return (
            <div key={s.model} className="row">
              <div className="grow"><div className="mono">{s.model}</div><div className="muted small">{s.note}</div></div>
              {have ? <span className="badge ok"><Check size={12} /> Ready</span> : (
                <button type="button" className="btn sm" disabled={!!pulling} onClick={() => pull(s.model)}>
                  {pulling === s.model ? <><Loader2 size={14} className="spin" /> {progress.pct}%</> : <><Download size={14} /> Download</>}
                </button>
              )}
            </div>
          );
        })}
        {pulling && <div className="col" aria-live="polite"><div className="progress" role="progressbar" aria-valuenow={progress.pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${progress.pct}%` }} /></div><div className="muted small">{progress.status}</div></div>}
      </div>
    </div>
  );
}

const STEPS = ["Welcome", "Secure", "Name", "Start", "Done"];

export default function Auth({ status, onDone }: { status: Status; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ password: "", confirm: "", setup_code: "", workspace_name: status.workspace_name });
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState<"quick" | "custom">("quick");
  const [customising, setCustomising] = useState(false);
  const [ready, setReady] = useState<Readiness | null>(null);

  useEffect(() => { if (step >= 3) api<Readiness>("/getting-started").then(setReady).catch(() => setReady(null)); }, [step, customising]);

  if (status.setup_complete && step === 0) {
    return <div className="auth"><div className="card auth-card"><Login onDone={onDone} /></div></div>;
  }

  const pwShort = form.password.length < MIN_PASSWORD;
  const pwMismatch = form.confirm.length > 0 && form.confirm !== form.password;
  const codeMissing = status.setup_code_required && !form.setup_code.trim();

  const nextFromSecure = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (codeMissing || pwShort || form.password !== form.confirm) return;
    setError("");
    setStep(2);
  };

  const createWorkspace = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api("/setup", { body: { password: form.password, setup_code: form.setup_code, workspace_name: form.workspace_name } });
      setStep(3);
    } catch (err) {
      const msg = (err as Error).message;
      if (msg.toLowerCase().includes("setup code")) { setError("The setup code doesn't match. Copy it again from the setup output or SETUP_CODE in .env."); setStep(1); }
      else if (msg.includes("already completed")) setError("This workspace is already set up. Reload the page to sign in.");
      else setError(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="card auth-card" style={{ width: customising ? "min(640px, 100%)" : undefined }}>
        <Steps labels={STEPS} current={step} />
        {error && <div className="alert err" role="alert" style={{ marginBottom: 14 }}>{error}</div>}

        {step === 0 && (
          <div className="col gap-lg">
            <Brand />
            <div>
              <h1>Your private workspace for getting work done</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>ROOKS keeps your tasks, notes, calendar and research in one place, with an assistant that remembers how you work and helps you finish things. Everything stays on this server.</p>
            </div>
            <ul className="col" style={{ listStyle: "none", padding: 0, margin: 0, gap: 8 }}>
              <li className="row"><MessageSquare size={16} className="prio-1" aria-hidden="true" /> Ask for help in plain English</li>
              <li className="row"><CheckSquare size={16} className="prio-1" aria-hidden="true" /> Keep tasks, notes and plans together</li>
              <li className="row"><ShieldCheck size={16} className="prio-1" aria-hidden="true" /> Private by default: you own the data</li>
            </ul>
            <p className="muted small" style={{ margin: 0 }}>Setup takes about two minutes.</p>
            <button className="btn primary lg" onClick={() => setStep(1)} autoFocus>Get started <ArrowRight size={16} /></button>
          </div>
        )}

        {step === 1 && (
          <form className="col gap-lg" onSubmit={nextFromSecure} noValidate>
            <div>
              <h1>Secure your workspace</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>ROOKS has one owner: you. This password is the only way in, so pick something long that you don't use elsewhere. A password manager helps.</p>
            </div>
            {status.setup_code_required && (
              <div className="col" style={{ gap: 5 }}>
                <label htmlFor="setup-code" className="field">Setup code <span className="hint">Shown at the end of the install, and saved as SETUP_CODE in the .env file. It proves you are the person who installed ROOKS.</span></label>
                <input id="setup-code" className="input mono" value={form.setup_code} autoFocus autoComplete="off" aria-invalid={(touched && codeMissing) || undefined} aria-describedby="code-msg"
                  onChange={(e) => setForm({ ...form, setup_code: e.target.value })} />
                {touched && codeMissing && <span id="code-msg" className="field-msg err">Enter the setup code to continue.</span>}
              </div>
            )}
            <div className="col" style={{ gap: 5 }}>
              <label htmlFor="new-password" className="field">Password</label>
              <PasswordInput id="new-password" value={form.password} onChange={(v) => setForm({ ...form, password: v })} autoFocus={!status.setup_code_required}
                autoComplete="new-password" invalid={touched && pwShort} describedBy="pw-msg" />
              <span id="pw-msg" className={`field-msg ${form.password && !pwShort ? "ok" : touched && pwShort ? "err" : "muted"}`}>
                {form.password && !pwShort ? <><Check size={12} /> Long enough</> : `At least ${MIN_PASSWORD} characters${form.password ? ` (${MIN_PASSWORD - form.password.length} to go)` : ""}`}
              </span>
            </div>
            <div className="col" style={{ gap: 5 }}>
              <label htmlFor="confirm-password" className="field">Type it again</label>
              <PasswordInput id="confirm-password" value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} autoComplete="new-password"
                invalid={(touched || form.confirm.length >= form.password.length) && pwMismatch} describedBy="confirm-msg" />
              <span id="confirm-msg" className={`field-msg ${pwMismatch ? "err" : form.confirm ? "ok" : "muted"}`} aria-live="polite">
                {pwMismatch ? "The two passwords don't match yet." : form.confirm ? <><Check size={12} /> Matches</> : " "}
              </span>
            </div>
            <div className="row">
              <button type="button" className="btn ghost" onClick={() => setStep(0)}><ArrowLeft size={16} /> Back</button>
              <div className="spacer" />
              <button className="btn primary">Continue <ArrowRight size={16} /></button>
            </div>
          </form>
        )}

        {step === 2 && (
          <form className="col gap-lg" onSubmit={createWorkspace}>
            <div>
              <h1>Name your workspace</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>Shown in the sidebar and the browser tab, so you can tell workspaces apart. It changes nothing about security or networking, and you can rename it later in Settings.</p>
            </div>
            <div className="col" style={{ gap: 5 }}>
              <label htmlFor="ws-name" className="field">Workspace name</label>
              <input id="ws-name" className="input" maxLength={80} value={form.workspace_name} autoFocus placeholder="ROOKS"
                onChange={(e) => setForm({ ...form, workspace_name: e.target.value })} />
              <span className="field-msg muted">For example: Acme HQ, Personal, Studio.</span>
            </div>
            <div className="row">
              <button type="button" className="btn ghost" onClick={() => setStep(1)} disabled={busy}><ArrowLeft size={16} /> Back</button>
              <div className="spacer" />
              <button className="btn primary" disabled={busy}>{busy ? <><Loader2 size={16} className="spin" /> Creating…</> : <>Create workspace <ArrowRight size={16} /></>}</button>
            </div>
          </form>
        )}

        {step === 3 && !customising && (
          <div className="col gap-lg">
            <div>
              <h1>How do you want to start?</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>Your workspace is created and you're signed in.</p>
            </div>
            <div className="col" role="radiogroup" aria-label="Starting experience">
              <button type="button" className="choice" role="radio" aria-checked={path === "quick"} onClick={() => setPath("quick")}>
                <Rocket size={20} className="prio-1" aria-hidden="true" />
                <span><strong>Just get started</strong><br /><span className="muted small">Use tasks, notes, documents and calendar right away. Add an AI model later when you want the assistant.</span></span>
              </button>
              <button type="button" className="choice" role="radio" aria-checked={path === "custom"} onClick={() => setPath("custom")}>
                <SlidersHorizontal size={20} className="prio-1" aria-hidden="true" />
                <span><strong>Set up the assistant now</strong><br /><span className="muted small">Connect a cloud provider or local models so the assistant, research and agents work from the start.</span></span>
              </button>
            </div>
            {ready && ready.providers > 0 && <div className="alert ok">A model provider is already configured on this server.</div>}
            <button className="btn primary lg" onClick={() => (path === "custom" ? setCustomising(true) : setStep(4))}>Continue <ArrowRight size={16} /></button>
          </div>
        )}

        {step === 3 && customising && (
          <div className="col gap-lg">
            <ModelsStep />
            <div className="row">
              <button type="button" className="btn ghost" onClick={() => setCustomising(false)}><ArrowLeft size={16} /> Back</button>
              <div className="spacer" />
              <button className="btn primary" onClick={() => { setCustomising(false); setStep(4); }}>Continue <ArrowRight size={16} /></button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className="col gap-lg">
            <Brand />
            <div>
              <h1>You're ready</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>Here's a good first few minutes. Your Home page keeps this list until you're done or hide it.</p>
            </div>
            <ul className="checklist">
              <li><span className="tick" aria-hidden="true" /><MessageSquare size={16} aria-hidden="true" /><span className="what">Ask ROOKS something</span></li>
              <li><span className="tick" aria-hidden="true" /><CheckSquare size={16} aria-hidden="true" /><span className="what">Create a task</span></li>
              <li><span className="tick" aria-hidden="true" /><NotebookPen size={16} aria-hidden="true" /><span className="what">Save a note</span></li>
            </ul>
            {ready && ready.providers === 0
              ? <div className="alert">No AI model is connected yet, so the assistant can't answer. Tasks, notes and calendar work without one. Add a model under Settings, Model providers.</div>
              : ready && <ModelTest />}
            {status.chat_url && <p className="muted small" style={{ margin: 0 }}>The Open WebUI add-on is at <a href={status.chat_url} target="_blank" rel="noreferrer">{status.chat_url}</a>; the first account created there becomes its admin.</p>}
            <button className="btn primary lg" onClick={onDone} autoFocus>Open ROOKS <ArrowRight size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}
