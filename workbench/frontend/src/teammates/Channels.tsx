// Talk to your teammates from Slack, Discord, Telegram, Matrix, Signal, WhatsApp or email, plus the phone link settings.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import "./teammates.css";

type Mate = { id: number; name: string };
type Status = {
  slack: { configured: boolean; running?: boolean; error?: string; allowed_users: string; teammate: number; steps: string[]; manifest: unknown };
  discord: { configured: boolean; running: boolean; bot?: string; error?: string; allowed_users: string; teammate: number; steps: string[]; invite_url: string };
  telegram: { configured: boolean; running: boolean; bot?: string; error?: string; allowed_users?: string; teammate: number; steps: string[] };
  matrix: More & { homeserver: string };
  signal: More & { api_url: string; number: string };
  whatsapp: More & { phone_number_id: string; webhook_path: string };
  email: More & { account_id: string; subject_tag: string };
};
type More = { configured: boolean; running: boolean; error?: string; account?: string; allowed_users: string; teammate: number; steps: string[] };
type Field = { key: string; label: string; secret?: boolean; initial?: string; placeholder?: string };

/** Matrix, Signal, WhatsApp, email: the same card, its fields from the list. Secrets are only sent when typed. */
function MoreChannel({ name, title, st, fields, test, pickMate, save, children }: {
  name: string; title: string; st: More; fields: Field[]; test?: boolean; children?: React.ReactNode;
  pickMate: (v: number, f: (n: number) => void) => React.ReactNode; save: (path: string, body: unknown, method?: string) => Promise<void>;
}) {
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, f.secret ? "" : f.initial ?? ""])));
  const body = () => Object.fromEntries(fields.filter((f) => !f.secret || v[f.key]).map((f) => [f.key, v[f.key]]));
  return <section className="tm-card wide">
    <div className="tm-card-head"><b>{title}</b><span className="tm-pill">{st.running ? `Connected${st.account ? ` as ${st.account}` : ""}` : st.configured ? "Set up" : "Not set up"}</span></div>
    <Wizard steps={st.steps}>
      {fields.map((f) => <label key={f.key}>{f.label}<input type={f.secret ? "password" : "text"} autoComplete="off" placeholder={f.placeholder}
        value={v[f.key]} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} /></label>)}
      {children}
      {pickMate(st.teammate, (n) => save(`/channels/${name}`, { teammate_id: n }))}
      {st.error && <p className="tm-err">{st.error}</p>}
      <div className="tm-row"><button className="btn primary sm" onClick={() => save(`/channels/${name}`, body())}>Save</button>
        {test && <button className="btn ghost sm" onClick={() => save(`/channels/${name}/test`, {}, "POST")}>Test connection</button>}</div>
    </Wizard>
  </section>;
}
type Link = { computer_id: string; relay_url: string; relay: { connected?: boolean; error?: string }; linked_phones: number; lan: string[]; shared_folders: string[] };

function Wizard({ steps, children }: { steps: string[]; children: React.ReactNode }) {
  const [i, setI] = useState(0);
  return <div className="tm-wizard">
    <ol>{steps.map((s, n) => <li key={n} className={n === i ? "on" : n < i ? "done" : ""} onClick={() => setI(n)}>{s}</li>)}</ol>
    <div className="tm-row"><button className="btn ghost sm" disabled={i === 0} onClick={() => setI(i - 1)}>Back</button>
      <button className="btn ghost sm" disabled={i >= steps.length - 1} onClick={() => setI(i + 1)}>Next step</button></div>
    {children}
  </div>;
}

export default function Channels() {
  const [st, setSt] = useState<Status | null>(null);
  const [link, setLink] = useState<Link | null>(null);
  const [mates, setMates] = useState<Mate[]>([]);
  const [slack, setSlack] = useState({ bot_token: "", app_token: "", allowed_users: "" });
  const [tg, setTg] = useState({ bot_token: "", allowed_users: "" });
  const [dc, setDc] = useState({ bot_token: "", allowed_users: "" });
  const [folders, setFolders] = useState("");
  const [relay, setRelay] = useState("");
  const [msg, setMsg] = useState("");
  const [mailAccounts, setMailAccounts] = useState<{ id: number; email: string }[]>([]);
  useEffect(() => { api<{ id: number; email: string }[]>("/mail/accounts").then(setMailAccounts).catch(() => undefined); }, []);
  const load = useCallback(async () => {
    const [s, l, m] = await Promise.all([api<Status>("/channels"), api<Link>("/link"), api<{ teammates: Mate[] }>("/teammates")]);
    setSt(s); setLink(l); setMates(m.teammates); setFolders(l.shared_folders.join("\n")); setRelay(l.relay_url);
    setSlack((x) => ({ ...x, allowed_users: s.slack.allowed_users })); setTg((x) => ({ ...x, allowed_users: s.telegram.allowed_users ?? "" }));
    setDc((x) => ({ ...x, allowed_users: s.discord.allowed_users }));
  }, []);
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [load]);
  const save = async (path: string, body: unknown, method = "PUT") => {
    setMsg("");
    try { await api(path, { method, body }); setMsg("Saved."); load(); } catch (e) { setMsg((e as Error).message); }
  };
  if (!st || !link) return <p className="tm-empty">{msg || "Loading…"}</p>;
  const pickMate = (v: number, f: (n: number) => void) => <label>Who answers<select value={v} onChange={(e) => f(Number(e.target.value))}>
    {mates.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>;
  return <div className="tm-page">
    <header><h1>Channels &amp; phone</h1><p className="tm-muted">Message your teammates from Slack, Discord, Telegram, Matrix, Signal, WhatsApp or email. They answer there, but anything that
      sends or changes things waits for you in the app.</p></header>
    {msg && <p className="tm-muted" role="status">{msg}</p>}
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Slack</b><span className="tm-pill">{st.slack.configured ? (st.slack.running ? "Connected" : "Set up") : "Not set up"}</span></div>
      <Wizard steps={st.slack.steps}>
        <details><summary>App manifest</summary><pre className="tm-pre">{JSON.stringify(st.slack.manifest, null, 2)}</pre>
          <button className="btn ghost sm" onClick={() => navigator.clipboard.writeText(JSON.stringify(st.slack.manifest, null, 2))}>Copy manifest</button></details>
        <label>Bot token (xoxb-…)<input type="password" autoComplete="off" value={slack.bot_token} onChange={(e) => setSlack({ ...slack, bot_token: e.target.value })} /></label>
        <label>App-level token (xapp-…)<input type="password" autoComplete="off" value={slack.app_token} onChange={(e) => setSlack({ ...slack, app_token: e.target.value })} /></label>
        <label>Your Slack member IDs (comma separated)<input value={slack.allowed_users} onChange={(e) => setSlack({ ...slack, allowed_users: e.target.value })} /></label>
        {pickMate(st.slack.teammate, (n) => save("/channels/slack", { teammate_id: n }))}
        {st.slack.error && <p className="tm-err">{st.slack.error}</p>}
        <div className="tm-row"><button className="btn primary sm" onClick={() => save("/channels/slack", {
          ...(slack.bot_token ? { bot_token: slack.bot_token } : {}), ...(slack.app_token ? { app_token: slack.app_token } : {}), allowed_users: slack.allowed_users })}>Save</button>
          <button className="btn ghost sm" onClick={() => save("/channels/slack/test", {}, "POST")}>Test connection</button></div>
      </Wizard>
    </section>
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Discord</b><span className="tm-pill">{st.discord.running ? `Connected${st.discord.bot ? ` as ${st.discord.bot}` : ""}` : st.discord.configured ? "Set up" : "Not set up"}</span></div>
      <Wizard steps={st.discord.steps}>
        <label>Bot token<input type="password" autoComplete="off" value={dc.bot_token} onChange={(e) => setDc({ ...dc, bot_token: e.target.value })} /></label>
        <label>Your Discord user IDs (comma separated)<input value={dc.allowed_users} onChange={(e) => setDc({ ...dc, allowed_users: e.target.value })} /></label>
        {pickMate(st.discord.teammate, (n) => save("/channels/discord", { teammate_id: n }))}
        {st.discord.invite_url && <p className="tm-muted"><a href={st.discord.invite_url} target="_blank" rel="noreferrer">Invite the bot to a server</a> (DMs work without one). It answers DMs and messages that @mention it.</p>}
        {st.discord.error && <p className="tm-err">{st.discord.error}</p>}
        <div className="tm-row"><button className="btn primary sm" onClick={() => save("/channels/discord", { ...(dc.bot_token ? { bot_token: dc.bot_token } : {}), allowed_users: dc.allowed_users })}>Save</button>
          <button className="btn ghost sm" onClick={() => save("/channels/discord/test", {}, "POST")}>Test connection</button></div>
      </Wizard>
    </section>
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Telegram</b><span className="tm-pill">{st.telegram.running ? `Running${st.telegram.bot ? ` as @${st.telegram.bot}` : ""}` : st.telegram.configured ? "Set up" : "Not set up"}</span></div>
      <Wizard steps={st.telegram.steps}>
        <label>Bot token<input type="password" autoComplete="off" value={tg.bot_token} onChange={(e) => setTg({ ...tg, bot_token: e.target.value })} /></label>
        <label>Your Telegram user IDs<input value={tg.allowed_users} onChange={(e) => setTg({ ...tg, allowed_users: e.target.value })} /></label>
        {pickMate(st.telegram.teammate, (n) => save("/channels/telegram", { teammate_id: n }))}
        {st.telegram.error && <p className="tm-err">{st.telegram.error}</p>}
        <button className="btn primary sm" onClick={() => save("/channels/telegram", { ...(tg.bot_token ? { bot_token: tg.bot_token } : {}), allowed_users: tg.allowed_users })}>Save</button>
      </Wizard>
    </section>
    <MoreChannel name="matrix" title="Matrix" st={st.matrix} test pickMate={pickMate} save={save} fields={[
      { key: "homeserver", label: "Homeserver URL", initial: st.matrix.homeserver, placeholder: "https://matrix.org" },
      { key: "access_token", label: "Bot access token", secret: true },
      { key: "allowed_users", label: "Matrix IDs allowed to ask (comma separated)", initial: st.matrix.allowed_users, placeholder: "@you:matrix.org" }]}>
      <p className="tm-muted">Unencrypted rooms and direct chats only: end-to-end encrypted rooms can't be read yet.</p>
    </MoreChannel>
    <MoreChannel name="signal" title="Signal" st={st.signal} test pickMate={pickMate} save={save} fields={[
      { key: "api_url", label: "signal-cli-rest-api URL", initial: st.signal.api_url, placeholder: "http://localhost:8080" },
      { key: "number", label: "Your Signal number", initial: st.signal.number, placeholder: "+61412345678" },
      { key: "allowed_users", label: "Numbers allowed to ask (comma separated)", initial: st.signal.allowed_users }]} />
    <MoreChannel name="whatsapp" title="WhatsApp" st={st.whatsapp} pickMate={pickMate} save={save} fields={[
      { key: "phone_number_id", label: "Phone number ID", initial: st.whatsapp.phone_number_id },
      { key: "access_token", label: "Access token", secret: true },
      { key: "app_secret", label: "App secret", secret: true },
      { key: "verify_token", label: "Verify token (any text you choose)", secret: true },
      { key: "allowed_users", label: "WhatsApp numbers allowed to ask (comma separated)", initial: st.whatsapp.allowed_users }]}>
      <p className="tm-muted">Meta delivers WhatsApp messages only to a public HTTPS address: set the webhook to <code>https://your-address{st.whatsapp.webhook_path}</code>.</p>
    </MoreChannel>
    <MoreChannel name="email" title="Email" st={st.email} pickMate={pickMate} save={save} fields={[
      { key: "allowed_users", label: "Addresses allowed to ask (comma separated)", initial: st.email.allowed_users },
      { key: "subject_tag", label: "Subject starts with", initial: st.email.subject_tag }]}>
      <label>Mail account<select defaultValue={st.email.account_id} onChange={(e) => save("/channels/email", { account_id: Number(e.target.value) })}>
        <option value="">Choose…</option>{mailAccounts.map((a) => <option key={a.id} value={a.id}>{a.email}</option>)}</select></label>
    </MoreChannel>
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Phone link</b><span className="tm-pill">{link.linked_phones} phone{link.linked_phones === 1 ? "" : "s"} linked</span></div>
      <p className="tm-muted">End-to-end encrypted. Your phone talks to this computer over Wi-Fi first, and through the relay when you're away. The relay only ever sees sealed envelopes.</p>
      <p className="tm-muted">Computer ID <code>{link.computer_id}</code>{link.lan.length ? <> · On this network at <code>{link.lan[0]}</code></> : null}</p>
      <label>Relay address (https)<input value={relay} placeholder="https://relay.example.com" onChange={(e) => setRelay(e.target.value)} /></label>
      {link.relay.error && <p className="tm-err">{link.relay.error}</p>}
      <label>Folders your phone can browse (one per line, read-only)<textarea rows={3} value={folders} onChange={(e) => setFolders(e.target.value)} /></label>
      <button className="btn primary sm" onClick={() => save("/link", { relay_url: relay, shared_folders: folders.split("\n").map((x) => x.trim()).filter(Boolean) })}>Save</button>
      <p className="tm-muted">To link a phone, open the Phone page and scan the pairing code with the AI Workspace app.</p>
    </section>
  </div>;
}
