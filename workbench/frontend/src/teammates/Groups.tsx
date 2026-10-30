// feat/grok-style: group chats. @mention a teammate to bring it in; teammates can hand off with @Name.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Avatar, type Teammate } from "./Teammates";
import "./teammates.css";

type GMsg = { id: number; author_id: number | null; content: string; handoff_from: number | null; created_at: string; reactions: string[] };
type Group = { id: number; name: string; members: Teammate[]; working: number[] };
const REACTIONS = ["👍", "❤️", "😂", "‼️", "❓", "👀"];

function NewGroup() {
  const nav = useNavigate();
  const [mates, setMates] = useState<Teammate[]>([]);
  const [pick, setPick] = useState<number[]>([]);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api<{ teammates: Teammate[] }>("/teammates").then((r) => setMates(r.teammates)); }, []);
  return <div className="tm-new">
    <h2>New group chat</h2><p className="tm-muted">Pick two or more teammates. Mention one with @Name to ask it directly.</p>
    <label>Name<input value={name} placeholder="e.g. Kitchen reno" onChange={(e) => setName(e.target.value)} /></label>
    <div className="tm-templates">{mates.map((m) => <button key={m.id} className={`tm-template${pick.includes(m.id) ? " on" : ""}`}
      aria-pressed={pick.includes(m.id)} onClick={() => setPick(pick.includes(m.id) ? pick.filter((x) => x !== m.id) : [...pick, m.id])}>
      <Avatar t={m} /><b>{m.name}</b><span className="tm-muted">{m.role}</span></button>)}</div>
    {err && <p className="tm-err">{err}</p>}
    <button className="btn primary" disabled={pick.length < 2} onClick={async () => {
      try { const g = await api<Group>("/groups", { body: { name, members: pick } }); nav(`/groups/${g.id}`); } catch (e) { setErr((e as Error).message); }
    }}>Start group</button>
  </div>;
}

export function mentionQuery(text: string, caret: number): string | null {
  const m = /@([\w ]{0,30})$/.exec(text.slice(0, caret));
  return m ? m[1] : null;
}

export default function Groups() {
  const { id } = useParams();
  const nav = useNavigate();
  const gid = id && id !== "new" ? Number(id) : null;
  const [group, setGroup] = useState<Group | null>(null);
  const [msgs, setMsgs] = useState<GMsg[]>([]);
  const [text, setText] = useState("");
  const [caret, setCaret] = useState(0);
  const [err, setErr] = useState("");
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!gid) return;
    try { const r = await api<{ group: Group; messages: GMsg[] }>(`/groups/${gid}`); setGroup(r.group); setMsgs(r.messages); } catch (e) { setErr((e as Error).message); }
  }, [gid]);
  useEffect(() => { load(); const t = setInterval(load, 2500); return () => clearInterval(t); }, [load]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length]);
  if (id === "new") return <div className="tm-shell"><nav className="tm-side"><button className="btn ghost sm" onClick={() => nav("/teammates")}>← Teammates</button></nav><main className="tm-chat"><NewGroup /></main></div>;
  const byId = new Map((group?.members ?? []).map((m) => [m.id, m]));
  const q = mentionQuery(text, caret);
  const suggestions = q === null ? [] : (group?.members ?? []).filter((m) => m.name.toLowerCase().startsWith(q.toLowerCase())).slice(0, 5);
  const post = async () => {
    if (!text.trim() || !gid) return;
    const t = text; setText("");
    try { await api(`/groups/${gid}/messages`, { body: { content: t } }); load(); } catch (e) { setErr((e as Error).message); setText(t); }
  };
  const react = async (m: GMsg, emoji: string) => {
    const on = !m.reactions.includes(emoji);
    await api("/reactions", { body: { scope: "group", ref: m.id, emoji, on } }); load();
  };
  return <div className="tm-shell">
    <nav className="tm-side" aria-label="Group members">
      <button className="btn ghost sm" onClick={() => nav("/teammates")}>← Teammates</button>
      <h2>{group?.name ?? "Group"}</h2>
      {group?.members.map((m) => <div key={m.id} className="tm-person"><Avatar t={m} /><span><b>{m.name}</b>
        <small>{group.working.includes(m.id) ? "Working…" : m.role}</small></span></div>)}
      <p className="tm-muted">In groups, teammates can't send anything or act outside the app.</p>
    </nav>
    <main className="tm-chat">
      <div className="tm-msgs" aria-live="polite">
        {!msgs.length && <p className="tm-empty">Say hello, or @mention a teammate to ask it something.</p>}
        {msgs.map((m) => { const who = m.author_id ? byId.get(m.author_id) : null; return <div key={m.id} className={`tm-msg ${who ? "assistant" : "user"}`}>
          {who && <small className="tm-muted"><Avatar t={who} size={18} /> {who.name}{m.handoff_from ? ` (asked by ${byId.get(m.handoff_from)?.name ?? "a teammate"})` : ""}</small>}
          <div className="tm-bubble">{m.content}</div>
          <div className="tm-reacts">{REACTIONS.map((r) => <button key={r} className={`tm-react${m.reactions.includes(r) ? " on" : ""}`}
            aria-pressed={m.reactions.includes(r)} aria-label={`React ${r}`} onClick={() => react(m, r)}>{r}</button>)}</div>
        </div>; })}
        {group?.working.map((w) => <p key={w} className="tm-muted">{byId.get(w)?.name} is typing…</p>)}
        <div ref={end} />
      </div>
      {err && <p className="tm-err">{err}</p>}
      <form className="tm-compose" onSubmit={(e) => { e.preventDefault(); post(); }}>
        {!!suggestions.length && <div className="tm-mentions" role="listbox">{suggestions.map((s) => <button type="button" role="option" aria-selected="false" key={s.id}
          onClick={() => { const before = text.slice(0, caret).replace(/@[\w ]{0,30}$/, `@${s.name} `); setText(before + text.slice(caret)); setCaret(before.length); }}>
          <Avatar t={s} size={18} /> {s.name}</button>)}</div>}
        <textarea rows={2} value={text} placeholder="Message the group. Use @ to mention a teammate"
          onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); post(); } }} />
        <button className="btn primary" disabled={!text.trim()}>Send</button>
      </form>
    </main>
  </div>;
}
