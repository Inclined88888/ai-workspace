import { Check, Cpu, Search, Download, FolderOpen, KeyRound, Loader2, Monitor, Plug, Plus, RefreshCw, Shield, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, stream } from "../api";
import { AppInfo, Sidecar, appInfo, inDesktopShell, listSidecars, openDataDir, openLogs, requestApproval } from "../desktop";
import { ModelSelect, useModels, useToast } from "../util";
import ConnectMailCalendar from "./ConnectMailCalendar";
import { AddKey, KeyInfo, SignInWithPlan, WebSearchSetup } from "./ModelSignIn";
import { PhoneCard } from "./Phone";

type DesktopInfo = { desktop: boolean; data_dir: string; secret_backend: string };

/** Desktop app only: where data lives, how keys are protected, and the helper processes (sidecars). */
function DesktopApp({ info }: { info: DesktopInfo }) {
  const [app, setApp] = useState<AppInfo | null>(null);
  const [sidecars, setSidecars] = useState<Sidecar[]>([]);
  const [answer, setAnswer] = useState("");
  const shell = inDesktopShell();
  useEffect(() => {
    if (!shell) return;
    appInfo().then(setApp).catch(() => undefined);
    listSidecars().then(setSidecars).catch(() => undefined);
  }, [shell]);
  const test = async () => setAnswer((await requestApproval("Test approval", "This is what an approval prompt looks like. Nothing will happen either way.", "low")) ? "Approved" : "Denied");
  return (
    <div className="card col gap-lg">
      <div className="row"><Monitor size={16} /><h2>Desktop app</h2>{app && <span className="badge">v{app.version} · {app.platform} {app.arch}</span>}</div>
      <div className="col small">
        <div>Your data: <span className="mono">{info.data_dir}</span></div>
        <div>API keys: {info.secret_backend === "os-keychain" ? "stored in this computer's keychain" : "encrypted in your data folder (no system keychain available)"}</div>
        {app && <div>Updates: {app.updater ? "automatic" : "manual for now (download new versions from the releases page)"}</div>}
      </div>
      {sidecars.length > 0 && (
        <div className="card flush">
          {sidecars.map((sc) => (
            <div key={sc.name} className="list-item">
              <div className="grow"><div><strong>{sc.name}</strong></div><div className="muted small">{sc.description}</div></div>
              <span className={`badge ${sc.status === "running" ? "ok" : sc.status === "failed" ? "danger" : ""}`}>{sc.status}</span>
            </div>
          ))}
        </div>
      )}
      {shell && (
        <div className="row wrap">
          <button className="btn" onClick={() => openDataDir()}><FolderOpen size={14} /> Open data folder</button>
          <button className="btn" onClick={() => openLogs()}><FolderOpen size={14} /> Open logs</button>
          <button className="btn ghost" onClick={test}>Test approval prompt</button>
          {answer && <span className="badge">{answer}</span>}
        </div>
      )}
    </div>
  );
}

type Provider = { id: number; name: string; auth_kind: string; protocol: string; base_url: string; models: string; enabled: number;
  has_key: boolean; key_hint: string; state: string; last_error: string };
type Presets = Record<string, { name: string; auth_kind: string; protocol: string; base_url: string; note?: string }>;

// The connection types for a custom connection (Advanced). Plans are signed in to with their own cards above it.
const CONNECTION_TYPES: Record<string, string> = {
  "api_key/chat_completions": "API key · Chat Completions",
  "api_key/responses": "API key · OpenAI Responses",
  "api_key/anthropic_messages": "API key · Anthropic Messages",
  "openai_compatible/chat_completions": "OpenAI compatible server (key optional)",
  "local_ollama/ollama_native": "Ollama (local)",
};
const KIND_LABEL: Record<string, string> = {
  api_key: "API key", cli_subscription: "Your plan", oauth: "Your plan", chatgpt: "Your plan", subscription: "Your plan",
  local_ollama: "On this computer", openai_compatible: "Custom server",
};
const kindLabel = (p: { auth_kind: string; protocol: string }) =>
  KIND_LABEL[p.auth_kind] ?? CONNECTION_TYPES[`${p.auth_kind}/${p.protocol}`] ?? p.auth_kind;
const EMPTY_FORM = { name: "", type: "openai_compatible/chat_completions", base_url: "", api_key: "", models: "" };
export function Providers({ onChange }: { onChange?: () => void } = {}) {
  const [list, setList] = useState<Provider[] | null>(null);
  const [presets, setPresets] = useState<Presets>({});
  const [info, setInfo] = useState<Record<string, KeyInfo>>({});
  const [form, setForm] = useState(EMPTY_FORM);
  const [note, setNote] = useState("");
  const toast = useToast();
  const load = () => api<{ providers: Provider[]; presets: Presets; key_info?: Record<string, KeyInfo> }>("/providers")
    .then((r) => { setList(r.providers); setPresets(r.presets); setInfo(r.key_info ?? {}); onChange?.(); }).catch(toast.err);
  useEffect(() => { load(); }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const [auth_kind, protocol] = form.type.split("/");
      await api("/providers", { body: { name: form.name, auth_kind, protocol, base_url: form.base_url, api_key: form.api_key, models: form.models } });
      setForm(EMPTY_FORM);
      setNote("");
      load();
      toast.ok("Added");
    } catch (err) { toast.err(err); }
  };
  const toggle = async (p: Provider) => {
    await api(`/providers/${p.id}`, { method: "PUT", body: { name: p.name, auth_kind: p.auth_kind, protocol: p.protocol, base_url: p.base_url, models: p.models, enabled: !p.enabled } });
    load();
  };
  const remove = async (p: Provider) => {
    if (!confirm(`Disconnect ${p.name}? Its saved key or sign-in is removed from this computer.`)) return;
    await api(`/providers/${p.id}`, { method: "DELETE" });
    load();
  };
  const usePreset = (k: string) => {
    const p = presets[k];
    if (p) setForm({ ...form, name: p.name, base_url: p.base_url, type: `${p.auth_kind}/${p.protocol}` });
    setNote(p?.note ?? "");
  };
  const advancedPresets = Object.keys(presets).filter((k) => presets[k].auth_kind !== "cli_subscription");

  return (
    <div className="card col gap-lg" id="models">
      <div className="row"><KeyRound size={16} /><h2>Connect</h2></div>
      {list === null ? <Loader2 size={16} className="spin" /> : list.length > 0 ? (
        <div className="card flush">
          {list.map((p) => (
            <div key={p.id} className="list-item" style={{ cursor: "default" }} title={p.base_url}>
              <div className="grow">
                <div className="row" style={{ gap: 8 }}>
                  <strong>{p.name}</strong>
                  <span className="badge">{kindLabel(p)}</span>
                  {!p.enabled ? <span className="badge">Off</span>
                    : p.state === "connected" ? <span className="badge ok"><Check size={11} /> Connected</span>
                    : <span className="badge">{p.state === "needs_auth" ? "Needs sign-in" : p.state}</span>}
                </div>
                {p.last_error && <div className="small" style={{ color: "var(--danger)" }}>{p.last_error}</div>}
              </div>
              <button className="btn sm ghost" onClick={() => toggle(p)}>{p.enabled ? "Turn off" : "Turn on"}</button>
              <button className="btn ghost sm icon" title="Disconnect" aria-label={`Disconnect ${p.name}`} onClick={() => remove(p)}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      ) : <div className="alert">Nothing connected yet. Sign in with a plan or paste a key below.</div>}

      <div className="col" style={{ gap: 4 }}>
        <h3 style={{ margin: 0 }}>Sign in with a plan you already pay for</h3>
        <div className="muted small">No key needed. Answers count against your plan.</div>
        <SignInWithPlan onChange={load} />
      </div>

      <div className="col" style={{ gap: 8 }}>
        <h3 style={{ margin: 0 }}>Or paste a key</h3>
        <div className="muted small">The ones marked (free) cost nothing. Paste any key and the provider is recognised from it.</div>
        <AddKey presets={presets} info={info} onChange={load} />
      </div>

      <details>
        <summary className="small">Advanced: a custom server, Ollama, or exact model ids</summary>
        <form className="col" style={{ marginTop: 12 }} onSubmit={add}>
          <div className="row wrap">
            <span className="small muted">Start from:</span>
            {advancedPresets.map((k) => <button type="button" key={k} className="btn sm" onClick={() => usePreset(k)}>{presets[k].name}</button>)}
          </div>
          {note && <div className="muted small">{note}</div>}
          <div className="row wrap">
            <input className="input" style={{ width: 180 }} required placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <select className="input" style={{ width: 250 }} value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })} aria-label="Connection type">
              {Object.entries(CONNECTION_TYPES).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
            </select>
            <input className="input grow" style={{ minWidth: 220 }} required placeholder="Base URL, e.g. https://api.groq.com/openai/v1" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
          </div>
          <div className="row wrap">
            <input className="input grow" type="password" placeholder="API key" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            <input className="input grow" placeholder="Models (optional, comma separated)" value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} />
            <button className="btn primary"><Plus size={15} /> Add</button>
          </div>
          <div className="muted small">Any OpenAI compatible server works (LM Studio, vLLM, LiteLLM, Together, Mistral). If it has no model list, type the model ids.</div>
        </form>
      </details>
      {toast.node}
    </div>
  );
}

function LocalModels() {
  const [status, setStatus] = useState<{ online: boolean; models: string[] } | null>(null);
  const [name, setName] = useState("");
  const [pull, setPull] = useState<{ model: string; pct: number; status: string } | null>(null);
  const toast = useToast();
  const load = () => api<{ online: boolean; models: string[] }>("/ollama/status").then(setStatus);
  useEffect(() => { load(); }, []);
  const start = async (e: FormEvent) => {
    e.preventDefault();
    const model = name.trim();
    setPull({ model, pct: 0, status: "starting" });
    await stream("/ollama/pull", { model }, (ev) => {
      if (ev.error) toast.err(String(ev.error));
      const total = Number(ev.total ?? 0);
      setPull({ model, pct: total ? Math.round((Number(ev.completed ?? 0) / total) * 100) : 0, status: String(ev.status ?? "") });
    });
    setPull(null);
    setName("");
    load();
  };
  return (
    <div className="card col gap-lg">
      <div className="row"><Cpu size={16} /><h2>AI on this computer (offline)</h2><div className="spacer" />{status && (status.online ? <span className="badge ok">Ollama is running</span> : <span className="badge">Ollama not running</span>)}<button className="btn ghost sm icon" title="Check again" onClick={load}><RefreshCw size={13} /></button></div>
      <div className="muted small">Runs a model on your own computer: private and free, but slower and less capable than the cloud. It needs the free Ollama app{status && !status.online ? <>: <a href="https://ollama.com/download" target="_blank" rel="noreferrer">download Ollama</a>, open it, then press the refresh button.</> : "."}</div>
      <div className="row wrap">{status?.models.map((m) => <span key={m} className="badge mono">{m}</span>)}{status?.models.length === 0 && <span className="muted small">No models downloaded yet.</span>}</div>
      <form className="row" onSubmit={start}>
        <input className="input grow mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="Model name, e.g. qwen3:8b (good start), gemma3:12b, llama3.2:3b" />
        <button className="btn" disabled={!name.trim() || !!pull}>{pull ? <Loader2 size={15} className="spin" /> : <Download size={15} />} Download</button>
      </form>
      {pull && <div className="col"><div className="progress"><div style={{ width: `${pull.pct}%` }} /></div><div className="muted small">{pull.model}: {pull.status} {pull.pct ? `${pull.pct}%` : ""}</div></div>}
      <div className="muted small">Models downloaded here are shared with Chat. Rough guide: a 4B model needs ~4 GB RAM, 8B ~8 GB, 14B ~12 GB. A GPU makes them much faster.</div>
      {toast.node}
    </div>
  );
}

type UsageSummary = { month_cents: number; month_tokens: number; prices: Record<string, { in: number; out: number }>;
  by_model: { model: string; tokens: number; cents: number; unpriced_calls: number }[]; by_channel: { channel: string; cents: number; tokens: number }[] };

function Usage() {
  const [u, setU] = useState<UsageSummary | null>(null);
  const [row, setRow] = useState({ model: "", in: "", out: "" });
  const toast = useToast();
  const load = () => api<UsageSummary>("/usage").then(setU).catch(toast.err);
  useEffect(() => { load(); }, []);
  const savePrices = async (prices: UsageSummary["prices"]) => {
    try { setU(await api<UsageSummary>("/usage/prices", { method: "PUT", body: { prices } })); toast.ok("Prices saved"); } catch (e) { toast.err(e); }
  };
  if (!u) return null;
  return (
    <div className="card col gap-lg">
      <div className="row"><h2 className="grow">Usage this month</h2><span className="badge">${(u.month_cents / 100).toFixed(2)} · {u.month_tokens.toLocaleString()} tokens</span></div>
      <div className="muted small">Everything the AI did this month, by model. Free tiers and plans cost nothing extra here. A paid key shows $0 until you set its price (from the provider's pricing page).</div>
      <div className="card flush">
        {u.by_model.length === 0 && <div className="list-item muted">No usage yet.</div>}
        {u.by_model.map((m) => (
          <div key={m.model} className="list-item" style={{ cursor: "default" }}>
            <span className="mono grow truncate">{m.model}</span>
            <span className="small">{Number(m.tokens).toLocaleString()} tokens</span>
            <span className="small">${(m.cents / 100).toFixed(2)}</span>
            {m.unpriced_calls > 0 && !u.prices[m.model] && <button className="btn sm" onClick={() => setRow({ model: m.model, in: "", out: "" })}>Set price</button>}
          </div>
        ))}
      </div>
      <div className="row wrap">
        <input className="input mono grow" placeholder="model id" value={row.model} onChange={(e) => setRow({ ...row, model: e.target.value })} />
        <input className="input" style={{ width: 130 }} type="number" min={0} step="any" placeholder="$ in / 1M" value={row.in} onChange={(e) => setRow({ ...row, in: e.target.value })} />
        <input className="input" style={{ width: 130 }} type="number" min={0} step="any" placeholder="$ out / 1M" value={row.out} onChange={(e) => setRow({ ...row, out: e.target.value })} />
        <button className="btn" disabled={!row.model || row.in === "" || row.out === ""} onClick={() => { savePrices({ ...u.prices, [row.model]: { in: Number(row.in), out: Number(row.out) } }); setRow({ model: "", in: "", out: "" }); }}>Save price</button>
      </div>
      {Object.entries(u.prices).map(([m, p]) => (
        <div key={m} className="row small"><span className="mono grow">{m}</span><span>${p.in} in · ${p.out} out per 1M</span>
          <button className="btn ghost sm icon" onClick={() => { const n = { ...u.prices }; delete n[m]; savePrices(n); }}><Trash2 size={12} /></button></div>
      ))}
      {toast.node}
    </div>
  );
}

export default function Settings({ onChanged }: { onChanged: () => void }) {
  const { models, reload } = useModels();
  const [s, setS] = useState({ workspace_name: "", default_model: "", chat_url: "", tools_url: "", model_catalog_refresh: false, model_catalog_env: false });
  const [pw, setPw] = useState({ current: "", new: "" });
  const toast = useToast();
  const [desktop, setDesktop] = useState<DesktopInfo | null>(null);
  useEffect(() => { api<typeof s>("/settings").then(setS); }, []);
  useEffect(() => { api<DesktopInfo>("/desktop/info").then(setDesktop).catch(() => setDesktop(null)); }, []);

  const save = async () => {
    try { setS(await api("/settings", { method: "PUT", body: { workspace_name: s.workspace_name, default_model: s.default_model } })); toast.ok("Saved"); onChanged(); }
    catch (e) { toast.err(e); }
  };
  const setCatalog = async (on: boolean) => {
    try { setS(await api("/settings", { method: "PUT", body: { model_catalog_refresh: on } })); toast.ok(on ? "Model prices will update once a day" : "Model price updates are off"); }
    catch (e) { toast.err(e); }
  };
  const changePw = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/password", { body: pw }); setPw({ current: "", new: "" }); toast.ok("Password changed. Other sessions were signed out."); }
    catch (err) { toast.err(err); }
  };

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head"><h1>Settings</h1></div>
      <div className="col gap-lg">
        <div className="card col gap-lg">
          <h2>General</h2>
          <label className="field">Workspace name<input className="input" value={s.workspace_name} onChange={(e) => setS({ ...s, workspace_name: e.target.value })} /></label>
          <label className="field">Default model <span className="hint">Auto picks a fitting model for each job. Pick one to always use it instead.</span>
            <div className="row"><div className="grow"><ModelSelect value={s.default_model} onChange={(v) => setS({ ...s, default_model: v })} models={models} /></div><button className="btn icon" onClick={() => reload(true)} title="Refresh model list"><RefreshCw size={14} /></button></div>
          </label>
          <div><button className="btn primary" onClick={save}>Save</button></div>
        </div>
        <div className="card col" id="models">
          <div className="row"><KeyRound size={16} /><h2 className="grow">AI models</h2><Link className="btn primary" to="/models">Open AI models</Link></div>
          <div className="muted small">Sign in with ChatGPT or Claude, paste a key, and choose which model does what. It all lives on the AI models page.</div>
        </div>
        <div className="card col" id="search">
          <div className="row"><Search size={16} /><h2>Web search</h2></div>
          <div className="muted small">Lets Research and Chat look things up on the web.</div>
          <WebSearchSetup />
        </div>
        <div className="card col" id="model-prices">
          <h2>Model prices and limits</h2>
          <div className="muted small">Costs and context sizes come from a list built into the app. Turn this on to download a newer list from models.dev once a day. It is a plain download: no chats, keys or settings are sent, though models.dev sees your IP address. Off unless you turn it on.</div>
          <label className="row" style={{ gap: 8 }}>
            <input type="checkbox" checked={s.model_catalog_refresh} disabled={s.model_catalog_env} onChange={(e) => setCatalog(e.target.checked)} />
            Update model prices from models.dev daily
          </label>
          {s.model_catalog_env && <div className="muted small">Set by MODEL_CATALOG_REFRESH on the server; change it there.</div>}
        </div>
        <ConnectMailCalendar />
        {desktop?.desktop && <DesktopApp info={desktop} />}
        {/* feat/v02-anywhere-voice */}
        <div className="card col">
          <h2>Ask from anywhere & voice</h2>
          <div className="muted small">The shortcut that opens the ask bar over any app, how I listen when you talk, and how I sound.</div>
          <div><Link className="btn" to="/voice">Open these settings</Link></div>
        </div>
        <PhoneCard />
        <Usage />
        <details className="card col settings-more">
          <summary><strong>More settings</strong> <span className="muted small">offline AI on this computer, other calendars{s.chat_url ? ", Open WebUI" : ""}</span></summary>
          <div className="col gap-lg" style={{ marginTop: 16 }}>
            <LocalModels />
            <div className="card col">
              <h2>Other calendars</h2>
              <div className="muted small">Google and Microsoft calendars come with your mail account. To add another calendar (iCloud, a school or team calendar link), use the Calendar page.</div>
              <div><Link className="btn" to="/calendar">Open calendar</Link></div>
            </div>
            {s.chat_url && <div className="card col">
              <div className="row"><Plug size={16} /><h2>Open WebUI</h2></div>
              <div className="muted small">The optional Open WebUI chat can use your notes, tasks, calendar and mail drafts (it can never send mail). Turn it on per chat with the tools button in its message box. Tool server: <span className="mono">{s.tools_url}</span></div>
              <div><a className="btn" href={s.chat_url} target="_blank" rel="noreferrer">Open WebUI</a></div>
            </div>}
          </div>
        </details>
        <p className="small muted" style={{ margin: 0 }}><Link to="/about">About</Link> · <Link to="/privacy">Privacy</Link></p>
        {!desktop?.desktop && <form className="card col" onSubmit={changePw}>
          <div className="row"><Shield size={16} /><h2>Password</h2></div>
          <div className="row wrap">
            <input className="input grow" type="password" placeholder="Current password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            <input className="input grow" type="password" placeholder="New password (10+ characters)" value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} />
            <button className="btn" disabled={!pw.current || pw.new.length < 10}>Change</button>
          </div>
        </form>}
      </div>
      {toast.node}
    </div>
  );
}
