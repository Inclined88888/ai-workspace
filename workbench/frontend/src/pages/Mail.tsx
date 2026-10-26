import { ArrowLeft, Inbox, Loader2, Mail, PenSquare, RefreshCw, Reply, Search, Send, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, stream } from "../api";
import { Empty, ModelSelect, relTime, useModels, useToast } from "../util";

type Account = { id: number; name: string; email: string };
type Summary = { uid: string; from: string; subject: string; date: string; unread: boolean };
type Message = Summary & { reply_to: string; to: string; cc: string; message_id: string; body: string };
type Draft = { id: number; account_id: number | null; to_addr: string; subject: string; body: string; in_reply_to: string; updated_at: string };
type Compose = { to: string; subject: string; body: string; in_reply_to: string; draft_id?: number; original?: string };

function Composer({ account, initial, onClose, onSent }: { account: Account; initial: Compose; onClose: () => void; onSent: () => void }) {
  const [c, setC] = useState(initial);
  const [intent, setIntent] = useState("");
  const [model, setModel] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [sending, setSending] = useState(false);
  const { models } = useModels();
  const toast = useToast();
  const abort = useRef<AbortController | null>(null);

  const draft = async () => {
    setDrafting(true);
    abort.current = new AbortController();
    let out = "";
    await stream("/mail/draft-reply", { message: c.original || `To: ${c.to}\nSubject: ${c.subject}`, intent, model: model || null }, (e) => {
      if (e.error) toast.err(String(e.error));
      if (e.delta) { out += e.delta; setC((x) => ({ ...x, body: out.replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trimStart() })); }
    }, abort.current.signal).catch(() => undefined);
    setDrafting(false);
  };

  const send = async () => {
    if (!confirm(`Send this email to ${c.to}?`)) return;
    setSending(true);
    try {
      await api(`/mail/${account.id}/send`, { body: { to: c.to, subject: c.subject, body: c.body, in_reply_to: c.in_reply_to, draft_id: c.draft_id ?? null } });
      toast.ok("Sent");
      onSent();
    } catch (e) {
      toast.err(e);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="card col" style={{ margin: 20 }}>
      <div className="row"><h3 className="grow">{c.in_reply_to ? "Reply" : "New message"} <span className="faint small">from {account.email}</span></h3><button className="btn ghost sm icon" onClick={onClose}><X size={15} /></button></div>
      <input className="input" placeholder="To" value={c.to} onChange={(e) => setC({ ...c, to: e.target.value })} />
      <input className="input" placeholder="Subject" value={c.subject} onChange={(e) => setC({ ...c, subject: e.target.value })} />
      <div className="row wrap">
        <Sparkles size={15} className="prio-1" />
        <input className="input grow" style={{ minWidth: 180 }} placeholder="What should it say? e.g. 'accept, suggest Thursday 2pm'" value={intent} onChange={(e) => setIntent(e.target.value)} />
        <div style={{ width: 170 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
        <button className="btn" onClick={draft} disabled={drafting}>{drafting ? <Loader2 size={15} className="spin" /> : "Draft with AI"}</button>
      </div>
      <textarea className="input" rows={12} value={c.body} onChange={(e) => setC({ ...c, body: e.target.value })} placeholder="Write your message" />
      <div className="row">
        <span className="muted small">AI drafts can contain [placeholders]. Read before sending.</span>
        <div className="spacer" />
        <button className="btn primary" disabled={sending || !c.to.trim() || !c.body.trim() || drafting} onClick={send}>{sending ? <Loader2 size={15} className="spin" /> : <Send size={15} />} Send</button>
      </div>
      {toast.node}
    </div>
  );
}

export default function MailPage() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [view, setView] = useState<"inbox" | "drafts">("inbox");
  const [list, setList] = useState<Summary[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<Message | null>(null);
  const [compose, setCompose] = useState<Compose | null>(null);
  const toast = useToast();

  useEffect(() => {
    api<Account[]>("/mail/accounts").then((a) => { setAccounts(a); setAccount(a[0] ?? null); }).catch(toast.err);
  }, []);
  const load = async () => {
    if (!account) return;
    setLoading(true);
    try {
      if (view === "inbox") setList(await api<Summary[]>(`/mail/${account.id}/messages?q=${encodeURIComponent(q)}`));
      else setDrafts(await api<Draft[]>("/mail/drafts"));
    } catch (e) {
      toast.err(e);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, [account, view]);

  const open = async (uid: string) => {
    setCompose(null);
    try { setMsg(await api<Message>(`/mail/${account!.id}/messages/${uid}`)); } catch (e) { toast.err(e); }
  };
  const reply = () => {
    if (!msg) return;
    const quoted = msg.body.split("\n").slice(0, 60).map((l) => `> ${l}`).join("\n");
    setCompose({
      to: msg.reply_to, subject: msg.subject.match(/^re:/i) ? msg.subject : `Re: ${msg.subject}`, in_reply_to: msg.message_id,
      body: `\n\nOn ${msg.date}, ${msg.from} wrote:\n${quoted}`, original: `From: ${msg.from}\nSubject: ${msg.subject}\n\n${msg.body.slice(0, 20000)}`,
    });
  };

  if (accounts === null) return null;
  if (!account) {
    return (
      <div className="page">
        <Empty icon={<Mail size={28} />} title="No mail account connected" hint="Add an IMAP/SMTP account in Settings. Gmail and Outlook need an app password." />
        <div className="row" style={{ justifyContent: "center" }}><Link className="btn primary" to="/settings#mail">Connect mail</Link></div>
      </div>
    );
  }

  const selected = !!(msg || compose);
  return (
    <div className={`split ${selected ? "has-selection" : ""}`} style={{ gridTemplateColumns: "360px 1fr" }}>
      <div className="split-list">
        <div className="col" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <div className="row">
            {accounts.length > 1 ? (
              <select className="input grow" value={account.id} onChange={(e) => setAccount(accounts.find((a) => a.id === Number(e.target.value))!)}>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.email}</option>)}
              </select>
            ) : <strong className="grow truncate">{account.email}</strong>}
            <button className="btn ghost sm icon" onClick={load} title="Refresh">{loading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}</button>
            <button className="btn primary sm" onClick={() => { setMsg(null); setCompose({ to: "", subject: "", body: "", in_reply_to: "" }); }}><PenSquare size={14} /></button>
          </div>
          <div className="tabs">
            <button className={`tab ${view === "inbox" ? "active" : ""}`} onClick={() => setView("inbox")}><Inbox size={13} /> Inbox</button>
            <button className={`tab ${view === "drafts" ? "active" : ""}`} onClick={() => setView("drafts")}>Drafts</button>
          </div>
          {view === "inbox" && (
            <form className="row" onSubmit={(e) => { e.preventDefault(); load(); }} style={{ position: "relative" }}>
              <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} />
              <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail, press Enter" />
            </form>
          )}
        </div>
        <div className="scroll">
          {view === "inbox" && !loading && list.length === 0 && <Empty icon={<Inbox size={22} />} title="No messages" />}
          {view === "inbox" && list.map((m) => (
            <div key={m.uid} className={`list-item ${msg?.uid === m.uid ? "active" : ""}`} onClick={() => open(m.uid)} style={{ alignItems: "flex-start" }}>
              {m.unread ? <span className="dot" style={{ background: "var(--accent)", marginTop: 7 }} /> : <span className="dot" />}
              <div className="grow">
                <div className="row"><span className={`truncate grow ${m.unread ? "" : "muted"}`} style={{ fontWeight: m.unread ? 600 : 500 }}>{m.from.replace(/<.*>/, "").trim() || m.from}</span><span className="faint small">{m.date ? relTime(m.date) : ""}</span></div>
                <div className="truncate small">{m.subject}</div>
              </div>
            </div>
          ))}
          {view === "drafts" && drafts.length === 0 && <Empty icon={<PenSquare size={22} />} title="No drafts" hint="Agents in Chat save drafts here. They never send on their own." />}
          {view === "drafts" && drafts.map((d) => (
            <div key={d.id} className="list-item" style={{ alignItems: "flex-start" }} onClick={() => { setMsg(null); setCompose({ to: d.to_addr, subject: d.subject, body: d.body, in_reply_to: d.in_reply_to, draft_id: d.id }); }}>
              <div className="grow">
                <div className="truncate"><strong>{d.subject || "(no subject)"}</strong></div>
                <div className="muted small truncate">To {d.to_addr} · {relTime(d.updated_at)}</div>
              </div>
              <button className="btn ghost sm icon" onClick={async (e) => { e.stopPropagation(); await api(`/mail/drafts/${d.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main">
        {compose ? (
          <Composer key={compose.draft_id ?? compose.in_reply_to ?? "new"} account={account} initial={compose} onClose={() => setCompose(null)} onSent={() => { setCompose(null); load(); }} />
        ) : msg ? (
          <div style={{ padding: "20px 28px", maxWidth: 900 }}>
            <div className="row" style={{ marginBottom: 12 }}>
              <button className="btn ghost sm icon mobile-only" onClick={() => setMsg(null)}><ArrowLeft size={15} /></button>
              <h2 className="grow">{msg.subject || "(no subject)"}</h2>
              <button className="btn primary sm" onClick={reply}><Reply size={14} /> Reply</button>
            </div>
            <div className="muted small">From {msg.from}</div>
            <div className="muted small">To {msg.to}{msg.cc ? ` · Cc ${msg.cc}` : ""}</div>
            <div className="faint small" style={{ marginBottom: 16 }}>{msg.date}</div>
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{msg.body || <span className="muted">(No text content)</span>}</div>
          </div>
        ) : (
          <Empty icon={<Mail size={28} />} title="Select a message" />
        )}
      </div>
      {toast.node}
    </div>
  );
}
