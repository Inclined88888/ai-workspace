import { Check, Cloud, Cpu, Download, Gem, Loader2, Server, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api, stream } from "../api";
import { useToast } from "../util";
import { ConnectProvider, ModelsStatus, Report } from "./Models";

/* Choosing how ROOKS uses AI. Free Cloud AI is the first-run default: only models verified as free are used, with no
   paid fallback. Nothing here installs a local engine, downloads a model or benchmarks hardware unless you choose
   Local AI and agree to a specific download after seeing its size, memory and licence. */

export type Mode = "free" | "premium" | "local" | "custom";
type ModeState = { mode: "" | Mode; monthly_limit_cents: number; providers: number };

export const MODES: { id: Mode; title: string; tag?: string; icon: typeof Cloud; about: string }[] = [
  { id: "free", title: "Free Cloud AI", tag: "Recommended", icon: Cloud,
    about: "Connect a free account with OpenRouter or Google Gemini. No download and no graphics card needed. Only models verified as free are used, within the provider's daily limits." },
  { id: "premium", title: "Premium Cloud AI", icon: Gem,
    about: "Use paid models such as Claude or GPT with your own key. You set a monthly spending limit first." },
  { id: "local", title: "Local AI", tag: "Optional", icon: Cpu,
    about: "Run models on this computer with Ollama or llama.cpp. Private, but needs memory, disk space and ideally a graphics card." },
  { id: "custom", title: "Custom AI Server", icon: Server,
    about: "Connect an existing OpenAI compatible endpoint, such as a company gateway or LM Studio." },
];

export function ModeChooser({ value, onChange, idPrefix = "mode" }: { value: string; onChange: (m: Mode) => void; idPrefix?: string }) {
  return (
    <div className="col" role="radiogroup" aria-label="How ROOKS uses AI">
      {MODES.map((m, i) => (
        <button key={m.id} type="button" className="choice" role="radio" aria-checked={value === m.id} id={`${idPrefix}-${m.id}`}
          tabIndex={value === m.id || (!value && i === 0) ? 0 : -1}
          onClick={() => onChange(m.id)}
          onKeyDown={(e) => {
            const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
            if (!d) return;
            e.preventDefault();
            const next = MODES[(i + d + MODES.length) % MODES.length];
            onChange(next.id);
            document.getElementById(`${idPrefix}-${next.id}`)?.focus();
          }}>
          <m.icon size={20} className="prio-1" aria-hidden="true" style={{ flexShrink: 0 }} />
          <span><strong>{m.title}</strong>{m.tag && <> <span className={`badge ${m.tag === "Recommended" ? "ok" : ""}`}>{m.tag}</span></>}<br />
            <span className="muted small">{m.about}</span></span>
        </button>
      ))}
    </div>
  );
}

/** What leaves this server, said plainly for each mode. */
export function PrivacyNote({ mode }: { mode: Mode }) {
  if (mode === "local") {
    return <div className="onboard-note small"><strong>Privacy.</strong> With Local AI your messages, notes and files stay on this computer or your network. ROOKS refuses cloud models in this mode, including as backups.</div>;
  }
  return (
    <details className="onboard-note small">
      <summary><strong>What is sent to the AI provider</strong></summary>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        <li>Your message, the conversation so far, and whatever the assistant reads to answer it: notes, tasks, calendar entries, file contents, web pages and saved memories that are relevant.</li>
        <li>Your API key, so the provider knows the account. Your ROOKS password is never sent.</li>
        <li>Local only projects are never sent to a cloud model.</li>
        {mode === "free" && <li><strong>OpenRouter</strong> passes prompts to the company hosting each model, and each has its own retention policy. Some may train on prompts; OpenRouter's privacy settings let you allow or block those, separately for free and paid models.</li>}
        {mode === "free" && <li><strong>Google Gemini</strong>: Google says free tier content is used to improve its products. Don't send anything through it that you need kept private.</li>}
        {mode !== "free" && <li>Each provider's own terms say what it keeps and for how long. Check them before sending sensitive work.</li>}
      </ul>
    </details>
  );
}

function FreeFacts() {
  return (
    <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
      <li>Free is not unlimited. Free OpenRouter models allow 20 requests a minute and 50 a day (1,000 a day once you have bought $10 of credits). Gemini's free tier has its own limits per model.</li>
      <li>ROOKS only uses a model it can show is free: OpenRouter's own price list must say $0, and a Gemini key must come from a project with no billing account, which you confirm.</li>
      <li>There is no paid backup in this mode. When the free limit is reached, the assistant says so and waits.</li>
      <li>One assistant answer can take several requests when it uses tools.</li>
    </ul>
  );
}

function PremiumLimit({ cents, onSaved }: { cents: number; onSaved: (c: number) => void }) {
  const [dollars, setDollars] = useState(cents > 0 ? String(cents / 100) : "10");
  const valid = Number(dollars) > 0 && Number(dollars) <= 100000;
  return (
    <div className="col" style={{ gap: 6 }}>
      <label htmlFor="premium-limit" className="field">Monthly spending limit (US dollars)
        <span className="hint">Required. AI requests stop when this month's known spend reaches it.</span></label>
      <div className="row">
        <input id="premium-limit" className="input" style={{ maxWidth: 140 }} inputMode="decimal" value={dollars} aria-invalid={!valid || undefined}
          onChange={(e) => setDollars(e.target.value.replace(/[^\d.]/g, ""))} />
        <button type="button" className="btn" disabled={!valid} onClick={() => onSaved(Math.round(Number(dollars) * 100))}>Set limit</button>
      </div>
      <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
        <li>OpenRouter reports the real cost of every request, so its spend counts exactly.</li>
        <li>Anthropic, OpenAI and other providers do not report cost. Enter their prices under Usage and budgets, or their spend counts as $0 and the limit cannot stop them.</li>
        <li>The limit is checked before each request, so a month can go over by at most one request.</li>
      </ul>
    </div>
  );
}

type Cat = { model: string; size_gb: number; memory_gb: number; licence: string; licence_url: string; note: string };

/** Optional downloads for Ollama: nothing starts until you agree to one model's size, memory and licence. */
export function LocalDownloads({ onDone }: { onDone?: () => void }) {
  const [cat, setCat] = useState<Cat[] | null>(null);
  const [configured, setConfigured] = useState(false);
  const [have, setHave] = useState<string[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [chosen, setChosen] = useState("");
  const [other, setOther] = useState("");
  const [agree, setAgree] = useState(false);
  const [pull, setPull] = useState<{ model: string; pct: number; status: string } | null>(null);
  const toast = useToast();
  const status = () => api<{ online: boolean; models: string[] }>("/ollama/status").then((s) => { setOnline(s.online); setHave(s.models); }).catch(() => setOnline(false));
  useEffect(() => {
    api<{ models: Cat[]; ollama_configured: boolean }>("/local/catalog").then((r) => { setCat(r.models); setConfigured(r.ollama_configured); }).catch(() => setCat([]));
    status();
  }, []);
  const target = chosen === "other" ? other.trim() : chosen;
  const item = cat?.find((c) => c.model === chosen);
  const start = async () => {
    setPull({ model: target, pct: 0, status: "starting" });
    await stream("/ollama/pull", { model: target, consent: true }, (ev) => {
      if (ev.error) toast.err(String(ev.error));
      const total = Number(ev.total ?? 0);
      setPull({ model: target, pct: total ? Math.round((Number(ev.completed ?? 0) / total) * 100) : 0, status: String(ev.status ?? "") });
    });
    setPull(null); setAgree(false); setChosen(""); status(); onDone?.();
  };
  if (!cat) return <Loader2 size={14} className="spin" aria-label="Loading" />;
  if (!configured) {
    return <p className="muted small" style={{ margin: 0 }}>Ollama is not set up on this server. On a Mac, install the Ollama app and run setup again; on a server, run <span className="mono">./setup.sh --with local-models</span>. ROOKS does not install it for you.</p>;
  }
  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="row wrap small">{online === null ? "Checking Ollama…" : online ? <span className="badge ok">Ollama is running</span> : <span className="badge danger">Ollama is not answering</span>}
        {have.length > 0 && <span className="muted">Already downloaded: {have.join(", ")}</span>}</div>
      <fieldset className="col" style={{ gap: 6, border: 0, padding: 0, margin: 0 }}>
        <legend className="field">Choose a model</legend>
        {cat.map((c) => {
          const got = have.some((m) => m === c.model || m === `${c.model}:latest`);
          return (
            <label key={c.model} className="row small" style={{ gap: 8, alignItems: "flex-start" }}>
              <input type="radio" name="dl" value={c.model} checked={chosen === c.model} disabled={got || !!pull} onChange={() => { setChosen(c.model); setAgree(false); }} />
              <span><span className="mono">{c.model}</span> {got && <span className="badge ok"><Check size={11} /> downloaded</span>}<br />
                <span className="muted">{c.size_gb} GB download · needs about {c.memory_gb} GB of free memory · {c.licence}. {c.note}</span></span>
            </label>
          );
        })}
        <label className="row small" style={{ gap: 8, alignItems: "flex-start" }}>
          <input type="radio" name="dl" value="other" checked={chosen === "other"} disabled={!!pull} onChange={() => { setChosen("other"); setAgree(false); }} />
          <span>Another model from ollama.com/library
            {chosen === "other" && <input className="input mono" aria-label="Model name" style={{ marginTop: 4 }} value={other} onChange={(e) => setOther(e.target.value)} placeholder="e.g. llama3.2:3b" />}</span>
        </label>
      </fieldset>
      {chosen && (
        <div className="card col" style={{ gap: 8 }}>
          {item ? (
            <p className="small" style={{ margin: 0 }}>Downloading <span className="mono">{item.model}</span> uses <strong>{item.size_gb} GB</strong> of disk and needs about <strong>{item.memory_gb} GB</strong> of free memory to run.
              Its licence is <a href={item.licence_url} target="_blank" rel="noreferrer">{item.licence}</a>. Memory is a rough guide, not measured on this computer.</p>
          ) : (
            <p className="small" style={{ margin: 0 }}>ROOKS does not know this model's size, memory needs or licence. Check its page on <a href="https://ollama.com/library" target="_blank" rel="noreferrer">ollama.com/library</a> first.</p>
          )}
          <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} /> I have read the size, memory and licence, and I agree to this download.</label>
          <div><button type="button" className="btn" disabled={!agree || !target || !!pull || !online} onClick={start}><Download size={14} /> Download {target}</button></div>
        </div>
      )}
      {pull && <div className="col" aria-live="polite"><div className="progress" role="progressbar" aria-label={`Downloading ${pull.model}`} aria-valuenow={pull.pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${pull.pct}%` }} /></div><div className="muted small">{pull.model}: {pull.status} {pull.pct ? `${pull.pct}%` : ""}</div></div>}
      {toast.node}
    </div>
  );
}

/** The setup for one mode: what it means, its privacy, connecting a provider and testing it. */
export function ModeSetup({ mode, onConnected, limitCents, onLimit }: { mode: Mode; onConnected?: (s: ModelsStatus, r: Report) => void;
  limitCents: number; onLimit: (c: number) => void }) {
  const [localOnline, setLocalOnline] = useState(false);
  useEffect(() => { if (mode === "local") api<{ online: boolean }>("/ollama/status").then((s) => setLocalOnline(s.online)).catch(() => setLocalOnline(false)); }, [mode]);
  const done = onConnected ?? (() => undefined);
  return (
    <div className="col gap-lg">
      {mode === "free" && (
        <>
          <FreeFacts />
          <ConnectProvider presets={["openrouter", "gemini"]} mode="free" onConnected={done} />
        </>
      )}
      {mode === "premium" && (
        <>
          <PremiumLimit cents={limitCents} onSaved={onLimit} />
          {limitCents > 0 && <ConnectProvider presets={["anthropic", "openai", "openrouter", "opencode-go"]} start="anthropic" mode="premium" onConnected={done} />}
        </>
      )}
      {mode === "local" && (
        <>
          <ConnectProvider key={String(localOnline)} presets={["ollama", "llamacpp", "custom"]} localAvailable={localOnline} start={localOnline ? "ollama" : "llamacpp"} mode="local" onConnected={done} />
          <details className="card col">
            <summary className="field" style={{ cursor: "pointer" }}>Download a model for Ollama (optional)</summary>
            <div style={{ marginTop: 10 }}><LocalDownloads /></div>
          </details>
          <div className="small muted"><strong>AirLLM</strong> is not supported: it is a Python library with no server ROOKS can connect to, and it cannot call tools, which the assistant needs.</div>
        </>
      )}
      {mode === "custom" && (
        <>
          <p className="small muted" style={{ margin: 0 }}>Any server that speaks the OpenAI chat API. ROOKS cannot see its prices: if it can bill you, set a monthly limit under Usage and budgets and enter its prices there.</p>
          <ConnectProvider presets={["custom"]} start="custom" mode="custom" onConnected={done} />
        </>
      )}
      <PrivacyNote mode={mode} />
    </div>
  );
}

export async function saveMode(mode: Mode, monthly_limit_cents?: number) {
  return api<ModeState & { status: ModelsStatus }>("/ai-mode", { method: "PUT", body: monthly_limit_cents === undefined ? { mode } : { mode, monthly_limit_cents } });
}

/** Settings: the current AI mode and switching it. Providers, keys and your saved default are kept when you switch. */
export function AiModePanel({ onChanged }: { onChanged: () => void }) {
  const [st, setSt] = useState<ModeState | null>(null);
  const [pick, setPick] = useState<Mode | "">("");
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const toast = useToast();
  const load = () => api<ModeState>("/ai-mode").then((s) => { setSt(s); setPick(s.mode); }).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const apply = async (m: Mode, cents?: number) => {
    setBusy(true);
    try { const r = await saveMode(m, cents); setSt(r); setChanging(false); toast.ok(`AI mode: ${MODES.find((x) => x.id === m)?.title}`); onChanged(); }
    catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  if (!st) return <div className="card"><Loader2 size={16} className="spin" aria-label="Loading" /></div>;
  const current = MODES.find((m) => m.id === st.mode);
  return (
    <div className="card col gap-lg" id="ai-mode">
      <div className="row"><ShieldCheck size={16} aria-hidden="true" /><h3 className="grow">AI mode</h3>
        {current ? <span className="badge ok">{current.title}</span> : <span className="badge">Not chosen</span>}</div>
      {!st.mode && <div className="alert" role="status">You set ROOKS up before AI modes existed, so nothing has changed: your providers and default model work exactly as before. Choosing a mode is optional.</div>}
      {current && !changing ? (
        <div className="setting-row">
          <div className="muted small">{current.about}{st.mode === "premium" && st.monthly_limit_cents > 0 ? ` Monthly limit: $${(st.monthly_limit_cents / 100).toFixed(2)}.` : ""}</div>
          <button type="button" className="btn sm" onClick={() => setChanging(true)} aria-expanded={false}>Change mode</button>
        </div>
      ) : <>
      <ModeChooser value={pick} onChange={setPick} idPrefix="set-mode" />
      {current && <div><button type="button" className="btn ghost sm" onClick={() => { setChanging(false); setPick(st.mode); }}>Keep {current.title}</button></div>}
      </>}
      {(changing || !current) && pick === "premium" && pick !== st.mode && <PremiumLimit cents={st.monthly_limit_cents} onSaved={(c) => apply("premium", c)} />}
      {(changing || !current) && pick && pick !== "premium" && pick !== st.mode && (
        <div className="row wrap">
          <button type="button" className="btn primary" disabled={busy} onClick={() => apply(pick)}>{busy ? <Loader2 size={14} className="spin" /> : null} Use {MODES.find((m) => m.id === pick)?.title}</button>
          <span className="small muted">Your providers, keys and saved default model are kept.{pick === "free" ? " Only verified free models will be used; ROOKS checks your providers with free requests only." : ""}</span>
        </div>
      )}
      {pick && <PrivacyNote mode={pick} />}
      {toast.node}
    </div>
  );
}
