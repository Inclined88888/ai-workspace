import { AlertTriangle, CalendarDays, Check, ChevronRight, ExternalLink, KeyRound, Laptop, Loader2, Mail, RefreshCw, ShieldCheck, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useToast } from "../util";
import "../connect.css";

type Provider = "google" | "microsoft";
type ProviderInfo = { configured: boolean; source: string; message: string; tenant: string; client_id_hint: string };
type Linked = { id: number; provider: Provider; email: string; name: string; status: "connected" | "reauth"; last_error: string; mail_account_id: number | null; calendar_id: number | null };
type MailAccount = { id: number; name: string; email: string; kind?: string; imap_host: string };
type Cal = { id: number; name: string; kind: string; color: string };
type Detected = {
  kind: "one_click" | "known" | "guess" | "invalid" | "unavailable"; provider?: Provider; workspace?: boolean; label?: string; message?: string;
  imap_host?: string; imap_port?: number; smtp_host?: string; smtp_port?: number; smtp_security?: "ssl" | "starttls";
  caldav_url?: string; app_password?: boolean; app_password_url?: string; steps?: string[]; username_hint?: string; source?: string;
  calendar_help?: string[]; key?: string;
  fallback?: Detected; one_click_unavailable?: Provider; mac?: boolean;
};
type MacAccount = { name: string; emails: string[]; enabled: boolean; type: string; connected: boolean };

const NAMES: Record<Provider, string> = { google: "Google", microsoft: "Microsoft" };
const WHAT: Record<Provider, string> = { google: "Gmail and Google Calendar", microsoft: "Outlook, Hotmail, Microsoft 365 mail and calendar" };

export function ProviderLogo({ provider }: { provider: Provider }) {
  if (provider === "google") {
    return (
      <svg className="logo" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.6 13.3l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z" />
        <path fill="#FBBC05" d="M10.5 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C.9 16.6 0 20.2 0 24s.9 7.4 2.6 10.7l7.9-6.1z" />
        <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.3 0-11.6-4.1-13.5-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
      </svg>
    );
  }
  return (
    <svg className="logo" viewBox="0 0 23 23" aria-hidden="true">
      <path fill="#f25022" d="M1 1h10v10H1z" /><path fill="#7fba00" d="M12 1h10v10H12z" />
      <path fill="#00a4ef" d="M1 12h10v10H1z" /><path fill="#ffb900" d="M12 12h10v10H12z" />
    </svg>
  );
}

/** "Connect your mail & calendar": one sign-in with Google or Microsoft sets up both; other providers by app password. */
export default function ConnectMailCalendar({ compact = false, onlyWhenEmpty = false, onChange }: { compact?: boolean; onlyWhenEmpty?: boolean; onChange?: () => void }) {
  const [providers, setProviders] = useState<Record<Provider, ProviderInfo> | null>(null);
  const [linked, setLinked] = useState<Linked[]>([]);
  const [imap, setImap] = useState<MailAccount[]>([]);
  const [cals, setCals] = useState<Cal[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState<{ provider: Provider; url: string; state: string } | null>(null);
  const [pasted, setPasted] = useState("");
  const [notice, setNotice] = useState<{ provider: Provider; text: string } | null>(null);
  const [error, setError] = useState("");
  const [other, setOther] = useState(false);
  const timer = useRef<number>();
  const toast = useToast();

  const load = async () => {
    const [p, l, m, c] = await Promise.all([
      api<Record<Provider, ProviderInfo>>("/connect/providers").catch(() => null),
      api<Linked[]>("/connect/accounts").catch(() => []),
      api<MailAccount[]>("/mail/accounts").catch(() => []),
      api<Cal[]>("/calendars").catch(() => []),
    ]);
    setProviders(p); setLinked(l); setImap(m.filter((a) => !a.kind || a.kind === "imap" || a.kind === "mac")); setCals(c.filter((x) => x.kind === "caldav" || x.kind === "ics"));
    setLoaded(true);
  };
  useEffect(() => { load(); return () => window.clearInterval(timer.current); }, []);

  const finished = (s: { status: string; error?: string; email?: string }) => {
    if (s.status === "pending") return false;
    window.clearInterval(timer.current);
    setPending(null); setPasted("");
    if (s.status === "connected") { toast.ok(`${s.email} is connected for mail and calendar`); load(); onChange?.(); }
    else setError(s.error || "That sign-in didn't finish. Try again.");
    return true;
  };

  const connect = async (provider: Provider, hint = "") => {
    setError(""); setNotice(null);
    const info = providers?.[provider];
    if (info && !info.configured) { setNotice({ provider, text: info.message }); return; }
    try {
      const r = await api<{ url: string; state: string; opened: boolean }>(`/connect/${provider}/start`, { body: { login_hint: hint } });
      if (!r.opened) window.open(r.url, "_blank", "noopener");
      setPending({ provider, url: r.url, state: r.state });
      window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        const s = await api<{ status: string; error?: string; email?: string }>(`/connect/status/${r.state}`).catch(() => null);
        if (s) finished(s);
      }, 1500);
    } catch (e) {
      const msg = (e as Error).message;
      if ((e as { status?: number }).status === 409) setNotice({ provider, text: msg }); else setError(msg);
    }
  };

  const completePasted = async () => {
    const s = await api<{ status: string; error?: string; email?: string }>("/connect/complete", { body: { url: pasted.trim() } }).catch((e) => ({ status: "failed", error: (e as Error).message }));
    finished(s);
  };

  const disconnect = async (a: Linked) => {
    if (!confirm(`Disconnect ${a.email}? Its mail and calendar are removed from AI Workspace (nothing is deleted at ${NAMES[a.provider]}).`)) return;
    try { const r = await api<{ note: string }>(`/connect/accounts/${a.id}`, { method: "DELETE" }); toast.ok(r.note); load(); onChange?.(); } catch (e) { toast.err(e); }
  };
  const removeImap = async (a: MailAccount) => {
    if (!confirm(`Disconnect ${a.email}?`)) return;
    await api(`/mail/accounts/${a.id}`, { method: "DELETE" }).catch(toast.err); load(); onChange?.();
  };

  if (onlyWhenEmpty && (!loaded || linked.length || imap.length || cals.length)) return null;
  const anything = linked.length + imap.length + cals.length > 0;
  // Only offer one-click buttons this install can actually do; otherwise lead with "type your email address"
  const oneClick = (["google", "microsoft"] as Provider[]).filter((p) => providers?.[p]?.configured);
  const signIn: Provider[] = ["google", "microsoft"];  // always offered: the provider's own sign-in page
  const showOther = other;

  return (
    <div className="card col gap-lg" id="mail" style={compact ? { marginBottom: 16 } : undefined}>
      <div className="row"><Mail size={16} /><CalendarDays size={16} /><h2 className="grow">Connect your mail &amp; calendar</h2>
        {anything && <button className="btn ghost sm icon" title="Refresh" onClick={load}><RefreshCw size={13} /></button>}</div>
      <div className="muted small">Sign in on Google's or Microsoft's own page; personal and work accounts both work. One sign-in sets up both your inbox and your calendar. Your assistant can read, summarise and draft; it never sends an email or an invitation until you say yes.</div>

      {anything && (
        <div className="account-chips">
          {linked.map((a) => (
            <span key={`l${a.id}`} className={`account-chip ${a.status === "reauth" ? "reauth" : ""}`} title={a.last_error || `${a.name} · ${NAMES[a.provider]}`}>
              <ProviderLogo provider={a.provider} />
              <span className="truncate">{a.email}</span>
              <span className="what">{[a.mail_account_id && "Mail", a.calendar_id && "Calendar"].filter(Boolean).join(" + ")}</span>
              {a.status === "reauth"
                ? <button className="btn sm" onClick={() => connect(a.provider, a.email)}><AlertTriangle size={12} /> Sign in again</button>
                : <Check size={13} className="prio-1" />}
              <button className="btn ghost sm icon" title="Disconnect" aria-label={`Disconnect ${a.email}`} onClick={() => disconnect(a)}><X size={12} /></button>
            </span>
          ))}
          {imap.map((a) => (
            <span key={`m${a.id}`} className="account-chip" title={a.imap_host}>
              {a.kind === "mac" ? <Laptop size={13} /> : <Mail size={13} />}<span className="truncate">{a.email}</span><span className="what">{a.kind === "mac" ? "Mail · this Mac" : "Mail · IMAP"}</span>
              <button className="btn ghost sm icon" title="Disconnect" aria-label={`Disconnect ${a.email}`} onClick={() => removeImap(a)}><X size={12} /></button>
            </span>
          ))}
          {cals.map((c) => (
            <Link key={`c${c.id}`} to="/calendar" className="account-chip" title="Manage on the Calendar page">
              <CalendarDays size={13} style={{ color: c.color }} /><span className="truncate">{c.name}</span><span className="what">{c.kind === "ics" ? "Calendar · ICS" : "Calendar · CalDAV"}</span>
            </Link>
          ))}
        </div>
      )}

      <div className="connect-buttons">
        {signIn.map((p) => (
          <button key={p} className="btn connect-btn" disabled={!!pending} onClick={() => connect(p)} title={WHAT[p]}>
            {pending?.provider === p ? <Loader2 size={16} className="spin" /> : <ProviderLogo provider={p} />} Sign in with {NAMES[p]}
          </button>
        ))}
        {!compact && <button className="btn ghost connect-btn" style={{ border: "1px solid transparent" }} onClick={() => setOther(!other)}>iCloud, Yahoo, other <ChevronRight size={14} /></button>}
        {compact && <Link className="btn ghost connect-btn" style={{ border: "1px solid transparent" }} to="/mail">iCloud, Yahoo, other <ChevronRight size={14} /></Link>}
      </div>

      {pending && (
        <div className="connect-pending">
          <Loader2 size={16} className="spin" style={{ marginTop: 2 }} />
          <div className="col grow" style={{ gap: 6 }}>
            <div><strong>Finish signing in with {NAMES[pending.provider]} in your browser.</strong> Allow mail and calendar, then come back here.</div>
            {pending.provider === "google" && <div className="small muted">If Google says it hasn't verified this app, that means this build's Google review isn't finished yet (it's limited to 100 people until it is). If you got AI Workspace from the official download, choose Advanced, then Go to AI Workspace.</div>}
            {pending.provider === "microsoft" && <div className="small muted">Work or school account? Some organisations need an IT admin to approve the app first.</div>}
            <div className="small muted">Browser didn't open? <a href={pending.url} target="_blank" rel="noreferrer">Open the sign-in page <ExternalLink size={11} /></a></div>
            <div className="small muted">Signed in on a different device? Paste the address the browser ended on:</div>
            <div className="row">
              <input className="input mono grow" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="http://127.0.0.1:…/oauth/callback?code=…" />
              <button className="btn sm" disabled={!pasted.includes("state=")} onClick={completePasted}>Finish</button>
              <button className="btn ghost sm" onClick={() => { window.clearInterval(timer.current); setPending(null); }}>Cancel</button>
            </div>
          </div>
        </div>
      )}
      {error && <div className="alert err">{error}</div>}
      {notice && (
        <div className="alert">
          <div className="col" style={{ gap: 6 }}>
            <div>{notice.text}</div>
            <div className="row wrap small">
              {compact ? <Link className="btn sm" to="/mail">Set it up</Link> : <a className="btn sm primary" href="#own-registration" onClick={() => (document.getElementById("own-registration") as HTMLDetailsElement | null)?.setAttribute("open", "")}>Set up {NAMES[notice.provider]} sign-in (one time)</a>}
              {notice.provider === "google" && !compact && <button className="btn sm ghost" onClick={() => { setNotice(null); setOther(true); }}>Use a Gmail app password instead</button>}
            </div>
          </div>
        </div>
      )}

      {!compact && showOther && <OtherProvider plain={!oneClick.length} onSuggest={(p, email) => { setOther(false); connect(p, email); }} onAdded={() => { load(); onChange?.(); }} />}

      {!compact && (
        <>
          <div className="connect-note row"><ShieldCheck size={13} /> Sign-in happens on Google's or Microsoft's own page in your browser; AI Workspace never sees your password and you can remove its access there any time. Tokens stay on this computer{oneClick.length < 2 ? "" : " (in the system keychain when available)"}.</div>
          <OwnRegistration providers={providers} onSaved={load} />
        </>
      )}
      {toast.node}
    </div>
  );
}

/** Mail through the account signed in on this Mac (Internet Accounts). The Mac signs in with the provider's normal page,
 *  so this works for Outlook (no app passwords) and any provider, with no password typed into AI Workspace. */
function MacMail({ email, onAdded }: { email: string; onAdded: () => void }) {
  const [accounts, setAccounts] = useState<MacAccount[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  const check = async () => {
    setError("");
    const r = await api<{ available: boolean; accounts: MacAccount[]; error?: string }>("/mail/mac").catch((e) => ({ available: true, accounts: [], error: (e as Error).message }));
    setAccounts(r.accounts);
    if (r.error) setError(r.error);
  };
  useEffect(() => { check(); }, []);
  const lower = email.toLowerCase();
  const match = accounts?.find((a) => a.emails.some((e) => e.toLowerCase() === lower));
  const connect = async () => {
    if (!match) return;
    setBusy(true); setError("");
    try { await api("/mail/mac", { body: { name: match.name, email } }); toast.ok(`${email} is connected`); onAdded(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="col" style={{ gap: 12 }}>
      {match && match.enabled ? (
        <div className="row wrap">
          <Laptop size={15} /><span className="grow small"><strong>{email}</strong> is already signed in on this Mac.</span>
          <button type="button" className="btn primary sm" disabled={busy} onClick={connect}>{busy ? <Loader2 size={14} className="spin" /> : "Connect"}</button>
        </div>
      ) : (
        <>
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <span className="step-num">1</span>
            <div className="col grow" style={{ gap: 6 }}>
              <button type="button" className="btn primary" style={{ alignSelf: "flex-start" }} onClick={() => api("/mail/mac/settings", { method: "POST" }).catch(toast.err)}>Open Internet Accounts</button>
              <div className="small muted">{match ? <>Open <strong>{match.name}</strong> and turn on <strong>Mail</strong>.</> : <>Click <strong>Add Account</strong>, pick <strong>Microsoft Exchange</strong> (Outlook), and sign in as usual. Keep <strong>Mail</strong> switched on.</>}</div>
            </div>
          </div>
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <span className="step-num">2</span>
            <div className="col grow" style={{ gap: 6 }}>
              <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={check}><RefreshCw size={13} /> Done, check again</button>
              <div className="small muted">macOS may ask once to let AI Workspace use Mail. Click OK.</div>
            </div>
          </div>
        </>
      )}
      {error && <div className="alert err small">{error}</div>}
      {toast.node}
    </div>
  );
}

/** "Gmail", "iCloud", "Yahoo" from labels like "Gmail (app password)" or "iCloud Mail". */
function brand(d: Detected): string {
  return (d.label || "Your provider").replace(/\s*\(.*\)$/, "").replace(/ Mail$/, "");
}
/** Whose account page the app password is made on: Google for Gmail, Apple for iCloud. */
function owner(d: Detected): string {
  return ({ gmail: "Google", icloud: "Apple" } as Record<string, string>)[d.key ?? ""] ?? brand(d);
}

function OtherProvider({ onSuggest, onAdded, plain = false }: { onSuggest: (p: Provider, email: string) => void; onAdded: () => void; plain?: boolean }) {
  const [email, setEmail] = useState("");
  const [det, setDet] = useState<Detected | null>(null);
  const [checking, setChecking] = useState(false);
  const [form, setForm] = useState({ name: "", username: "", password: "", imap_host: "", imap_port: 993, smtp_host: "", smtp_port: 465, smtp_security: "ssl" as "ssl" | "starttls" });
  const [addCal, setAddCal] = useState(true);
  const [showServers, setShowServers] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState<{ email: string; calendar: string[] | null } | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const toast = useToast();

  const detect = async (value = email) => {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim())) { setDet(null); return; }
    setChecking(true);
    try {
      const d = await api<Detected>(`/mail/autodetect?email=${encodeURIComponent(value.trim())}`);
      setDet(d);
      if (d.imap_host) setForm((f) => ({ ...f, imap_host: d.imap_host!, imap_port: d.imap_port ?? 993, smtp_host: d.smtp_host!, smtp_port: d.smtp_port ?? 465, smtp_security: d.smtp_security ?? "ssl" }));
      setShowServers(d.kind === "guess");
    } catch { setDet(null); } finally { setChecking(false); }
  };
  useEffect(() => { const t = window.setTimeout(() => detect(email), 500); return () => window.clearTimeout(t); }, [email]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    const username = form.username || email.trim();
    try {
      await api("/mail/accounts", { body: { ...form, name: form.name || email.split("@")[0], email: email.trim(), username } });
      let calNote = "";
      if (addCal && det?.caldav_url) {
        try {
          const found = await api<{ name: string; url: string }[]>("/calendars/discover", { body: { url: det.caldav_url, username, password: form.password } });
          for (const c of found.slice(0, 8)) await api("/calendars", { body: { name: c.name, kind: "caldav", url: c.url, username, password: form.password, color: "#0ea5e9" } });
          calNote = found.length ? ` and ${found.length} calendar${found.length === 1 ? "" : "s"}` : "";
        } catch { calNote = " (calendar couldn't be found; add it later from the Calendar page)"; }
      }
      toast.ok(`Mail connected${calNote}`);
      setForm({ ...form, password: "" });
      setConnected({ email: email.trim(), calendar: det?.calendar_help ?? null });
      setDet(null); setEmail("");
      onAdded();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };

  return (
    <form ref={formRef} className="card col" onSubmit={submit} style={{ background: "var(--panel-2)" }}>
      {plain
        ? <div className="row"><Mail size={15} /><h3 className="grow">Type your email address</h3><span className="muted small">Gmail, iCloud, Yahoo, Fastmail, AOL, Zoho, your own domain…</span></div>
        : <div className="row"><KeyRound size={15} /><h3 className="grow">Other provider</h3><span className="muted small">iCloud, Yahoo, Fastmail, AOL, Zoho, your own domain…</span></div>}
      <div className="row">
        <input className="input grow" required type="email" autoFocus placeholder="Your email address" value={email} onChange={(e) => setEmail(e.target.value)} onBlur={() => detect()} />
        {checking && <Loader2 size={15} className="spin" />}
      </div>
      {det?.kind === "one_click" && (
        <div className="alert">
          <div className="row wrap">
            <ProviderLogo provider={det.provider!} />
            <div className="grow">{det.workspace ? `${email.split("@")[1]} uses ${det.provider === "google" ? "Google Workspace" : "Microsoft 365"}.` : `That's a ${NAMES[det.provider!]} address.`} One click is easier, with no app password.</div>
            <button type="button" className="btn sm primary" onClick={() => onSuggest(det.provider!, email.trim())}>Connect {NAMES[det.provider!]}</button>
            {det.fallback && <button type="button" className="btn sm ghost" onClick={() => setDet(det.fallback!)}>Use an app password instead</button>}
          </div>
        </div>
      )}
      {det?.kind === "invalid" && email.includes("@") && <div className="alert err small">{det.message}</div>}
      {det?.kind === "unavailable" && (
        <div className="alert col" style={{ gap: 10 }}>
          <div className="row"><ProviderLogo provider={det.provider!} /><strong className="grow">{det.label}</strong></div>
          <div className="small">{det.message}</div>
          {det.mac
            ? <MacMail email={email.trim()} onAdded={() => { setDet(null); setEmail(""); onAdded(); }} />
            : det.steps && <ol className="connect-steps small">{det.steps.map((x) => <li key={x}>{x}</li>)}</ol>}
          {det.mac && det.steps && (
            <details className="connect-more">
              <summary><ChevronRight size={12} /> Only want your Outlook calendar?</summary>
              <ol className="connect-steps small">{det.steps.map((x) => <li key={x}>{x}</li>)}</ol>
              <Link className="btn sm" to="/calendar">Open Calendar</Link>
            </details>
          )}
          {!det.mac && <div><Link className="btn sm" to="/calendar">Open Calendar</Link></div>}
        </div>
      )}
      {connected && (
        <div className="alert ok col" style={{ gap: 8 }}>
          <div className="row"><Check size={15} /><strong className="grow">{connected.email} is connected.</strong>
            <button type="button" className="btn ghost sm icon" aria-label="Close" onClick={() => setConnected(null)}><X size={12} /></button></div>
          {connected.calendar && (
            <>
              <div className="small">Want your calendar too? It takes three steps:</div>
              <ol className="connect-steps small">{connected.calendar.map((x) => <li key={x}>{x}</li>)}</ol>
              <div className="row wrap"><a className="btn sm" href="https://calendar.google.com/calendar/r/settings" target="_blank" rel="noreferrer">Open Google Calendar settings <ExternalLink size={11} /></a>
                <Link className="btn sm ghost" to="/calendar">Go to the Calendar page</Link></div>
            </>
          )}
        </div>
      )}
      {det && det.app_password && det.app_password_url && det.kind !== "one_click" && det.kind !== "unavailable" && (
        <div className="col" style={{ gap: 14 }}>
          <div className="small muted">{brand(det)} needs an <strong>app password</strong>: a separate password just for this app. About a minute.</div>
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <span className="step-num">1</span>
            <div className="col grow" style={{ gap: 6 }}>
              <a className="btn primary" style={{ alignSelf: "flex-start" }} href={det.app_password_url} target="_blank" rel="noreferrer">Open {owner(det)}'s app password page <ExternalLink size={13} /></a>
              {det.steps && <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{det.steps.map((x) => <li key={x}>{x.replace(/,? and paste it here\.?$/i, ".")}</li>)}</ul>}
            </div>
          </div>
          <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
            <span className="step-num">2</span>
            <div className="col grow" style={{ gap: 6 }}>
              <strong className="small">Paste the app password here</strong>
              <div className="row">
                <input className="input grow mono" required type="password" autoComplete="off" placeholder="Paste it here" value={form.password}
                  onChange={(e) => {
                    const v = e.target.value;
                    setForm({ ...form, password: v });
                    // a full app password (16 letters, any spaces or dashes) connects straight away
                    if (v.replace(/[\s-]/g, "").length === 16 && !busy) window.setTimeout(() => formRef.current?.requestSubmit(), 50);
                  }} />
                <button className="btn primary" disabled={busy || !form.password}>{busy ? <Loader2 size={15} className="spin" /> : "Connect"}</button>
              </div>
              {busy && <div className="small muted">Checking it with {brand(det)}…</div>}
            </div>
          </div>
          {error && <div className="alert err">{error}</div>}
          <details className="connect-more" open={showServers} onToggle={(e) => setShowServers((e.target as HTMLDetailsElement).open)}>
            <summary><ChevronRight size={12} /> More options</summary>
            <div className="col">
              <input className="input" placeholder="Your name on sent mail (optional)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              {det.caldav_url && <label className="check small"><input type="checkbox" checked={addCal} onChange={(e) => setAddCal(e.target.checked)} /> Also add my calendars</label>}
              {det.username_hint && <div className="muted small">{det.username_hint}</div>}
              <input className="input" placeholder="Username (defaults to your email)" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </div>
          </details>
        </div>
      )}
      {det && !(det.app_password && det.app_password_url) && det.kind !== "one_click" && det.kind !== "invalid" && det.kind !== "unavailable" && (
        <>
          {det.one_click_unavailable && <div className="small muted">{det.one_click_unavailable === "google" ? "Gmail" : NAMES[det.one_click_unavailable]} connects with an app password: a special password just for this app. It takes about a minute.</div>}
          <div className="small"><strong>{det.label}</strong>{det.source === "guess" ? " · best guess" : det.source === "ispdb" ? " · settings found automatically" : ""}</div>
          {det.steps && (
            <div className="small">
              {det.app_password ? <>This provider needs an <strong>app password</strong> (not your normal password):</> : null}
              <ol className="connect-steps">{det.steps.map((s) => <li key={s}>{s}</li>)}</ol>
              {det.app_password_url && <a href={det.app_password_url} target="_blank" rel="noreferrer">Open the app password page <ExternalLink size={11} /></a>}
            </div>
          )}
          <div className="row wrap">
            <input className="input grow" placeholder="Your name (shown on sent mail)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className="input grow" required type="password" autoComplete="off" placeholder={det.app_password ? "App password" : "Password"} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
          {det.caldav_url && <label className="check small"><input type="checkbox" checked={addCal} onChange={(e) => setAddCal(e.target.checked)} /> Also add my calendars ({det.caldav_url.replace("https://", "")})</label>}
          <details className="connect-more" open={showServers} onToggle={(e) => setShowServers((e.target as HTMLDetailsElement).open)}>
            <summary><ChevronRight size={12} /> Server settings{det.username_hint ? " and username" : ""}</summary>
            <div className="col">
              {det.username_hint && <div className="muted small">{det.username_hint}</div>}
              <input className="input" placeholder="Username (defaults to your email)" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
              <div className="row wrap">
                <input className="input grow" required placeholder="IMAP host" value={form.imap_host} onChange={(e) => setForm({ ...form, imap_host: e.target.value })} />
                <input className="input" style={{ width: 90 }} type="number" value={form.imap_port} onChange={(e) => setForm({ ...form, imap_port: Number(e.target.value) })} />
                <input className="input grow" required placeholder="SMTP host" value={form.smtp_host} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} />
                <input className="input" style={{ width: 90 }} type="number" value={form.smtp_port} onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })} />
                <select className="input" style={{ width: 120 }} value={form.smtp_security} onChange={(e) => setForm({ ...form, smtp_security: e.target.value as "ssl" | "starttls" })}><option value="ssl">SSL</option><option value="starttls">STARTTLS</option></select>
              </div>
            </div>
          </details>
          {error && <div className="alert err">{error}</div>}
          <div><button className="btn primary" disabled={busy || !form.password}>{busy ? <Loader2 size={15} className="spin" /> : "Test and connect"}</button></div>
        </>
      )}
      {toast.node}
    </form>
  );
}

function OwnRegistration({ providers, onSaved }: { providers: Record<Provider, ProviderInfo> | null; onSaved: () => void }) {
  const [p, setP] = useState<Provider>("google");
  const [form, setForm] = useState({ client_id: "", client_secret: "", tenant: "common" });
  const toast = useToast();
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api(`/connect/${p}/client`, { method: "PUT", body: p === "google" ? { client_id: form.client_id, client_secret: form.client_secret } : { client_id: form.client_id, tenant: form.tenant } });
      toast.ok(form.client_id ? `Saved. Connect ${NAMES[p]} now uses your registration.` : "Back to the built-in registration.");
      setForm({ client_id: "", client_secret: "", tenant: "common" });
      onSaved();
    } catch (err) { toast.err(err); }
  };
  const info = providers?.[p];
  return (
    <details className="connect-more" id="own-registration">
      <summary><ChevronRight size={12} /> Set up Google and Microsoft sign-in (one time, for whoever runs this app)</summary>
      <form className="col" onSubmit={save}>
        <div className="tabs">{(["google", "microsoft"] as Provider[]).map((k) => <button type="button" key={k} className={`tab ${p === k ? "active" : ""}`} onClick={() => setP(k)}>{NAMES[k]}</button>)}</div>
        <div className="muted small">
          {p === "google"
            ? "Create a Desktop app OAuth client in Google Cloud Console (APIs & Services > Credentials), enable the Gmail and Google Calendar APIs, and add yourself as a test user. Paste the client ID and its client secret."
            : "Register an app in the Microsoft Entra admin center (App registrations), add the Mobile and desktop platform with redirect URI http://localhost, and turn on Allow public client flows. No secret is needed."}
          {" "}Steps: <span className="mono">docs/connect-google-microsoft.md</span>.
          {info && <> Currently: {info.source === "none" ? "not set up" : `${info.source} (${info.client_id_hint})`}.</>}
        </div>
        <input className="input mono" placeholder={p === "google" ? "1234-abc.apps.googleusercontent.com" : "Application (client) ID"} value={form.client_id} onChange={(e) => setForm({ ...form, client_id: e.target.value })} />
        {p === "google" && <input className="input mono" type="password" autoComplete="off" placeholder="Client secret (GOCSPX-…)" value={form.client_secret} onChange={(e) => setForm({ ...form, client_secret: e.target.value })} />}
        {p === "microsoft" && <input className="input mono" placeholder="Tenant: common, consumers, organizations or your tenant ID" value={form.tenant} onChange={(e) => setForm({ ...form, tenant: e.target.value })} />}
        <div className="row"><button className="btn sm">{form.client_id ? "Save registration" : "Use the built-in one"}</button></div>
      </form>
      {toast.node}
    </details>
  );
}
