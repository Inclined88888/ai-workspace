import { Check, Cpu, Download, FolderOpen, KeyRound, Loader2, Mail, Monitor, Plug, Plus, RefreshCw, Shield, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, stream } from "../api";
import { AppInfo, Sidecar, appInfo, inDesktopShell, listSidecars, openDataDir, openLogs, requestApproval } from "../desktop";
import { ModelSelect, useModels, useToast } from "../util";

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

// The connection types a user can create here; subscription sign-in has its own flow (item 101)
const CONNECTION_TYPES: Record<string, string> = {
  "api_key/chat_completions": "API key · Chat Completions",
  "api_key/responses": "API key · OpenAI Responses",
  "api_key/anthropic_messages": "API key · Anthropic Messages",
  "openai_compatible/chat_completions": "OpenAI compatible server (key optional)",
  "local_ollama/ollama_native": "Ollama (local)",
};
const typeLabel = (p: { auth_kind: string; protocol: string }) =>
  CONNECTION_TYPES[`${p.auth_kind}/${p.protocol}`] ?? `${p.auth_kind} · ${p.protocol}`;
const EMPTY_FORM = { name: "", type: "openai_compatible/chat_completions", base_url: "", api_key: "", models: "" };
type MailAccount = { id: number; name: string; email: string; imap_host: string; smtp_host: string };

const MAIL_PRESETS: Record<string, { imap_host: string; smtp_host: string; smtp_port: number; smtp_security: "ssl" | "starttls"; note: string }> = {
  Fastmail: { imap_host: "imap.fastmail.com", smtp_host: "smtp.fastmail.com", smtp_port: 465, smtp_security: "ssl", note: "Use an app password from Settings → Privacy & Security." },
  iCloud: { imap_host: "imap.mail.me.com", smtp_host: "smtp.mail.me.com", smtp_port: 587, smtp_security: "starttls", note: "Use an app specific password from appleid.apple.com. Username is your full iCloud address." },
  Gmail: { imap_host: "imap.gmail.com", smtp_host: "smtp.gmail.com", smtp_port: 465, smtp_security: "ssl", note: "Needs 2 step verification and an app password (myaccount.google.com/apppasswords). Some Workspace admins disable this." },
  Other: { imap_host: "", smtp_host: "", smtp_port: 465, smtp_security: "ssl", note: "Ask your provider for IMAP (port 993, SSL) and SMTP settings." },
};

function Providers() {
  const [list, setList] = useState<Provider[]>([]);
  const [presets, setPresets] = useState<Presets>({});
  const [form, setForm] = useState(EMPTY_FORM);
  const [note, setNote] = useState("");
  const toast = useToast();
  const load = () => api<{ providers: Provider[]; presets: Presets }>("/providers").then((r) => { setList(r.providers); setPresets(r.presets); }).catch(toast.err);
  useEffect(() => { load(); }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const [auth_kind, protocol] = form.type.split("/");
      await api("/providers", { body: { name: form.name, auth_kind, protocol, base_url: form.base_url, api_key: form.api_key, models: form.models } });
      setForm(EMPTY_FORM);
      load();
      toast.ok("Provider added");
    } catch (err) { toast.err(err); }
  };
  const toggle = async (p: Provider) => {
    await api(`/providers/${p.id}`, { method: "PUT", body: { name: p.name, auth_kind: p.auth_kind, protocol: p.protocol, base_url: p.base_url, models: p.models, enabled: !p.enabled } });
    load();
  };
  const remove = async (p: Provider) => {
    if (!confirm(`Remove ${p.name}?`)) return;
    await api(`/providers/${p.id}`, { method: "DELETE" });
    load();
  };
  const usePreset = (k: string) => {
    const p = presets[k];
    if (p) setForm({ ...form, name: p.name, base_url: p.base_url, type: `${p.auth_kind}/${p.protocol}` });
    setNote(p?.note ?? "");
  };

  return (
    <div className="card col gap-lg">
      <div className="row"><KeyRound size={16} /><h2>Model providers</h2></div>
      <div className="muted small"><strong>Free options:</strong> local models with Ollama, OpenRouter models ending in :free (free key), and Google Gemini's free tier (free key). Free models are marked (free) in model menus. OpenCode's free models only run inside the OpenCode app, so here they are available to Company agents through the paperclip add-on. Any OpenAI compatible endpoint works: OpenAI, Anthropic, OpenRouter, Groq, Together, Mistral, LM Studio, vLLM, LiteLLM. If a provider has no model list endpoint, type model ids in “Models”.</div>
      <div className="card flush">
        {list.map((p) => (
          <div key={p.id} className="list-item" style={{ cursor: "default" }}>
            <input type="checkbox" checked={!!p.enabled} onChange={() => toggle(p)} />
            <div className="grow">
              <div>{p.name} <span className="badge">{typeLabel(p)}</span>{p.state !== "connected" && <span className="badge">{p.state === "needs_auth" ? "needs sign-in" : p.state}</span>}</div>
              <div className="faint small mono truncate">{p.base_url}{p.has_key ? ` · key ${p.key_hint || "set"}` : ""}{p.models ? ` · ${p.models}` : ""}</div>
              {p.last_error && <div className="small" style={{ color: "var(--danger)" }}>{p.last_error}</div>}
            </div>
            <button className="btn ghost sm icon" onClick={() => remove(p)}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>
      <form className="col" onSubmit={add}>
        <div className="row wrap">
          <span className="small muted">Preset:</span>
          {Object.keys(presets).map((k) => <button type="button" key={k} className="btn sm" onClick={() => usePreset(k)}>{presets[k].name}</button>)}
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
      </form>
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
      <div className="row"><Cpu size={16} /><h2>Local models</h2><div className="spacer" />{status && (status.online ? <span className="badge ok">Ollama online</span> : <span className="badge danger">Ollama offline</span>)}<button className="btn ghost sm icon" onClick={load}><RefreshCw size={13} /></button></div>
      <div className="row wrap">{status?.models.map((m) => <span key={m} className="badge mono">{m}</span>)}{status?.models.length === 0 && <span className="muted small">No models downloaded yet.</span>}</div>
      <form className="row" onSubmit={start}>
        <input className="input grow mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="Model from ollama.com/library, e.g. qwen3:8b, gemma3:12b, llama3.2:3b" />
        <button className="btn" disabled={!name.trim() || !!pull}>{pull ? <Loader2 size={15} className="spin" /> : <Download size={15} />} Download</button>
      </form>
      {pull && <div className="col"><div className="progress"><div style={{ width: `${pull.pct}%` }} /></div><div className="muted small">{pull.model}: {pull.status} {pull.pct ? `${pull.pct}%` : ""}</div></div>}
      <div className="muted small">Models downloaded here are shared with Chat. Rough guide: a 4B model needs ~4 GB RAM, 8B ~8 GB, 14B ~12 GB. A GPU makes them much faster.</div>
      {toast.node}
    </div>
  );
}

function MailAccounts() {
  const [list, setList] = useState<MailAccount[]>([]);
  const [preset, setPreset] = useState("Fastmail");
  const [form, setForm] = useState({ name: "", email: "", username: "", password: "", imap_port: 993, ...MAIL_PRESETS.Fastmail });
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const load = () => api<MailAccount[]>("/mail/accounts").then(setList);
  useEffect(() => { load(); }, []);
  const pick = (k: string) => { setPreset(k); setForm({ ...form, ...MAIL_PRESETS[k] }); };
  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/mail/accounts", { body: { ...form, username: form.username || form.email } });
      toast.ok("Mail connected");
      setForm({ ...form, password: "" });
      load();
    } catch (err) { toast.err(err); } finally { setBusy(false); }
  };
  return (
    <div className="card col gap-lg" id="mail">
      <div className="row"><Mail size={16} /><h2>Mail</h2></div>
      {list.map((a) => (
        <div key={a.id} className="row">
          <Check size={14} className="prio-1" /><div className="grow">{a.name} <span className="muted">· {a.email}</span></div>
          <button className="btn ghost sm icon" onClick={async () => { if (confirm(`Disconnect ${a.email}?`)) { await api(`/mail/accounts/${a.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={13} /></button>
        </div>
      ))}
      <form className="col" onSubmit={add}>
        <div className="tabs">{Object.keys(MAIL_PRESETS).map((k) => <button type="button" key={k} className={`tab ${preset === k ? "active" : ""}`} onClick={() => pick(k)}>{k}</button>)}</div>
        <div className="muted small">{MAIL_PRESETS[preset].note} Outlook.com and Microsoft 365 no longer accept passwords over IMAP (OAuth only), so they are not supported here.</div>
        <div className="row wrap">
          <input className="input grow" required placeholder="Your name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input grow" required type="email" placeholder="Email address" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </div>
        <div className="row wrap">
          <input className="input grow" placeholder="Username (defaults to email)" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input className="input grow" required type="password" placeholder="App password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        <div className="row wrap">
          <input className="input grow" required placeholder="IMAP host" value={form.imap_host} onChange={(e) => setForm({ ...form, imap_host: e.target.value })} />
          <input className="input" style={{ width: 90 }} type="number" value={form.imap_port} onChange={(e) => setForm({ ...form, imap_port: Number(e.target.value) })} />
          <input className="input grow" required placeholder="SMTP host" value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
          <input className="input" style={{ width: 90 }} type="number" value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })} />
          <select className="input" style={{ width: 120 }} value={form.smtp_security} onChange={(e) => setForm({ ...form, smtp_security: e.target.value as "ssl" | "starttls" })}><option value="ssl">SSL</option><option value="starttls">STARTTLS</option></select>
        </div>
        <button className="btn primary" disabled={busy}>{busy ? <Loader2 size={15} className="spin" /> : "Test and connect"}</button>
      </form>
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
      <div className="muted small">Every model call is counted (chat, Terminal, Telegram, schedules, agents, learning). OpenRouter reports real cost. For other providers enter prices per million tokens from your provider's pricing page, or cost shows as $0.</div>
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
  const [s, setS] = useState({ workspace_name: "", default_model: "", chat_url: "", tools_url: "" });
  const [pw, setPw] = useState({ current: "", new: "" });
  const toast = useToast();
  const [desktop, setDesktop] = useState<DesktopInfo | null>(null);
  useEffect(() => { api<typeof s>("/settings").then(setS); }, []);
  useEffect(() => { api<DesktopInfo>("/desktop/info").then(setDesktop).catch(() => setDesktop(null)); }, []);

  const save = async () => {
    try { setS(await api("/settings", { method: "PUT", body: { workspace_name: s.workspace_name, default_model: s.default_model } })); toast.ok("Saved"); onChanged(); }
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
          <label className="field">Default model <span className="hint">Used for research, writing assist, task breakdown and mail drafts unless you pick another.</span>
            <div className="row"><div className="grow"><ModelSelect value={s.default_model} onChange={(v) => setS({ ...s, default_model: v })} models={models} /></div><button className="btn icon" onClick={() => reload(true)} title="Refresh model list"><RefreshCw size={14} /></button></div>
          </label>
          <div><button className="btn primary" onClick={save}>Save</button></div>
        </div>
        {desktop?.desktop && <DesktopApp info={desktop} />}
        <Usage />
        <LocalModels />
        <Providers />
        <MailAccounts />
        <div className="card col">
          <h2>Calendars</h2>
          <div className="muted small">Add ICS subscriptions or CalDAV accounts from the Calendar page.</div>
          <div><Link className="btn" to="/calendar">Open calendar</Link></div>
        </div>
        <div className="card col">
          <div className="row"><Plug size={16} /><h2>Chat integration</h2></div>
          <div className="muted small">Chat (Open WebUI) is preconfigured with a Workbench tool server at <span className="mono">{s.tools_url}</span>, so agents can search notes, manage tasks and events, search mail and save drafts. Agents can never send mail. Enable it per chat from the tools button in the message box, or for a model in Admin → Models.</div>
          <div><a className="btn" href={s.chat_url} target="_blank" rel="noreferrer">Open chat</a></div>
        </div>
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
