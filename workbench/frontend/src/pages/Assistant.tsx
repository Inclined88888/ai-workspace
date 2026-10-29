import { Brain, Check, Copy, KeyRound, Loader2, MessageSquare, Plus, Search, Send, ShieldAlert, Sparkles, Trash2, Wrench } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, stream } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Conv = { id: number; title: string; channel: string; updated_at: string; model: string };
type Msg = { id: number; role: string; content: string; tool_calls: string[]; name: string };
type Step = { kind: "call" | "result" | "check"; name: string; text: string; ok?: boolean };
type Approval = { id: number; tool: string; args: string; effect: string; reason: string };
type Memory = { id: number; content: string; kind: string; source: string; updated_at: string };
type Skill = { id: number; name: string; description: string; steps: string; uses: number; source: string; enabled: number };

const CHANNEL: Record<string, string> = { web: "Web", cli: "Terminal", telegram: "Telegram", schedule: "Schedule" };

function Chat({ tabs }: { tabs: React.ReactNode }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("");
  const { models } = useModels();
  const endRef = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const cid = id ? Number(id) : null;

  const loadConvs = () => api<Conv[]>(`/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setConvs).catch(toast.err);
  useEffect(() => { const t = setTimeout(loadConvs, 200); return () => clearTimeout(t); }, [q]);
  const loadConv = (id: number) => {
    api<{ messages: Msg[]; model: string }>(`/conversations/${id}`).then((c) => { setMessages(c.messages); setModel(c.model || ""); }).catch(toast.err);
    api<Approval[]>(`/approvals?conversation_id=${id}`).then(setApprovals).catch(toast.err);
  };
  useEffect(() => {
    setSteps([]);
    setApprovals([]);
    if (!cid) return setMessages([]);
    loadConv(cid);
  }, [cid]);

  const decide = async (a: Approval, approve: boolean) => {
    try {
      const r = await api<{ status: string; result: string }>(`/approvals/${a.id}`, { method: "POST", body: { approve } });
      setApprovals((xs) => xs.filter((x) => x.id !== a.id));
      if (r.status === "failed") toast.err(r.result);
      if (cid) loadConv(cid);
    } catch (e) { toast.err(e); }
  };
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, steps]);

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const msg = text.trim();
    if (!msg || busy) return;
    setText("");
    setBusy(true);
    setSteps([]);
    setMessages((m) => [...m, { id: Date.now(), role: "user", content: msg, tool_calls: [], name: "" }]);
    let newId = cid;
    await stream("/assistant/chat", { message: msg, conversation_id: cid, model: model || null }, (ev) => {
      const t = ev.type as string | undefined;
      if (t === "start") newId = Number(ev.conversation_id);
      if (t === "tool_call") setSteps((s) => [...s, { kind: "call", name: String(ev.name), text: String(ev.args) }]);
      if (t === "tool_result") {
        const r = (ev.receipt ?? {}) as { verified?: boolean | null; evidence?: string };
        // Show what was confirmed in the database when there is a check; otherwise the raw result
        const text = r.verified === true ? `verified: ${r.evidence}` : r.verified === false ? `check failed: ${r.evidence}` : String(ev.preview);
        setSteps((s) => [...s, { kind: "result", name: String(ev.name), text, ok: Boolean(ev.ok) }]);
      }
      if (t === "claim_check") setSteps((s) => [...s, { kind: "check", name: "", ok: false,
        text: ev.retrying ? `Answer said "${ev.claim}" but nothing confirms it. Asking the model to act or correct it.` : `Still unconfirmed: "${ev.claim}"` }]);
      if (t === "approval_required") setApprovals((a) => [...a, { id: Number(ev.id), tool: String(ev.tool), args: String(ev.args), effect: String(ev.effect), reason: String(ev.reason) }]);
      if (t === "message") setMessages((m) => [...m, { id: Date.now() + 1, role: "assistant", content: String(ev.content), tool_calls: [], name: "" }]);
      if (t === "error" || (!t && ev.error)) toast.err(String(ev.error));
    });
    setBusy(false);
    loadConvs();
    if (newId && newId !== cid) navigate(`/assistant/${newId}`, { replace: true });
  };

  const remove = async (c: Conv) => {
    if (!confirm(`Delete "${c.title}"? Memories learned from it are kept.`)) return;
    await api(`/conversations/${c.id}`, { method: "DELETE" });
    if (c.id === cid) navigate("/assistant");
    loadConvs();
  };

  const visible = messages.filter((m) => (m.role === "user" && m.name !== "claim_check") || (m.role === "assistant" && (m.content || m.tool_calls.length)));
  return (
    <div className={`split ${cid !== null ? "has-selection" : ""}`} style={{ height: "calc(100vh - 0px)" }}>
      <div className="split-list">
        <div className="col" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <div className="row"><h2 className="grow">Assistant</h2><button className="btn primary sm" onClick={() => navigate("/assistant")}><Plus size={14} /> New</button></div>
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} />
            <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search all conversations" />
          </div>
        </div>
        <div className="scroll">
          {convs.length === 0 && <Empty icon={<MessageSquare size={22} />} title={q ? "No matches" : "No conversations yet"} />}
          {convs.map((c) => (
            <div key={c.id} className={`list-item ${c.id === cid ? "active" : ""}`} onClick={() => navigate(`/assistant/${c.id}`)}>
              <div className="grow">
                <div className="truncate">{c.title}</div>
                <div className="faint small">{CHANNEL[c.channel] ?? c.channel} · {relTime(c.updated_at)}</div>
              </div>
              <button className="btn ghost sm icon" onClick={(e) => { e.stopPropagation(); remove(c); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main" style={{ display: "flex", flexDirection: "column" }}>
        <div className="toolbar">
          <div style={{ width: 240 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
          <div className="spacer" />
          {tabs}
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
          <div style={{ maxWidth: 820, margin: "0 auto" }} className="col gap-lg">
            {visible.length === 0 && !busy && (
              <Empty icon={<Sparkles size={26} />} title="What can I do for you?"
                hint="I remember what matters across conversations, use your notes, tasks, calendar and mail, search the web, and can schedule things like a daily report." />
            )}
            {visible.map((m) => m.role === "user"
              ? <div key={m.id} className="bubble-user" style={{ alignSelf: "flex-end", maxWidth: "85%" }}>{m.content}</div>
              : <div key={m.id} className="col" style={{ gap: 4 }}>
                  {m.tool_calls.length > 0 && <div className="small mono faint row"><Wrench size={12} /> {m.tool_calls.join(", ")}</div>}
                  {m.content && <Markdown text={m.content} />}
                </div>)}
            {steps.length > 0 && (
              <div className="col" style={{ gap: 4 }}>
                {steps.map((s, i) => (
                  <div key={i} className="small mono faint row" style={{ alignItems: "flex-start" }}>
                    {s.kind === "call" ? <Wrench size={12} style={{ marginTop: 3 }} /> : s.kind === "check" ? <ShieldAlert size={12} style={{ marginTop: 3 }} />
                      : s.ok ? <Check size={12} style={{ marginTop: 3 }} /> : <span style={{ color: "var(--danger)" }}>✕</span>}
                    <span className="truncate">{s.kind === "call" ? `${s.name} ${s.text}` : s.text}</span>
                  </div>
                ))}
              </div>
            )}
            {approvals.map((a) => (
              <div key={a.id} className="card col" style={{ gap: 8 }}>
                <div className="row"><ShieldAlert size={16} /> <b>Approve {a.tool}?</b></div>
                <div className="small muted">{a.reason} {a.effect === "persistent" ? "This would change how the assistant behaves from now on." : "This acts outside the workspace."}</div>
                <pre className="small mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{a.args}</pre>
                <div className="row">
                  <button className="btn primary sm" onClick={() => decide(a, true)}>Approve</button>
                  <button className="btn sm" onClick={() => decide(a, false)}>Deny</button>
                </div>
              </div>
            ))}
            {busy && <div className="row muted"><Loader2 size={15} className="spin" /> Working…</div>}
            <div ref={endRef} />
          </div>
        </div>
        <form className="row" onSubmit={send} style={{ padding: "12px 24px 20px", borderTop: "1px solid var(--border)", background: "var(--panel)" }}>
          <textarea className="input grow" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Message your assistant (Enter to send, Shift+Enter for a new line)"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="btn primary" disabled={busy || !text.trim()}><Send size={15} /></button>
        </form>
      </div>
      {toast.node}
    </div>
  );
}

function MemoryTab() {
  const [items, setItems] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState({ content: "", kind: "fact" });
  const toast = useToast();
  const load = () => api<Memory[]>(`/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setItems).catch(toast.err);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [q]);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/memories", { body: form }); setForm({ content: "", kind: "fact" }); load(); } catch (err) { toast.err(err); }
  };
  const groups: [string, Memory[]][] = [["Who you are", items.filter((m) => m.kind === "profile")], ["Preferences", items.filter((m) => m.kind === "preference")], ["Facts", items.filter((m) => m.kind === "fact")]];
  return (
    <div className="col gap-lg">
      <p className="muted" style={{ margin: 0 }}>Learned automatically from your conversations on every channel. "Who you are" is always in context; the rest is recalled when relevant. Edit or delete anything.</p>
      <form className="card row wrap" onSubmit={add}>
        <input className="input grow" style={{ minWidth: 220 }} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="Teach it something, e.g. I prefer short answers" />
        <select className="input" style={{ width: 150 }} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
          <option value="profile">Who I am</option><option value="preference">Preference</option><option value="fact">Fact</option>
        </select>
        <button className="btn primary" disabled={form.content.trim().length < 3}>Add</button>
      </form>
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories" />
      {items.length === 0 && <Empty icon={<Brain size={24} />} title="Nothing remembered yet" hint="Chat normally; it learns as you go." />}
      {groups.filter(([, l]) => l.length).map(([title, list]) => (
        <div key={title}>
          <h3 style={{ marginBottom: 6 }}>{title}</h3>
          <div className="card flush">
            {list.map((m) => (
              <div key={m.id} className="list-item" style={{ cursor: "default" }}>
                <div className="grow">{m.content}<div className="faint small">{m.source === "auto" ? "learned" : m.source === "user" ? "added by you" : "saved by assistant"} · {relTime(m.updated_at)}</div></div>
                <button className="btn ghost sm icon" onClick={async () => { await api(`/memories/${m.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      ))}
      {toast.node}
    </div>
  );
}

function SkillsTab() {
  const [items, setItems] = useState<Skill[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [draft, setDraft] = useState<Skill | null>(null);
  const toast = useToast();
  const load = () => api<Skill[]>("/skills").then(setItems).catch(toast.err);
  useEffect(() => { load(); }, []);
  const save = async () => {
    if (!draft) return;
    try {
      if (draft.id) await api(`/skills/${draft.id}`, { method: "PUT", body: { ...draft, enabled: !!draft.enabled } });
      else await api("/skills", { body: { ...draft, enabled: true } });
      setDraft(null); load();
    } catch (e) { toast.err(e); }
  };
  return (
    <div className="col gap-lg">
      <div className="row"><p className="muted grow" style={{ margin: 0 }}>Reusable procedures. The assistant writes these after complex tasks and improves them on reuse. You can edit, disable or add your own.</p>
        <button className="btn" onClick={() => setDraft({ id: 0, name: "", description: "", steps: "", uses: 0, source: "user", enabled: 1 })}><Plus size={14} /> Skill</button></div>
      {draft && (
        <div className="card col">
          <input className="input mono" disabled={!!draft.id} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="short-kebab-name" />
          <input className="input" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="When to use it" />
          <textarea className="input mono" rows={8} value={draft.steps} onChange={(e) => setDraft({ ...draft, steps: e.target.value })} placeholder={"1. ...\n2. ..."} />
          <div className="row"><button className="btn primary" onClick={save}>Save</button><button className="btn ghost" onClick={() => setDraft(null)}>Cancel</button></div>
        </div>
      )}
      {items.length === 0 && !draft && <Empty icon={<Sparkles size={24} />} title="No skills yet" hint="Ask for something multi step, like 'plan my week from my calendar and tasks'." />}
      <div className="card flush">
        {items.map((s) => (
          <div key={s.id} className="col" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
            <div className="row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === s.id ? null : s.id)}>
              <strong className="mono grow">{s.name}</strong>
              <span className="badge">{s.source === "auto" ? "learned" : s.source}</span>
              <span className="faint small">used {s.uses}×</span>
            </div>
            <div className="muted small">{s.description}</div>
            {open === s.id && (
              <>
                <Markdown text={s.steps} />
                <div className="row">
                  <button className="btn sm" onClick={() => setDraft(s)}>Edit</button>
                  <button className="btn sm" onClick={async () => { await api(`/skills/${s.id}`, { method: "PUT", body: { ...s, enabled: !s.enabled } }); load(); }}>{s.enabled ? "Disable" : "Enable"}</button>
                  <button className="btn ghost sm danger" onClick={async () => { if (confirm(`Delete skill ${s.name}?`)) { await api(`/skills/${s.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={13} /></button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
      {toast.node}
    </div>
  );
}

type ASettings = { assistant_name: string; timezone: string; learning_enabled: boolean; search_provider: string; cli_token_set: boolean;
  telegram: { configured: boolean; running: boolean; bot: string; error: string; allowed_users: string } };

function SettingsTab() {
  const [s, setS] = useState<ASettings | null>(null);
  const [f, setF] = useState({ assistant_name: "", timezone: "", brave: "", tavily: "", tg_token: "", tg_users: "" });
  const [token, setToken] = useState("");
  const toast = useToast();
  const load = () => api<ASettings>("/assistant/settings").then((r) => { setS(r); setF((x) => ({ ...x, assistant_name: r.assistant_name, timezone: r.timezone, tg_users: r.telegram.allowed_users })); });
  useEffect(() => { load(); }, []);
  const put = async (body: Record<string, unknown>, msg = "Saved") => {
    try { setS(await api<ASettings>("/assistant/settings", { method: "PUT", body })); toast.ok(msg); } catch (e) { toast.err(e); }
  };
  if (!s) return null;
  const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <div className="col gap-lg">
      <div className="card col">
        <h3>Basics</h3>
        <div className="row wrap">
          <label className="field grow">Assistant name<input className="input" value={f.assistant_name} onChange={(e) => setF({ ...f, assistant_name: e.target.value })} /></label>
          <label className="field grow">Timezone (schedules follow it)<input className="input" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} placeholder={browserTz} /></label>
        </div>
        <div className="row">
          {f.timezone !== browserTz && <button className="btn sm ghost" onClick={() => setF({ ...f, timezone: browserTz })}>Use {browserTz}</button>}
          <div className="spacer" />
          <button className="btn primary" onClick={() => put({ assistant_name: f.assistant_name, timezone: f.timezone })}>Save</button>
        </div>
        <label className="check"><input type="checkbox" checked={s.learning_enabled} onChange={(e) => put({ learning_enabled: e.target.checked })} /> Learn memories and skills automatically (uses extra model calls)</label>
      </div>
      <div className="card col">
        <h3>Web search</h3>
        <div className="muted small">Currently: {s.search_provider || "not set up"}. Add a Brave Search or Tavily API key, or enable the SearXNG add-on.</div>
        <div className="row wrap">
          <input className="input grow" type="password" placeholder="Brave Search API key" value={f.brave} onChange={(e) => setF({ ...f, brave: e.target.value })} />
          <input className="input grow" type="password" placeholder="Tavily API key" value={f.tavily} onChange={(e) => setF({ ...f, tavily: e.target.value })} />
          <button className="btn" onClick={() => put({ ...(f.brave ? { brave_api_key: f.brave } : {}), ...(f.tavily ? { tavily_api_key: f.tavily } : {}) }, "Search key saved")}>Save</button>
        </div>
      </div>
      <div className="card col">
        <div className="row"><h3 className="grow">Telegram</h3>{s.telegram.running ? <span className="badge ok">@{s.telegram.bot} online</span> : s.telegram.configured ? <span className="badge danger">offline</span> : <span className="badge">not set up</span>}</div>
        {s.telegram.error && <div className="alert err">{s.telegram.error}</div>}
        <div className="muted small">1. Message @BotFather in Telegram, send /newbot, paste the token. 2. Message @userinfobot for your user id. Only listed ids get answers; scheduled reports go to the first one.</div>
        <div className="row wrap">
          <input className="input grow" type="password" placeholder={s.telegram.configured ? "Token saved (paste to replace)" : "Bot token"} value={f.tg_token} onChange={(e) => setF({ ...f, tg_token: e.target.value })} />
          <input className="input" style={{ width: 200 }} placeholder="Your user id(s), comma separated" value={f.tg_users} onChange={(e) => setF({ ...f, tg_users: e.target.value })} />
          <button className="btn" onClick={() => put({ ...(f.tg_token ? { telegram_token: f.tg_token } : {}), telegram_allowed_users: f.tg_users }, "Telegram saved; connecting…").then(() => setTimeout(load, 4000))}>Save</button>
          {s.telegram.configured && <button className="btn ghost danger" onClick={() => put({ telegram_token: "" }, "Telegram disconnected")}>Disconnect</button>}
        </div>
      </div>
      <div className="card col">
        <div className="row"><KeyRound size={15} /><h3 className="grow">Terminal access</h3>{s.cli_token_set && <span className="badge ok">token active</span>}</div>
        <div className="muted small">Install the <span className="mono">ws</span> command (cli/ws in the repository, Python 3 only), then run <span className="mono">ws login {window.location.origin}</span> and paste the token.</div>
        <div className="row">
          <button className="btn" onClick={async () => { const r = await api<{ token: string }>("/cli-token", { method: "POST" }); setToken(r.token); load(); }}>{s.cli_token_set ? "Replace token" : "Create token"}</button>
          {token && <><span className="mono small truncate grow">{token}</span><button className="btn sm" onClick={() => { navigator.clipboard.writeText(token); toast.ok("Copied"); }}><Copy size={13} /></button></>}
        </div>
        {token && <div className="alert">Shown once. It replaces any previous token.</div>}
      </div>
      {toast.node}
    </div>
  );
}

export default function Assistant({ tab }: { tab: "chat" | "memory" | "skills" | "settings" }) {
  const navigate = useNavigate();
  const tabs = (
    <div className="tabs">
      {(["chat", "memory", "skills", "settings"] as const).map((t) => (
        <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => navigate(t === "chat" ? "/assistant" : `/assistant-${t}`)}>{t[0].toUpperCase() + t.slice(1)}</button>
      ))}
    </div>
  );
  if (tab === "chat") return <Chat tabs={tabs} />;
  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head"><h1 className="grow">Assistant</h1>{tabs}</div>
      {tab === "memory" && <MemoryTab />}
      {tab === "skills" && <SkillsTab />}
      {tab === "settings" && <SettingsTab />}
    </div>
  );
}
