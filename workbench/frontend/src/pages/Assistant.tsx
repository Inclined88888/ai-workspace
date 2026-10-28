import { Brain, Check, Copy, KeyRound, ListChecks, Loader2, MessageSquare, Plus, Search, Send, ShieldQuestion, Sparkles, Trash2, Wrench, X } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, stream } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Conv = { id: number; title: string; channel: string; updated_at: string; model: string };
type Step = { kind: "call" | "result"; name: string; text: string; ok?: boolean };
type Msg = { id: number; role: string; content: string; tool_calls: string[]; name: string; steps?: Step[] };
type Memory = { id: number; content: string; kind: string; source: string; updated_at: string };
type Approval = { id: number; name: string; args: string; reason: string; description?: string };
type Action = { id: number; ts: string; name: string; arguments: string; decision: string; ok: number; result: string; channel: string; conversation_id: number };
type Skill = { id: number; name: string; description: string; steps: string; uses: number; source: string; enabled: number };

const CHANNEL: Record<string, string> = { web: "Web", cli: "Terminal", telegram: "Telegram", schedule: "Schedule" };

function Steps({ steps }: { steps: Step[] }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      {steps.map((s, i) => (
        <div key={i} className="small mono faint row" style={{ alignItems: "flex-start" }}>
          {s.kind === "call" ? <Wrench size={12} style={{ marginTop: 3 }} /> : s.ok ? <Check size={12} style={{ marginTop: 3 }} /> : <span style={{ color: "var(--danger)" }}>✕</span>}
          <span className="truncate">{s.kind === "call" ? `${s.name} ${s.text}` : s.text}</span>
        </div>
      ))}
    </div>
  );
}

function Chat({ tabs }: { tabs: React.ReactNode }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const stepsRef = useRef<Step[]>([]);  // the steps of the run in progress, moved onto the answer they led to
  const addStep = (st: Step) => { stepsRef.current = [...stepsRef.current, st]; setSteps(stepsRef.current); };
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [partial, setPartial] = useState("");
  const [approval, setApproval] = useState<Approval | null>(null);
  const [model, setModel] = useState("");
  const { models } = useModels();
  const endRef = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const cid = id ? Number(id) : null;

  const loadConvs = () => api<Conv[]>(`/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setConvs).catch(toast.err);
  useEffect(() => { const t = setTimeout(loadConvs, 200); return () => clearTimeout(t); }, [q]);
  useEffect(() => {
    setSteps([]);
    stepsRef.current = [];
    setApproval(null);
    if (!cid) return setMessages([]);
    api<{ messages: Msg[]; model: string; pending_approvals: { id: number; name: string; arguments: string; reason: string }[] }>(`/conversations/${cid}`).then((c) => {
      setMessages(c.messages); setModel(c.model || "");
      const p = c.pending_approvals?.[0];
      if (p) setApproval({ id: p.id, name: p.name, args: p.arguments, reason: p.reason });
    }).catch(toast.err);
  }, [cid]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, steps, partial]);

  // One handler for every event stream: a new message, or the conversation continuing after an approval
  const run = async (path: string, body: unknown) => {
    setBusy(true);
    setPartial("");
    setApproval(null);
    let newId = cid;
    let paused = false;  // the approval card says it; no "I need your approval" bubble as well
    try { await stream(path, body, (ev) => {
      const t = ev.type as string | undefined;
      if (t === "start") newId = Number(ev.conversation_id);
      if (t === "delta") setPartial((s) => s + String(ev.content));
      if (t === "tool_call") {
        setPartial("");
        addStep({ kind: "call", name: String(ev.name), text: String(ev.args) });
      }
      if (t === "tool_result") addStep({ kind: "result", name: String(ev.name), text: String(ev.preview), ok: Boolean(ev.ok) });
      if (t === "approval") paused = true;
      if (t === "approval") setApproval({ id: Number(ev.id), name: String(ev.name), args: String(ev.args), reason: String(ev.reason || ""), description: String(ev.description || "") });
      if (t === "message" && (!ev.content || paused)) return;
      if (t === "message") {
        setPartial("");
        const done = stepsRef.current;
        stepsRef.current = [];
        setSteps([]);
        setMessages((m) => [...m, { id: Date.now() + 1, role: "assistant", content: String(ev.content), tool_calls: [], name: "", steps: done }]);
      }
      if (t === "error" || (!t && ev.error)) toast.err(String(ev.error));
    }); } catch (err) { toast.err(err); }
    setBusy(false);
    setPartial("");
    loadConvs();
    if (newId && newId !== cid) navigate(`/assistant/${newId}`, { replace: true });
  };

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const msg = text.trim();
    if (!msg || busy) return;
    setText("");
    setSteps([]);
    stepsRef.current = [];
    setMessages((m) => [...m, { id: Date.now(), role: "user", content: msg, tool_calls: [], name: "" }]);
    await run("/assistant/chat", { message: msg, conversation_id: cid, model: model || null });
  };

  const decide = (approve: boolean) => approval && run(`/approvals/${approval.id}`, { approve, model: model || null });

  const remove = async (c: Conv) => {
    if (!confirm(`Delete "${c.title}"? Memories learned from it are kept.`)) return;
    await api(`/conversations/${c.id}`, { method: "DELETE" });
    if (c.id === cid) navigate("/assistant");
    loadConvs();
  };

  const visible = messages.filter((m) => m.role === "user" || (m.role === "assistant" && (m.content || m.tool_calls.length)));
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
                  {m.steps && m.steps.length > 0 && <Steps steps={m.steps} />}
                  {m.content && <Markdown text={m.content} />}
                </div>)}
            {steps.length > 0 && <Steps steps={steps} />}
            {partial && <Markdown text={partial} />}
            {approval && !busy && (
              <div className="card col" style={{ borderColor: "var(--warn, #d97706)", gap: 8 }}>
                <div className="row"><ShieldQuestion size={16} /><strong className="grow">Allow {approval.name}?</strong></div>
                {approval.reason && <div className="muted small">{approval.reason}</div>}
                {approval.description && <div className="faint small">{approval.description}</div>}
                <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 200, overflow: "auto" }}>{approval.args}</pre>
                <div className="row">
                  <button className="btn primary" onClick={() => decide(true)}><Check size={14} /> Approve</button>
                  <button className="btn" onClick={() => decide(false)}><X size={14} /> Decline</button>
                  <span className="faint small">Sending a new message instead declines it.</span>
                </div>
              </div>
            )}
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

const DECISION: Record<string, string> = { allowed: "ran", approved: "approved", declined: "declined", denied: "blocked", expired: "not approved" };

function ActivityTab() {
  const [items, setItems] = useState<Action[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const toast = useToast();
  useEffect(() => { api<Action[]>("/actions?limit=300").then(setItems).catch(toast.err); }, []);
  return (
    <div className="col gap-lg">
      <p className="muted" style={{ margin: 0 }}>Every tool the assistant used on any channel, with its arguments and result. Approvals and blocked calls are listed too.</p>
      {items.length === 0 && <Empty icon={<ListChecks size={24} />} title="No activity yet" />}
      <div className="card flush">
        {items.map((a) => (
          <div key={a.id} className="col" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", cursor: "pointer" }} onClick={() => setOpen(open === a.id ? null : a.id)}>
            <div className="row">
              <strong className="mono grow truncate">{a.name}</strong>
              <span className={`badge ${a.decision === "denied" || a.decision === "declined" ? "danger" : a.ok ? "ok" : ""}`}>{DECISION[a.decision] ?? a.decision}{a.decision !== "denied" && a.decision !== "declined" && a.decision !== "expired" && !a.ok ? ", failed" : ""}</span>
              <span className="faint small">{CHANNEL[a.channel] ?? a.channel} · {relTime(a.ts)}</span>
            </div>
            <div className="faint small mono truncate">{a.arguments}</div>
            {open === a.id && <pre className="mono small" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{a.result || "(no result)"}</pre>}
          </div>
        ))}
      </div>
      {toast.node}
    </div>
  );
}

type Policy = { name: string; description: string; policy: "allow" | "ask" | "deny"; default: string; external: boolean; persists: boolean };

function ToolPermissions() {
  const [items, setItems] = useState<Policy[]>([]);
  const toast = useToast();
  const load = () => api<Policy[]>("/tool-policies").then(setItems).catch(toast.err);
  useEffect(() => { load(); }, []);
  const set = async (name: string, policy: string) => {
    try { await api("/tool-policies", { method: "PUT", body: { name, policy } }); load(); } catch (e) { toast.err(e); }
  };
  return (
    <div className="card col">
      <h3>Tool permissions</h3>
      <div className="muted small">Allow runs a tool straight away, Ask waits for your approval in chat, the terminal or Telegram, Deny never runs it. Scheduled runs and company agents cannot ask, so for them Ask means Deny. After the assistant reads web pages or email in a turn, memories, skills and schedules always ask.</div>
      <div className="card flush">
        {items.map((t) => (
          <div key={t.name} className="row" style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)" }}>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="mono small">{t.name}{t.external && <span className="badge" style={{ marginLeft: 6 }}>MCP</span>}</div>
              <div className="faint small truncate">{t.description}</div>
            </div>
            <select className="input" style={{ width: 110 }} value={t.policy} onChange={(e) => set(t.name, e.target.value)}>
              <option value="allow">Allow</option><option value="ask">Ask</option><option value="deny">Deny</option>
            </select>
          </div>
        ))}
      </div>
      {toast.node}
    </div>
  );
}

type SearchResult = { provider: string; ok: boolean; results: number; ms: number; error: string; sample: { title: string; url: string }[] };

function SearchTest() {
  const [r, setR] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setR(await api<SearchResult>("/assistant/search-test", { method: "POST" })); } finally { setBusy(false); }
  };
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row"><button className="btn sm" onClick={run} disabled={busy}>{busy ? <Loader2 size={13} className="spin" /> : <Search size={13} />} Test search</button>
        {r && <span className={`badge ${r.ok ? "ok" : "danger"}`}>{r.provider || "none"}: {r.ok ? `${r.results} results in ${r.ms} ms` : "failed"}</span>}</div>
      {r && !r.ok && <div className="alert err">{r.error}</div>}
      {r && r.ok && <div className="faint small">{r.sample.map((x) => x.title || x.url).join(" · ")}</div>}
    </div>
  );
}

type ASettings = { tool_mode: "all" | "compact" | "auto"; assistant_name: string; timezone: string; learning_enabled: boolean; search_provider: string; search_fallback: boolean; cli_token_set: boolean;
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
        <label className="field">Tools offered to the model
          <select className="input" value={s.tool_mode} onChange={(e) => put({ tool_mode: e.target.value })}>
            <option value="auto">Auto</option><option value="compact">Compact</option><option value="all">All</option>
          </select>
        </label>
        <div className="muted small">Auto uses a smaller tool list for Ollama context windows below 32,768 tokens. Compact keeps core tools and adds others from keywords in your message. Choose All for tasks that need the full tool list.</div>
        <label className="check"><input type="checkbox" checked={s.learning_enabled} onChange={(e) => put({ learning_enabled: e.target.checked })} /> Learn memories and skills automatically (uses extra model calls)</label>
      </div>
      <ToolPermissions />
      <div className="card col">
        <h3>Web search</h3>
        <div className="muted small">Currently: {s.search_provider || "not set up"}. Without a key or the SearXNG add-on, the assistant uses DuckDuckGo's keyless results page, which can rate limit. For dependable keyless search enable the SearXNG add-on, or add a Brave Search or Tavily API key.</div>
        <label className="check"><input type="checkbox" checked={s.search_fallback} onChange={(e) => put({ search_fallback: e.target.checked })} /> Fall back to DuckDuckGo when no search provider is set up</label>
        <SearchTest />
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

export default function Assistant({ tab }: { tab: "chat" | "memory" | "skills" | "activity" | "settings" }) {
  const navigate = useNavigate();
  const tabs = (
    <div className="tabs">
      {(["chat", "memory", "skills", "activity", "settings"] as const).map((t) => (
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
      {tab === "activity" && <ActivityTab />}
      {tab === "settings" && <SettingsTab />}
    </div>
  );
}
