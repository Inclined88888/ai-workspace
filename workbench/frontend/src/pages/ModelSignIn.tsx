import { ArrowRight, Check, ClipboardPaste, ExternalLink, Loader2, ShieldCheck, UserRound } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import PlanCard from "./PlanCard";

/** Connecting a model without jargon, shared by the first-run welcome and Settings: sign in with a plan you already
 * pay for (ChatGPT, Claude), or paste a key and the provider is recognised from it. */

export type Preset = { name: string; auth_kind: string; protocol: string; base_url: string; note?: string };
export type KeyInfo = { url: string; prefix: string; free?: boolean; limits?: string };
export type Validation = { ok: boolean; preset: string; name: string; detail: string; health: string };
export type Sub = { id: string; name: string; plans: string; available: boolean; detail: string; source: string; via?: string };
export const KEY_ORDER = ["openai", "anthropic", "xai", "gemini", "groq", "openrouter", "mistral", "deepseek", "compatible"];

/** Paste box that recognises the provider from the key itself, and picks a key up from the clipboard when you come
 * back from the provider's page (only when the clipboard holds something shaped like a key for it). */
export function KeyField({ value, onChange, expect, autoFocus, onDetected }: {
  value: string; onChange: (v: string) => void; expect?: string; autoFocus?: boolean; onDetected?: (preset: string | null, name: string | null) => void;
}) {
  const [found, setFound] = useState("");
  const detect = useCallback(async (k: string) => {
    if (k.trim().length < 8) return onDetected?.(null, null);
    const r = await api<{ preset: string | null; name: string | null }>("/providers/detect", { body: { api_key: k.trim() } }).catch(() => null);
    if (r) onDetected?.(r.preset, r.name);
  }, [onDetected]);
  const fromClipboard = useCallback(async (quiet: boolean) => {
    try {
      const t = (await navigator.clipboard.readText()).trim();
      if (!t || t.length > 300 || /\s/.test(t) || (expect && !t.startsWith(expect))) {
        if (!quiet) setFound("Your clipboard doesn't hold a key yet.");
        return;
      }
      onChange(t);
      setFound("Found a key on your clipboard.");
      detect(t);
    } catch {
      if (!quiet) setFound("Paste it with Ctrl+V (or ⌘V) instead.");
    }
  }, [expect, onChange, detect]);
  useEffect(() => {
    const onFocus = () => { if (!value) fromClipboard(true); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [value, fromClipboard]);
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="key-field">
        <input className="input mono" type="password" autoFocus={autoFocus} autoComplete="off" spellCheck={false} value={value}
          placeholder={expect ? `${expect}…` : "Paste your key"} aria-label="API key"
          onChange={(e) => { onChange(e.target.value); setFound(""); detect(e.target.value); }} />
        <button type="button" className="btn sm ghost" onClick={() => fromClipboard(false)}><ClipboardPaste size={14} /> Paste</button>
      </div>
      {found && <div className="muted small">{found}</div>}
    </div>
  );
}

/** Sign in with ChatGPT (OpenAI's own sign-in in the browser), with Codex as the fallback. */
export function ChatGPTCard({ sub, onConnected, cta = "Start chatting" }: { sub: Sub; onConnected: () => Promise<void> | void; cta?: string }) {
  const [state, setState] = useState<{ state: string; url: string } | null>(null);
  const [status, setStatus] = useState<{ status: string; error?: string; email?: string } | null>(null);
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState("");
  const timer = useRef<number>();
  useEffect(() => () => window.clearInterval(timer.current), []);

  const start = async () => {
    setError("");
    setStatus(null);
    try {
      const r = await api<{ url: string; state: string }>("/auth/chatgpt/start", { body: {} });
      setState(r);
      window.open(r.url, "_blank", "noopener");
      window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        const s = await api<{ status: string; error?: string; email?: string }>(`/auth/chatgpt/status/${r.state}`).catch(() => null);
        if (s && s.status !== "pending") {
          window.clearInterval(timer.current);
          setStatus(s);
          setState(null);
        }
      }, 1500);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  const completePasted = async () => {
    const s = await api<{ status: string; error?: string; email?: string }>("/auth/chatgpt/complete", { body: { url: pasted.trim() } })
      .catch((e) => ({ status: "failed", error: (e as Error).message }));
    window.clearInterval(timer.current);
    setStatus(s);
    setState(null);
  };

  return (
    <div className="sub-card on">
      <div className="row">
        <strong className="sub-name">ChatGPT</strong>
        {status?.status === "connected" && <span className="badge ok"><Check size={11} /> Signed in</span>}
        <div className="spacer" />
        <span className="tiny muted">{sub.plans}</span>
      </div>
      <p className="small muted">{sub.detail}</p>
      {error && <div className="alert err">{error}</div>}
      {status?.status === "failed" && <div className="alert err">{status.error || "Sign-in didn't finish. Try again."}</div>}
      {status?.status === "connected" ? (
        <div className="row">
          <span className="row small"><Check size={14} /> Signed in as {status.email || "your ChatGPT account"}</span>
          <div className="spacer" />
          <button className="btn primary" onClick={() => onConnected()}>{cta} <ArrowRight size={16} /></button>
        </div>
      ) : state ? (
        <div className="col" style={{ gap: 8 }}>
          <div className="row small"><Loader2 size={14} className="spin" /> Waiting for you to finish in the browser…
            <div className="spacer" /><a className="small" href={state.url} target="_blank" rel="noreferrer">Open again</a></div>
          <details className="small">
            <summary>Signed in on another device?</summary>
            <div className="row" style={{ marginTop: 8 }}>
              <input className="input mono grow" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="Paste the address the browser ended on (http://127.0.0.1…)" />
              <button className="btn sm" disabled={!pasted.includes("state=")} onClick={completePasted}>Finish</button>
            </div>
          </details>
        </div>
      ) : (
        <div className="row"><button className="btn primary" onClick={start}><UserRound size={15} /> Sign in with ChatGPT</button></div>
      )}
      <details className="small"><summary>Didn't work? Use ChatGPT through Codex instead</summary>
        <PlanCard agent="codex" title="ChatGPT through Codex" onConnected={onConnected} cta={cta}
          blurb="OpenAI's own Codex app, signed in with your ChatGPT plan. A bit slower than the direct sign-in above." />
      </details>
    </div>
  );
}

/** Settings: the plans you can sign in with, then a key paste box. Calls onChange after anything connects. */
export function SignInWithPlan({ onChange }: { onChange: () => void }) {
  const [subs, setSubs] = useState<Sub[] | null>(null);
  const [done, setDone] = useState("");
  useEffect(() => { api<{ options: Sub[] }>("/auth/subscriptions").then((r) => setSubs(r.options)).catch(() => setSubs([])); }, []);
  const chatgpt = subs?.find((s) => s.id === "chatgpt");
  const claude = subs?.find((s) => s.id === "claude" && s.available && s.via === "claude_code");
  const connected = (name: string) => async () => { setDone(name); onChange(); };
  if (subs === null) return <Loader2 size={16} className="spin" />;
  return (
    <div className="col" style={{ gap: 0 }}>
      {done && <div className="alert ok row"><Check size={14} /> {done} is connected. It now shows up in every model menu.</div>}
      {chatgpt && <ChatGPTCard sub={chatgpt} onConnected={connected("ChatGPT")} cta="Use it" />}
      {claude && <PlanCard agent="claude" title="Claude" onConnected={connected("Claude")} cta="Use it"
        blurb={`For Claude ${claude.plans}. Uses Claude Code, Anthropic's own app: we set it up and open Anthropic's sign-in. Answers count against your plan.`} />}
      {!chatgpt && !claude && <div className="muted small">No plan sign-in is available in this build. Use a key below.</div>}
    </div>
  );
}

/** Settings: pick a provider (or just paste: it is recognised), open its key page, paste, checked before it is saved. */
export function AddKey({ presets, info, onChange }: { presets: Record<string, Preset>; info: Record<string, KeyInfo>; onChange: () => void }) {
  const choices = KEY_ORDER.filter((k) => k !== "compatible" && presets[k]).map((k) => [k, presets[k]] as const);
  const [kind, setKind] = useState("openai");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState("");
  const preset = presets[kind];
  const onDetected = useCallback((p: string | null) => { if (p && presets[p]) setKind(p); }, [presets]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setAdded("");
    try {
      const r = await api<Validation>("/providers/validate", { body: { api_key: key.trim(), preset: kind, save: true } });
      if (!r.ok) throw new Error(`${r.name} didn't accept that key (${r.detail}). Copy it again from the key page.`);
      setAdded(r.name);
      setKey("");
      onChange();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!choices.length) return null;
  return (
    <form className="col" style={{ gap: 10 }} onSubmit={submit}>
      <div className="welcome-providers" role="radiogroup" aria-label="Provider">
        {choices.map(([k, p]) => (
          <button type="button" key={k} role="radio" aria-checked={k === kind} className={`welcome-choice ${k === kind ? "on" : ""}`}
            onClick={() => { setKind(k); setError(""); }}>
            {p.name}{info[k]?.free ? " (free)" : ""}
          </button>
        ))}
      </div>
      {preset?.note && <div className="muted small">{preset.note}</div>}
      {error && <div className="alert err">{error}</div>}
      {added && <div className="alert ok row"><Check size={14} /> {added} is connected.</div>}
      <div className="row wrap" style={{ alignItems: "flex-start" }}>
        <div className="grow" style={{ minWidth: 260 }}><KeyField value={key} onChange={setKey} expect={info[kind]?.prefix} onDetected={onDetected} /></div>
        {info[kind]?.url && <a className="btn" href={info[kind].url} target="_blank" rel="noreferrer">Get {/^[AEIOU]/i.test(preset.name) ? "an" : "a"} {preset.name} key <ExternalLink size={12} /></a>}
        <button className="btn primary" disabled={busy || key.trim().length < 8}>{busy ? <><Loader2 size={15} className="spin" /> Checking…</> : "Connect"}</button>
      </div>
      <div className="row muted small"><ShieldCheck size={14} /> The key is tested before it is saved, stays on this computer, and only goes to {preset?.name}.</div>
    </form>
  );
}

type SearchStatus = { provider: string; name: string; signup: Record<string, string> };

/** Web search for Research and Chat: a free Tavily key, guided (open the page, copy, paste; tested before saving). */
export function WebSearchSetup({ compact = false, onChange }: { compact?: boolean; onChange?: () => void }) {
  const [st, setSt] = useState<SearchStatus | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  const [change, setChange] = useState(false);
  const load = () => api<SearchStatus>("/assistant/search").then(setSt).catch(() => setSt({ provider: "", name: "", signup: {} }));
  useEffect(() => { load(); }, []);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/assistant/search-key", { body: { provider: "tavily", key: key.trim() } });
      setKey("");
      setChange(false);
      await load();
      onChange?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!st) return <Loader2 size={16} className="spin" />;
  if (st.provider && !change) {
    return (
      <div className="row small">
        <Check size={14} /> <span className="grow">Web search is on ({st.name}).</span>
        {!compact && <button className="btn sm ghost" onClick={() => setChange(true)}>Change key</button>}
      </div>
    );
  }
  return (
    <form className="col" style={{ gap: 10 }} onSubmit={save}>
      <div className="small">{compact ? "Web search is off. " : ""}Turn it on with a free Tavily key: 1,000 searches a month, no card, about a minute.</div>
      <ol className="free-steps">
        <li className={opened ? "done" : ""}>
          <span className="free-n">1</span>
          <span className="grow">Open Tavily and sign up (Google sign-in works). Your key is on the first page.</span>
          <a className="btn sm" href={st.signup.tavily || "https://app.tavily.com/home"} target="_blank" rel="noreferrer" onClick={() => setOpened(true)}>Open Tavily <ExternalLink size={12} /></a>
        </li>
        <li>
          <span className="free-n">2</span>
          <span className="grow col" style={{ gap: 8 }}>
            <span>Copy the key (it starts with tvly-), come back and paste it.</span>
            <KeyField value={key} onChange={setKey} expect="tvly-" />
          </span>
        </li>
      </ol>
      {error && <div className="alert err">{error}</div>}
      <div className="row">
        <button className="btn primary" disabled={busy || key.trim().length < 8}>{busy ? <><Loader2 size={15} className="spin" /> Testing…</> : "Turn on web search"}</button>
        {change && <button type="button" className="btn ghost" onClick={() => setChange(false)}>Cancel</button>}
      </div>
    </form>
  );
}
