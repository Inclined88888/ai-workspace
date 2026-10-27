import { Check, Cpu, Download, Loader2, Mail, Plug, RefreshCw, Shield, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, stream } from "../api";
import { useModels, useToast } from "../util";
import { PasswordInput } from "../components/ui";
import { ModelTest } from "./Auth";
import { ModelRoles } from "../components/ModelRoles";
import { ModelsPanel } from "../components/Models";
import WebSearchPanel from "../components/WebSearch";
import AppearancePanel from "../components/AppearancePanel";
import { ActivityLog, ConnectionsHealth, GitHubPanel, PermissionsPanel, ToolsPanel } from "../components/Trust";
import { WorkspacePanel } from "../components/WorkspacePanel";
import { CallsPanel, VoiceSettingsPanel } from "../components/VoicePanel";

type MailAccount = { id: number; name: string; email: string; imap_host: string; smtp_host: string };

const MAIL_PRESETS: Record<string, { imap_host: string; smtp_host: string; smtp_port: number; smtp_security: "ssl" | "starttls"; note: string }> = {
  Fastmail: { imap_host: "imap.fastmail.com", smtp_host: "smtp.fastmail.com", smtp_port: 465, smtp_security: "ssl", note: "Use an app password from Settings → Privacy & Security." },
  iCloud: { imap_host: "imap.mail.me.com", smtp_host: "smtp.mail.me.com", smtp_port: 587, smtp_security: "starttls", note: "Use an app specific password from appleid.apple.com. Username is your full iCloud address." },
  Gmail: { imap_host: "imap.gmail.com", smtp_host: "smtp.gmail.com", smtp_port: 465, smtp_security: "ssl", note: "Needs 2 step verification and an app password (myaccount.google.com/apppasswords). Some Workspace admins disable this." },
  Other: { imap_host: "", smtp_host: "", smtp_port: 465, smtp_security: "ssl", note: "Ask your provider for IMAP (port 993, SSL) and SMTP settings." },
};

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
      <div className="row"><Cpu size={16} /><h2>Local models</h2><div className="spacer" />{status && (status.online ? <span className="badge ok">Ollama online</span> : <span className="badge danger">Ollama offline</span>)}<button className="btn ghost sm icon" onClick={load} aria-label="Check Ollama again" title="Check again"><RefreshCw size={13} /></button></div>
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
          <button className="btn ghost sm icon" aria-label={`Disconnect ${a.email}`} title="Disconnect account" onClick={async () => { if (confirm(`Disconnect ${a.email}?`)) { await api(`/mail/accounts/${a.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={13} /></button>
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
  by_model: { model: string; tokens: number; cents: number; unpriced_calls: number }[]; by_channel: { channel: string; cents: number; tokens: number }[];
  limits?: { monthly_limit_cents: number; fallback_allow_paid: boolean } };

function Usage() {
  const [u, setU] = useState<UsageSummary | null>(null);
  const [row, setRow] = useState({ model: "", in: "", out: "" });
  const toast = useToast();
  const load = () => api<UsageSummary>("/usage").then(setU).catch(toast.err);
  useEffect(() => { load(); }, []);
  const savePrices = async (prices: UsageSummary["prices"]) => {
    try { setU(await api<UsageSummary>("/usage/prices", { method: "PUT", body: { prices } })); toast.ok("Prices saved"); } catch (e) { toast.err(e); }
  };
  const saveLimits = async (body: object) => {
    try { const limits = await api<UsageSummary["limits"]>("/usage/limits", { method: "PUT", body }); setU((x) => (x ? { ...x, limits } : x)); toast.ok("Saved"); } catch (e) { toast.err(e); }
  };
  if (!u) return null;
  return (
    <div className="card col gap-lg" id="usage">
      <div className="row"><h2 className="grow">Usage this month</h2><span className="badge">${(u.month_cents / 100).toFixed(2)} · {u.month_tokens.toLocaleString()} tokens</span></div>
      <div className="row wrap" style={{ alignItems: "flex-end" }}>
        <label className="field">Monthly spending limit ($) <span className="hint">0 = no limit. Model calls stop when reached.</span>
          <input className="input" type="number" min={0} step="1" style={{ width: 140 }} defaultValue={(u.limits?.monthly_limit_cents ?? 0) / 100}
            onBlur={(e) => saveLimits({ monthly_limit_cents: Math.round(Number(e.target.value || 0) * 100) })} aria-label="Monthly spending limit in dollars" /></label>
        <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={!!u.limits?.fallback_allow_paid} onChange={(e) => saveLimits({ fallback_allow_paid: e.target.checked })} />
          Backup models may cost money even when my default is free</label>
      </div>
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
          <button className="btn ghost sm icon" aria-label={`Remove price for ${m}`} title="Remove price" onClick={() => { const n = { ...u.prices }; delete n[m]; savePrices(n); }}><Trash2 size={12} /></button></div>
      ))}
      {toast.node}
    </div>
  );
}

export default function Settings({ onChanged }: { onChanged: () => void }) {
  const { models, reload } = useModels();
  const [s, setS] = useState({ workspace_name: "", default_model: "", chat_url: "", tools_url: "" });
  const [pw, setPw] = useState({ current: "", new: "" });
  const [localAvailable, setLocalAvailable] = useState(false);
  const location = useLocation();
  // Links like /settings#models (from chat errors) land on the right section once it has rendered
  useEffect(() => {
    if (!location.hash) return;
    const t = setTimeout(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "start" }), 300);
    return () => clearTimeout(t);
  }, [location.hash]);
  useEffect(() => { api<{ local_models_enabled: boolean }>("/status").then((r) => setLocalAvailable(!!r.local_models_enabled)).catch(() => setLocalAvailable(false)); }, []);
  const toast = useToast();
  useEffect(() => { api<typeof s>("/settings").then(setS); }, []);

  const save = async () => {
    try { setS(await api("/settings", { method: "PUT", body: { workspace_name: s.workspace_name } })); toast.ok("Saved"); onChanged(); }
    catch (e) { toast.err(e); }
  };
  const changePw = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/password", { body: pw }); setPw({ current: "", new: "" }); toast.ok("Password changed. Other sessions were signed out."); }
    catch (err) { toast.err(err); }
  };

  const [gs, setGs] = useState<boolean | null>(null);
  useEffect(() => { api<{ dismissed: boolean }>("/getting-started").then((r) => setGs(r.dismissed)).catch(() => setGs(null)); }, []);
  const showChecklist = async () => {
    try { await api("/getting-started", { method: "PUT", body: { dismissed: false } }); setGs(false); toast.ok("The getting-started checklist is back on Home"); }
    catch (e) { toast.err(e); }
  };

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head"><div><h1>Settings</h1><p className="muted">Essentials first. Everything under Advanced is optional.</p></div></div>
      <div className="col gap-lg">
        <h2 className="settings-group">Essentials</h2>
        <div className="card col gap-lg">
          <h2>Workspace</h2>
          <label className="field">Workspace name <span className="hint">Shown in the sidebar and browser tab.</span><input className="input" value={s.workspace_name} onChange={(e) => setS({ ...s, workspace_name: e.target.value })} /></label>
          <div className="row wrap"><button className="btn primary" onClick={save}>Save</button></div>
        </div>
        <div id="models" style={{ scrollMarginTop: 16 }}><ModelsPanel models={models} reloadModels={() => reload(true)} localAvailable={localAvailable} /></div>
        {models.length > 0 && <div className="card col"><h3>Quick check</h3><p className="muted small" style={{ margin: 0 }}>Sends one tiny message through the default model (and its backups if needed).</p><ModelTest /></div>}
        {models.length > 0 && <ModelRoles models={models} />}
        {localAvailable && <LocalModels />}

        <h2 className="settings-group">Appearance</h2>
        <AppearancePanel />

        <h2 className="settings-group">Connections</h2>
        <ConnectionsHealth />
        <WebSearchPanel />
        <MailAccounts />
        <GitHubPanel />
        <div className="card col">
          <h2>Calendars</h2>
          <div className="muted small">Subscribe to a calendar link (Google, Outlook, iCloud) or connect CalDAV from the Calendar page.</div>
          <div><Link className="btn" to="/calendar">Open calendar</Link></div>
        </div>
        <div className="card col">
          <h2>Telegram and terminal</h2>
          <div className="muted small">Chat with ROOKS from Telegram, or create a token for the <span className="mono">ws</span> terminal command.</div>
          <div><Link className="btn" to="/assistant-settings">Open assistant settings</Link></div>
        </div>
        {s.chat_url && (
          <div className="card col">
            <div className="row"><Plug size={16} aria-hidden="true" /><h2>Open WebUI add-on</h2></div>
            <div className="muted small">Open WebUI is preconfigured with the ROOKS tool server at <span className="mono">{s.tools_url}</span>, so its agents can search notes, manage tasks and events, search mail and save drafts. They can never send mail. Turn it on per chat from the tools button in the message box, or for a model under Admin, Models.</div>
            <div><a className="btn" href={s.chat_url} target="_blank" rel="noreferrer">Open Open WebUI</a></div>
          </div>
        )}

        <h2 className="settings-group">Voice and calls</h2>
        <VoiceSettingsPanel />
        <CallsPanel />

        <h2 className="settings-group">Security</h2>
        <PermissionsPanel />
        <WorkspacePanel />
        <form className="card col" onSubmit={changePw}>
          <div className="row"><Shield size={16} aria-hidden="true" /><h2>Password</h2></div>
          <div className="muted small">Changing it signs out every other browser and device.</div>
          <div className="grid two">
            <div className="col" style={{ gap: 5 }}><label htmlFor="pw-current" className="field">Current password</label><PasswordInput id="pw-current" value={pw.current} onChange={(v) => setPw({ ...pw, current: v })} autoComplete="current-password" /></div>
            <div className="col" style={{ gap: 5 }}><label htmlFor="pw-new" className="field">New password <span className="hint">at least 10 characters</span></label><PasswordInput id="pw-new" value={pw.new} onChange={(v) => setPw({ ...pw, new: v })} autoComplete="new-password" invalid={pw.new.length > 0 && pw.new.length < 10} /></div>
          </div>
          <div><button className="btn" disabled={!pw.current || pw.new.length < 10}>Change password</button></div>
        </form>

        <ActivityLog />

        <h2 className="settings-group">Advanced</h2>
        <ToolsPanel />
        <Usage />
        <div className="card col">
          <h2>Getting started</h2>
          <div className="muted small">The checklist on Home tracks your first steps. {gs ? "It is hidden right now." : "It is showing on Home."}</div>
          {gs && <div><button className="btn" onClick={showChecklist}>Show it again</button></div>}
        </div>
      </div>
      {toast.node}
    </div>
  );
}
