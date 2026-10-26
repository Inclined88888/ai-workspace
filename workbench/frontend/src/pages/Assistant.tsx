import { Brain, Check, Copy, Download, KeyRound, Loader2, MessageSquare, Plus, Search, Send, ShieldAlert, Sparkles, Square, Trash2, Upload, Wrench, X } from "lucide-react";
import { FormEvent, Fragment, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, stream } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Conv = { id: number; title: string; channel: string; updated_at: string; model: string };
type Msg = { id: number; role: string; content: string; tool_calls: string[]; name: string };
type Step = { name: string; args: string; status: "running" | "ok" | "error" | "approval"; preview?: string; ms?: number; startedAt: number };
type Memory = { id: number; content: string; kind: string; source: string; updated_at: string; created_at?: string; conversation_id?: number | null };
type Used = { id: number; content: string; kind: string };
type Approval = { id: number; tool: string; args: string; reason: string; channel: string; conversation_id: number | null; status: string; created_at: string };

const SOURCE: Record<string, string> = { auto: "learned", user: "added by you", agent: "saved by assistant", import: "imported" };

function argSummary(raw: string): string {
  try {
    const a = JSON.parse(raw) as Record<string, unknown>;
    return Object.entries(a).map(([k, v]) => `${k}: ${String(v).slice(0, 120)}`).join(" · ");
  } catch {
    return raw.slice(0, 200);
  }
}

function Approvals({ items, onDone, toast }: { items: Approval[]; onDone: () => void; toast: ReturnType<typeof useToast> }) {
  const [busy, setBusy] = useState(0);
  if (!items.length) return null;
  const decide = async (a: Approval, approve: boolean) => {
    setBusy(a.id);
    try {
      const r = await api<Approval & { result: string }>(`/approvals/${a.id}`, { body: { approve } });
      if (r.status === "failed") toast.err(r.result);
      else toast.ok(approve ? `${a.tool} done` : "Denied");
    } catch (e) { toast.err(e); } finally { setBusy(0); onDone(); }
  };
  return (
    <div className="card col" style={{ gap: 8, borderColor: "var(--warn, var(--accent))" }}>
      <div className="row"><ShieldAlert size={15} /><strong className="grow">{items.length} waiting for your approval</strong></div>
      <div className="muted small">These were requested in a conversation that read outside content (web, search or email), which can carry planted instructions. Check them before allowing.</div>
      {items.map((a) => (
        <div key={a.id} className="row wrap" style={{ alignItems: "flex-start", borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <div className="grow" style={{ minWidth: 200 }}>
            <span className="mono">{a.tool}</span> <span className="faint small">#{a.id} · {a.channel} · {relTime(a.created_at)}</span>
            <div className="small" style={{ wordBreak: "break-word" }}>{argSummary(a.args)}</div>
          </div>
          <button className="btn sm primary" disabled={busy === a.id} onClick={() => decide(a, true)}><Check size={13} /> Allow</button>
          <button className="btn sm ghost" disabled={busy === a.id} onClick={() => decide(a, false)}><X size={13} /> Deny</button>
        </div>
      ))}
    </div>
  );
}
type Skill = { id: number; name: string; description: string; steps: string; uses: number; source: string; enabled: number };

const CHANNEL: Record<string, string> = { web: "Web", cli: "Terminal", telegram: "Telegram", schedule: "Schedule" };

function Chat({ tabs }: { tabs: React.ReactNode }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const [convs, setConvs] = useState<Conv[]>([]);
  const [q, setQ] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState("");
  const [used, setUsed] = useState<Used[]>([]);
  const [showUsed, setShowUsed] = useState(false);
  const [pending, setPending] = useState<Approval[]>([]);
  const [running, setRunning] = useState<number | null>(null);
  const { models } = useModels();
  const endRef = useRef<HTMLDivElement>(null);
  const keepTurn = useRef(false); // set when a new chat's first turn moves us to its URL: keep its steps on screen
  const toast = useToast();
  const cid = id ? Number(id) : null;

  const loadConvs = () => api<Conv[]>(`/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setConvs).catch(toast.err);
  const loadPending = () => api<Approval[]>("/approvals").then(setPending).catch(() => setPending([]));
  useEffect(() => { const t = setTimeout(loadConvs, 200); return () => clearTimeout(t); }, [q]);
  useEffect(() => { loadPending(); }, []);
  useEffect(() => {
    if (keepTurn.current) { keepTurn.current = false; return; }
    setSteps([]);
    setUsed([]);
    if (!cid) return setMessages([]);
    api<{ messages: Msg[]; model: string }>(`/conversations/${cid}`).then((c) => { setMessages(c.messages); setModel(c.model || ""); }).catch(toast.err);
  }, [cid]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, steps]);

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const msg = text.trim();
    if (!msg || busy) return;
    setText("");
    setBusy(true);
    setSteps([]);
    setUsed([]);
    setMessages((m) => [...m, { id: Date.now(), role: "user", content: msg, tool_calls: [], name: "" }]);
    let newId = cid;
    // Update the newest step with this name that is still open
    const settle = (name: string, patch: Partial<Step>) => setSteps((s) => {
      const i = s.map((x) => x.name === name && (x.status === "running" || x.status === "approval")).lastIndexOf(true);
      return i < 0 ? s : s.map((x, j) => (j === i ? { ...x, ...patch, status: x.status === "approval" && patch.status !== "approval" ? "approval" : patch.status ?? x.status } : x));
    });
    await stream("/assistant/chat", { message: msg, conversation_id: cid, model: model || null }, (ev) => {
      const t = ev.type as string | undefined;
      if (t === "start") { newId = Number(ev.conversation_id); setRunning(newId); }
      if (t === "memories") setUsed((ev.items as Used[]) ?? []);
      if (t === "tool_call") setSteps((s) => [...s, { name: String(ev.name), args: String(ev.args), status: "running", startedAt: Date.now() }]);
      if (t === "approval") { settle(String(ev.name), { status: "approval" }); loadPending(); }
      if (t === "tool_result") settle(String(ev.name), { status: ev.ok ? "ok" : "error", preview: String(ev.preview), ms: Number(ev.ms) || undefined });
      if (t === "message") setMessages((m) => [...m, { id: Date.now() + 1, role: "assistant", content: String(ev.content), tool_calls: [], name: "" }]);
      if (t === "error" || (!t && ev.error)) toast.err(String(ev.error));
    });
    setBusy(false);
    setRunning(null);
    loadConvs();
    loadPending();
    if (newId && newId !== cid) { keepTurn.current = true; navigate(`/assistant/${newId}`, { replace: true }); }
  };

  const remove = async (c: Conv) => {
    if (!confirm(`Delete "${c.title}"? Memories learned from it are kept.`)) return;
    await api(`/conversations/${c.id}`, { method: "DELETE" });
    if (c.id === cid) navigate("/assistant");
    loadConvs();
  };

  const stop = async () => {
    if (!running) return;
    try { await api(`/assistant/stop/${running}`, { method: "POST" }); toast.ok("Stopping after the current step"); } catch (e) { toast.err(e); }
  };
  const forgetUsed = async (m: Used) => {
    try { await api(`/memories/${m.id}`, { method: "DELETE" }); setUsed((u) => u.filter((x) => x.id !== m.id)); toast.ok("Forgotten"); } catch (e) { toast.err(e); }
  };

  const visible = messages.filter((m) => m.role === "user" || (m.role === "assistant" && (m.content || m.tool_calls.length)));
  const lastUser = visible.map((m) => m.role).lastIndexOf("user");
  const timeline = steps.length > 0 && (
              <ol className="col" style={{ gap: 6, listStyle: "none", padding: 0, margin: 0 }} aria-label="Steps">
                {steps.map((s, i) => (
                  <li key={i} className="small" style={{ borderLeft: `2px solid ${s.status === "error" ? "var(--danger)" : s.status === "approval" ? "var(--accent)" : "var(--border)"}`, paddingLeft: 10 }}>
                    <div className="row" style={{ gap: 6 }}>
                      {s.status === "running" ? <Loader2 size={12} className="spin" /> : s.status === "ok" ? <Check size={12} /> : s.status === "approval" ? <ShieldAlert size={12} /> : <X size={12} style={{ color: "var(--danger)" }} />}
                      <span className="mono">{s.name}</span>
                      <span className="faint">{s.status === "approval" ? "waiting for your approval" : s.status === "running" ? "running" : s.status === "error" ? "failed" : "done"}{s.ms !== undefined ? ` · ${s.ms < 1000 ? `${s.ms} ms` : `${(s.ms / 1000).toFixed(1)} s`}` : ""}</span>
                    </div>
                    <details>
                      <summary className="faint truncate" style={{ cursor: "pointer" }}>{argSummary(s.args) || "no arguments"}</summary>
                      {s.preview && <div className="mono faint" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", marginTop: 4 }}>{s.preview}</div>}
                    </details>
                  </li>
                ))}
              </ol>
            );

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
            {visible.map((m, i) => (
              <Fragment key={m.id}>
                {m.role === "user"
                  ? <div className="bubble-user" style={{ alignSelf: "flex-end", maxWidth: "85%" }}>{m.content}</div>
                  : <div className="col" style={{ gap: 4 }}>
                      {m.tool_calls.length > 0 && <div className="small mono faint row"><Wrench size={12} /> {m.tool_calls.join(", ")}</div>}
                      {m.content && <Markdown text={m.content} />}
                    </div>}
                {i === lastUser && timeline}
              </Fragment>
            ))}
            {used.length > 0 && !busy && (
              <div className="small">
                <button type="button" className="btn ghost sm" onClick={() => setShowUsed(!showUsed)} aria-expanded={showUsed}><Brain size={13} /> Used {used.length} {used.length === 1 ? "memory" : "memories"}</button>
                {showUsed && (
                  <div className="card flush" style={{ marginTop: 6 }}>
                    {used.map((m) => (
                      <div key={m.id} className="list-item" style={{ cursor: "default" }}>
                        <div className="grow">{m.content}<div className="faint small">{m.kind}</div></div>
                        <button className="btn ghost sm icon" title="Forget this" aria-label="Forget this memory" onClick={() => forgetUsed(m)}><Trash2 size={13} /></button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {busy && <div className="row muted"><Loader2 size={15} className="spin" /> Working…<div className="spacer" />{running && <button type="button" className="btn sm" onClick={stop}><Square size={12} /> Stop</button>}</div>}
            <Approvals items={pending} onDone={loadPending} toast={toast} />
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
  const exportAll = async () => {
    try {
      const data = await api<unknown>("/memories/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = `memories-${new Date().toISOString().slice(0, 10)}.json`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast.err(e); }
  };
  const importFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as { memories?: { content: string; kind: string }[] };
      const memories = (parsed.memories ?? []).filter((m) => typeof m?.content === "string").map((m) => ({ content: m.content.slice(0, 1000), kind: ["profile", "preference", "fact"].includes(m.kind) ? m.kind : "fact" }));
      const r = await api<{ added: number; skipped: number }>("/memories/import", { body: { memories } });
      toast.ok(`Imported ${r.added}, skipped ${r.skipped} already known`); load();
    } catch (e) { toast.err(e instanceof SyntaxError ? "That file is not valid JSON" : e); }
  };
  const groups: [string, Memory[]][] = [["Who you are", items.filter((m) => m.kind === "profile")], ["Preferences", items.filter((m) => m.kind === "preference")], ["Facts", items.filter((m) => m.kind === "fact")]];
  return (
    <div className="col gap-lg">
      <div className="row wrap">
        <p className="muted grow" style={{ margin: 0, minWidth: 240 }}>Learned automatically from your conversations on every channel. "Who you are" is always in context; the rest is recalled when relevant. Deleted memories are never recalled again.</p>
        <button className="btn sm" onClick={exportAll}><Download size={13} /> Export</button>
        <label className="btn sm" style={{ cursor: "pointer" }}><Upload size={13} /> Import<input type="file" accept="application/json,.json" hidden onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }} /></label>
      </div>
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
                <div className="grow">{m.content}<div className="faint small">
                  {SOURCE[m.source] ?? m.source}
                  {m.created_at && <> · <span title={new Date(m.created_at).toLocaleString()}>added {relTime(m.created_at)}</span></>}
                  {m.created_at && m.updated_at !== m.created_at && <> · edited {relTime(m.updated_at)}</>}
                  {m.conversation_id ? <> · <a href={`/assistant/${m.conversation_id}`}>from this chat</a></> : null}
                </div></div>
                <button className="btn ghost sm icon" aria-label="Delete memory" onClick={async () => { await api(`/memories/${m.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
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
