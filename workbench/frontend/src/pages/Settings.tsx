import { Check, Cpu, Loader2, Mail, Plug, RefreshCw, Shield, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";
import { useModels, useToast } from "../util";
import { PasswordInput } from "../components/ui";
import { ModelTest } from "./Auth";
import { ModelRoles } from "../components/ModelRoles";
import { ModelsPanel } from "../components/Models";
import ConnectionDirectory from "../components/ConnectionDirectory";
import { AiModePanel, LocalDownloads } from "../components/AiSetup";
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
  const load = () => api<{ online: boolean; models: string[] }>("/ollama/status").then(setStatus);
  useEffect(() => { load(); }, []);
  return (
    <div className="card col gap-lg">
      <div className="row"><Cpu size={16} /><h2>Local models</h2><div className="spacer" />{status && (status.online ? <span className="badge ok">Ollama online</span> : <span className="badge danger">Ollama offline</span>)}<button className="btn ghost sm icon" onClick={load} aria-label="Check Ollama again" title="Check again"><RefreshCw size={13} /></button></div>
      <LocalDownloads onDone={load} />
      <div className="muted small">Models downloaded here are shared with Chat. A graphics card makes them much faster.</div>
    </div>
  );
}

/** The timezone reminders, automations and check-ins use. Saved on the server; the browser's zone is offered. */
function TimezoneSetting() {
  const [tz, setTz] = useState<string | null>(null);
  const [pick, setPick] = useState("");
  const [state, setState] = useState<"" | "saving" | "saved" | "error">("");
  const [err, setErr] = useState("");
  const browserTz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } })();
  const zones = (() => { try { return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone") ?? []; } catch { return []; } })();
  useEffect(() => { api<{ timezone: string }>("/assistant/settings").then((r) => { setTz(r.timezone); setPick(r.timezone); }).catch(() => setTz("")); }, []);
  const save = async (value: string) => {
    setState("saving"); setErr("");
    try { const r = await api<{ timezone: string }>("/assistant/settings", { method: "PUT", body: { timezone: value } }); setTz(r.timezone); setPick(r.timezone); setState("saved"); }
    catch (e) { setErr((e as Error).message); setState("error"); }
  };
  if (tz === null) return <Loader2 size={14} className="spin" aria-label="Loading" />;
  return (
    <div className="setting-row">
      <div><div className="field" style={{ fontWeight: 550 }}>Timezone</div>
        <div className="muted small">Reminders, automations and check-ins run on this clock. Now: <strong>{tz || "UTC"}</strong>{browserTz && browserTz !== tz ? `; this browser is in ${browserTz}.` : "."}</div></div>
      <div className="row wrap">
        {zones.length ? (
          <select className="input" aria-label="Timezone" value={pick} onChange={(e) => { setPick(e.target.value); setState(""); }} style={{ maxWidth: 240 }}>
            {!zones.includes(pick) && pick && <option value={pick}>{pick}</option>}
            {zones.map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        ) : <input className="input" aria-label="Timezone" value={pick} onChange={(e) => { setPick(e.target.value); setState(""); }} placeholder="Australia/Sydney" style={{ maxWidth: 240 }} />}
        <button type="button" className="btn" disabled={!pick || pick === tz || state === "saving"} onClick={() => save(pick)}>Save timezone</button>
        {browserTz && browserTz !== tz && <button type="button" className="btn ghost sm" onClick={() => save(browserTz)}>Use {browserTz}</button>}
        <span className={`save-state ${state === "saved" ? "ok" : state === "error" ? "err" : ""}`} role="status" aria-live="polite">
          {state === "saving" ? "Saving…" : state === "saved" ? <><Check size={13} /> Saved</> : state === "error" ? err : ""}</span>
      </div>
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
          <input className="input" aria-label="IMAP port" style={{ width: 90 }} type="number" value={form.imap_port} onChange={(e) => setForm({ ...form, imap_port: Number(e.target.value) })} />
          <input className="input grow" required placeholder="SMTP host" value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
          <input className="input" aria-label="SMTP port" style={{ width: 90 }} type="number" value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })} />
          <select className="input" aria-label="SMTP security" style={{ width: 120 }} value={form.smtp_security} onChange={(e) => setForm({ ...form, smtp_security: e.target.value as "ssl" | "starttls" })}><option value="ssl">SSL</option><option value="starttls">STARTTLS</option></select>
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

const SECTIONS: [string, string][] = [["s-models", "AI models"], ["s-voice", "Voice and calls"], ["s-computer", "Computer Use"], ["s-connections", "Connections"],
  ["s-privacy", "Memory and privacy"], ["s-automations", "Automations"], ["s-security", "Security and permissions"], ["s-usage", "Usage and budgets"],
  ["s-appearance", "Appearance"], ["s-advanced", "Advanced"]];

function Section({ id, title, about, children }: { id: string; title: string; about: string; children: React.ReactNode }) {
  return (
    <section id={id} className="settings-section col gap-lg" aria-labelledby={`${id}-h`}>
      <div><h2 id={`${id}-h`} className="settings-title">{title}</h2><p className="muted small" style={{ margin: "2px 0 0" }}>{about}</p></div>
      {children}
    </section>
  );
}

type CU = { settings: { enabled: boolean; mode: string }; devices: { id: number; name: string; os: string; online: boolean; last_seen: string }[]; vision: { enabled?: boolean } };

/** Where Computer Use stands, and the difference between the three ways ROOKS can act. The controls are on the Computer page. */
function ComputerSummary() {
  const [c, setC] = useState<CU | null>(null);
  useEffect(() => { api<CU>("/computer").then(setC).catch(() => setC(null)); }, []);
  const device = c?.devices.find((d) => d.online) ?? c?.devices[0];
  return (
    <div className="card col">
      <div className="row wrap">
        <span className={`badge ${c?.settings.enabled && device?.online ? "ok" : ""}`}>{!c ? "Checking…" : c.settings.enabled ? (device?.online ? "On" : "On, but no computer is connected") : "Off"}</span>
        {c && <span className="badge">{{ read_only: "Look only", ask: "Ask before changes", allowed: "Allowed" }[c.settings.mode] ?? c.settings.mode}</span>}
        {device && <span className="small muted">{device.name} ({device.os}) · {device.online ? "connected" : "not connected"}</span>}
      </div>
      <ol className="settings-facts">
        <li><strong>Connections</strong> (mail, calendar, GitHub): ROOKS uses the service's own interface. Safest and most reliable.</li>
        <li><strong>Browser agent</strong> (optional add-on): a separate browser on the server that completes goals on websites.</li>
        <li><strong>Desktop control</strong> (ROOKS Companion): uses apps, its own browser and folders you allow on your computer. Always visible, stoppable, and it asks before sending, buying, deleting or installing.</li>
      </ol>
      <div className="row wrap"><Link className="btn primary" to="/computer">{device ? "Open Computer controls" : "Pair a computer"}</Link></div>
    </div>
  );
}

export default function Settings({ onChanged }: { onChanged: () => void }) {
  const { models, reload } = useModels();
  const [s, setS] = useState({ workspace_name: "", default_model: "", chat_url: "", tools_url: "" });
  const [pw, setPw] = useState({ current: "", new: "" });
  const [localAvailable, setLocalAvailable] = useState(false);
  const location = useLocation();
  const [active, setActive] = useState(SECTIONS[0][0]);
  const [modeRev, setModeRev] = useState(0);  // remounts the models panel after an AI mode change
  const lock = useRef(0);
  useEffect(() => {  // the section in view is highlighted: the last one whose top has passed the top of the view
    const scroller = document.querySelector(".main");
    const onScroll = () => {
      if (Date.now() < lock.current) return;
      let cur = SECTIONS[0][0];
      for (const [id] of SECTIONS) {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= 140) cur = id;
      }
      setActive(cur);
    };
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller?.removeEventListener("scroll", onScroll);
  }, []);
  const jump = (id: string, smooth = true) => {
    lock.current = Date.now() + 900;  // a click decides; the scroll it causes does not override it
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" });
    history.replaceState(null, "", `#${id}`);
  };
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
    <div className="page settings-page">
      <div className="page-head"><div><h1>Settings</h1><p className="muted">Everything ROOKS needs, in plain words. Only an AI model is required; the rest is optional.</p></div></div>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} className={active === id ? "active" : ""} aria-current={active === id ? "true" : undefined}
            onClick={(e) => { e.preventDefault(); jump(id); }}>{label}</a>)}
        </nav>
        <select className="input settings-jump" aria-label="Jump to a settings section" value={active} onChange={(e) => jump(e.target.value, false)}>
          {SECTIONS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <div className="col gap-lg settings-body">
          <Section id="s-models" title="AI models" about="Choose how ROOKS uses AI, then connect a provider. ROOKS checks each key with a tiny free request when it can, and never tests paid models without asking.">
            <AiModePanel onChanged={() => { setModeRev((n) => n + 1); reload(true); }} />
            <div id="models" style={{ scrollMarginTop: 16 }}><ModelsPanel key={modeRev} models={models} reloadModels={() => reload(true)} localAvailable={localAvailable} /></div>
            {models.length > 0 && <div className="card col"><h3>Quick check</h3><p className="muted small" style={{ margin: 0 }}>Sends one tiny message through the default model (and its backups if needed).</p><ModelTest /></div>}
            <ConnectionDirectory key={`dir-${modeRev}`} />
            {models.length > 0 && <ModelRoles models={models} />}
            {localAvailable && <LocalModels />}
          </Section>

          <Section id="s-voice" title="Voice and calls" about="Talk with ROOKS, and choose when it may ask to talk to you. Phone calls are optional and off until you set them up.">
            <VoiceSettingsPanel />
            <CallsPanel />
          </Section>

          <Section id="s-computer" title="Computer Use" about="Three different ways ROOKS can act for you, from safest to most powerful.">
            <ComputerSummary />
          </Section>

          <Section id="s-connections" title="Connections" about="Services ROOKS can use. A connection shows as working only after a real check, not because a key was saved.">
            <ConnectionsHealth />
            <WebSearchPanel />
            <MailAccounts />
            <GitHubPanel />
            <div className="card col">
              <h2>Calendars</h2>
              <div className="muted small">Subscribe to a calendar link (Google, Outlook, iCloud) or connect CalDAV from the Calendar page.</div>
              <div><Link className="btn" to="/calendar">Open calendar</Link></div>
            </div>
            {s.chat_url && (
              <div className="card col">
                <div className="row"><Plug size={16} aria-hidden="true" /><h2>Open WebUI add-on</h2></div>
                <div className="muted small">Open WebUI is preconfigured with the ROOKS tool server at <span className="mono">{s.tools_url}</span>, so its agents can search notes, manage tasks and events, search mail and save drafts. They can never send mail. Turn it on per chat from the tools button in the message box, or for a model under Admin, Models.</div>
                <div><a className="btn" href={s.chat_url} target="_blank" rel="noreferrer">Open Open WebUI</a></div>
              </div>
            )}
          </Section>

          <Section id="s-privacy" title="Memory and privacy" about="What ROOKS keeps, and how to review or remove it.">
            <div className="card col">
              <ul className="settings-facts">
                <li><strong>Memory.</strong> ROOKS learns lasting facts from conversations (never keys, passwords or card numbers). <Link to="/assistant-memory">Review and correct memories</Link> or turn learning off in <Link to="/assistant-settings">Assistant settings</Link>.</li>
                <li><strong>Projects.</strong> A project set to local models only never sends its content to a cloud model, including for voice and research.</li>
                <li><strong>Voice.</strong> Audio is never stored. You choose at the end of each voice session whether to keep the transcript.</li>
                <li><strong>Screenshots.</strong> Computer Use screenshots stay in memory and are never written to disk or logs; sending them to a model is a separate permission on the Computer page.</li>
                <li><strong>Activity.</strong> Every change ROOKS makes is in the activity log under Security, without typed text or secrets.</li>
              </ul>
            </div>
          </Section>

          <Section id="s-automations" title="Automations and notifications" about="Scheduled jobs, check-ins, quiet hours and where ROOKS tells you things.">
            <div className="card col"><TimezoneSetting /></div>
            <div className="card col">
              <div className="muted small">Check-ins, their quiet hours and daily limits are on the Automations page. Briefings go to the ROOKS inbox, and to Telegram if you connect it. Calls follow the contact hours above.</div>
              <div className="row wrap"><Link className="btn" to="/automations">Open automations</Link><Link className="btn ghost" to="/needs-you">See what needs you</Link></div>
            </div>
            <div className="card col">
              <h2>Telegram and terminal</h2>
              <div className="muted small">Chat with ROOKS from Telegram, or create a token for the <span className="mono">ws</span> terminal command.</div>
              <div><Link className="btn" to="/assistant-settings">Open assistant settings</Link></div>
            </div>
          </Section>

          <Section id="s-security" title="Security and permissions" about="What the assistant may do on its own, the isolated workspace, your password and the activity log.">
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
          </Section>

          <Section id="s-usage" title="Usage and budgets" about="What ROOKS has spent, your monthly limit and your prices per model.">
            <Usage />
          </Section>

          <Section id="s-appearance" title="Appearance" about="Theme, accent colour, font, text size and spacing. Light, dark or following your device.">
            <AppearancePanel />
          </Section>

          <Section id="s-advanced" title="Advanced" about="You can leave these alone.">
            <div className="card col gap-lg">
              <h2>Workspace</h2>
              <label className="field">Workspace name <span className="hint">Shown in the sidebar and browser tab.</span><input className="input" value={s.workspace_name} onChange={(e) => setS({ ...s, workspace_name: e.target.value })} /></label>
              <div className="row wrap"><button className="btn primary" onClick={save}>Save</button></div>
            </div>
            <ToolsPanel />
            <div className="card col">
              <h2>Getting started</h2>
              <div className="muted small">The checklist on Home tracks your first steps. {gs ? "It is hidden right now." : "It is showing on Home."}</div>
              {gs && <div><button className="btn" onClick={showChecklist}>Show it again</button></div>}
            </div>
          </Section>
        </div>
      </div>
      {toast.node}
    </div>
  );
}
