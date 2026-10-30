// feat/grok-style: talk to your teammates from Slack or Telegram, plus the phone link settings.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import "./teammates.css";

type Mate = { id: number; name: string };
type Status = {
  slack: { configured: boolean; connected?: boolean; error?: string; allowed_users: string; teammate: number; steps: string[]; manifest: unknown };
  telegram: { configured: boolean; running: boolean; bot?: string; error?: string; allowed_users?: string; teammate: number; steps: string[] };
  whatsapp: { available: boolean; why: string };
};
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
  const [folders, setFolders] = useState("");
  const [relay, setRelay] = useState("");
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    const [s, l, m] = await Promise.all([api<Status>("/channels"), api<Link>("/link"), api<{ teammates: Mate[] }>("/teammates")]);
    setSt(s); setLink(l); setMates(m.teammates); setFolders(l.shared_folders.join("\n")); setRelay(l.relay_url);
    setSlack((x) => ({ ...x, allowed_users: s.slack.allowed_users })); setTg((x) => ({ ...x, allowed_users: s.telegram.allowed_users ?? "" }));
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
    <header><h1>Channels &amp; phone</h1><p className="tm-muted">Message your teammates from Slack or Telegram. They answer there, but anything that
      sends or changes things waits for you in the app.</p></header>
    {msg && <p className="tm-muted" role="status">{msg}</p>}
    <section className="tm-card wide">
      <div className="tm-card-head"><b>Slack</b><span className="tm-pill">{st.slack.configured ? (st.slack.connected ? "Connected" : "Set up") : "Not set up"}</span></div>
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
      <div className="tm-card-head"><b>Telegram</b><span className="tm-pill">{st.telegram.running ? `Running${st.telegram.bot ? ` as @${st.telegram.bot}` : ""}` : st.telegram.configured ? "Set up" : "Not set up"}</span></div>
      <Wizard steps={st.telegram.steps}>
        <label>Bot token<input type="password" autoComplete="off" value={tg.bot_token} onChange={(e) => setTg({ ...tg, bot_token: e.target.value })} /></label>
        <label>Your Telegram user IDs<input value={tg.allowed_users} onChange={(e) => setTg({ ...tg, allowed_users: e.target.value })} /></label>
        {pickMate(st.telegram.teammate, (n) => save("/channels/telegram", { teammate_id: n }))}
        {st.telegram.error && <p className="tm-err">{st.telegram.error}</p>}
        <button className="btn primary sm" onClick={() => save("/channels/telegram", { ...(tg.bot_token ? { bot_token: tg.bot_token } : {}), allowed_users: tg.allowed_users })}>Save</button>
      </Wizard>
    </section>
    <section className="tm-card wide"><div className="tm-card-head"><b>WhatsApp</b><span className="tm-pill">Not available yet</span></div><p className="tm-muted">{st.whatsapp.why}</p></section>
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
