import { ArrowLeft, ArrowRight, Check, CheckSquare, Loader2, MessageSquare, NotebookPen, ShieldCheck } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import type { Status } from "../App";
import RookMark from "../components/RookMark";
import { PasswordInput, Steps } from "../components/ui";
import { Mode, MODES, ModeChooser, ModeSetup, saveMode } from "../components/AiSetup";

const MIN_PASSWORD = 10; // matches the server's rule for setup and password changes

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

const STEPS = ["Welcome", "Secure", "Name", "AI", "Done"];
const browserTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } })();

export default function Auth({ status, onDone }: { status: Status; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState({ password: "", confirm: "", setup_code: "", workspace_name: status.workspace_name });
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<Mode>("free");  // Free Cloud AI is the first-run default
  const [customising, setCustomising] = useState(false);
  const [limit, setLimit] = useState(0);
  const [connected, setConnected] = useState(false);
  const [ready, setReady] = useState<Readiness | null>(null);

  useEffect(() => { if (step >= 3) api<Readiness>("/getting-started").then(setReady).catch(() => setReady(null)); }, [step, customising, connected]);
  const chooseMode = async () => {
    setError("");
    if (mode === "premium") { setCustomising(true); return; }  // saved once the spending limit is set
    try { await saveMode(mode); setCustomising(true); } catch (err) { setError((err as Error).message); }
  };
  const setPremiumLimit = async (cents: number) => {
    try { const r = await saveMode("premium", cents); setLimit(r.monthly_limit_cents); setError(""); } catch (err) { setError((err as Error).message); }
  };
  const skipAi = async () => {
    // Skipping keeps Free Cloud AI: nothing that costs money can run until you choose otherwise in Settings
    try { await saveMode("free"); } catch { /* the server already defaults a new workspace to free */ }
    setMode("free"); setCustomising(false); setStep(4);
  };
  const modeTitle = MODES.find((m) => m.id === mode)?.title ?? "";

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
      // Reminders and automations follow this browser's timezone from the start (shown on the last step; changeable)
      if (browserTz) await api("/assistant/settings", { method: "PUT", body: { timezone: browserTz } }).catch(() => undefined);
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
      <div className="card auth-card" style={{ width: step === 3 ? "min(640px, 100%)" : undefined }}>
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
              <h1>How should ROOKS use AI?</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>Your workspace is created and you're signed in. The assistant, research and missions need an AI model.
                Free Cloud AI is selected: it needs no download and costs nothing. You can switch at any time in Settings.</p>
            </div>
            <ModeChooser value={mode} onChange={setMode} idPrefix="onboard-mode" />
            {ready && ready.providers > 0 && <div className="alert ok">A model provider is already configured on this server. It is kept whichever mode you choose.</div>}
            <div className="row wrap">
              <button type="button" className="btn ghost" onClick={skipAi}>Skip for now</button>
              <div className="spacer" />
              <button className="btn primary lg" onClick={chooseMode}>Continue with {modeTitle} <ArrowRight size={16} /></button>
            </div>
            <p className="small muted" style={{ margin: 0 }}>Skipping keeps Free Cloud AI with nothing connected. Tasks, notes and calendar work without AI.</p>
          </div>
        )}

        {step === 3 && customising && (
          <div className="col gap-lg">
            <div>
              <h1>{modeTitle}</h1>
              <p className="muted" style={{ margin: "6px 0 0" }}>{MODES.find((m) => m.id === mode)?.about}</p>
            </div>
            <ModeSetup mode={mode} limitCents={limit} onLimit={setPremiumLimit} onConnected={(_, r) => { if (r.ok) setConnected(true); }} />
            <div className="row wrap">
              <button type="button" className="btn ghost" onClick={() => setCustomising(false)}><ArrowLeft size={16} /> Choose another way</button>
              <div className="spacer" />
              <button className={`btn ${connected ? "primary" : ""}`} disabled={mode === "premium" && limit <= 0} onClick={() => { setCustomising(false); setStep(4); }}>
                {connected ? <>Continue <ArrowRight size={16} /></> : <>Continue without connecting <ArrowRight size={16} /></>}</button>
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
              ? <div className="alert">No AI model is connected yet, so the assistant can't answer. Tasks, notes and calendar work without one. Connect one under Settings, AI models.</div>
              : ready && <ModelTest />}
            {browserTz && <p className="small muted" style={{ margin: 0 }}>Reminders and automations use your timezone, <strong>{browserTz}</strong>, taken from this browser. You can change it in Settings, under Automations and notifications.</p>}
            <div className="onboard-note small">
              <strong>Your privacy.</strong> ROOKS runs on your own machine. It sends text to the AI provider you connected, and asks before anything
              with lasting effects after reading outside content. You can review what it remembers, and choose local-only projects, under Settings.
            </div>
            <div className="small muted">Later, if you like: <a href="/capabilities">see what ROOKS can do</a> · try <strong>Voice</strong> in the Assistant ·
              pair your computer on the <strong>Computer</strong> page. None of these are needed to start.</div>
            {status.chat_url && <p className="muted small" style={{ margin: 0 }}>The Open WebUI add-on is at <a href={status.chat_url} target="_blank" rel="noreferrer">{status.chat_url}</a>; the first account created there becomes its admin.</p>}
            <button className="btn primary lg" onClick={onDone} autoFocus>Open ROOKS <ArrowRight size={16} /></button>
          </div>
        )}
      </div>
    </div>
  );
}
