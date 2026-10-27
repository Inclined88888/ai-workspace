import { BarChart3, BookOpenCheck, Brain, CalendarRange, Check, ChevronDown, ChevronRight, Copy, Download, FileSpreadsheet, FilePen, Flag, FolderKanban, FolderTree, Paperclip, KeyRound, Loader2, MessageSquare, Mic, MonitorPlay, Pencil, Plus, Repeat, RotateCcw, Search, Send, ShieldAlert, Sparkles, Square, Trash2, Upload, Wrench, X } from "lucide-react";
import { FormEvent, Fragment, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, FileMeta, stream, uploadFile } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";
import { shortRef } from "../components/Models";
import WebSearchPanel from "../components/WebSearch";
import VoiceSession from "../components/Voice";
import RichText from "../rich/RichText";
import Blocks from "../rich/Blocks";
import { Block, validateAll } from "../rich/schema";

type Conv = { id: number; title: string; channel: string; updated_at: string; model: string; project_id?: number | null };
type ProjectRef = { id: number; name: string };
type Msg = { id: number; role: string; content: string; tool_calls: string[]; name: string; files?: string[]; created_at?: string; blocks?: unknown;
  live?: { blocks: Block[]; tools: { name: string; ok: boolean }[] } };
type View = Msg & { blocks: Block[]; tools: { name: string; ok: boolean }[] };
const FAILED = /^(Error|Not |Unknown tool|Invalid arguments)/;

/** Messages as shown: each answer carries the structured results of the tools used for it (rebuilt by the server from
 *  what was stored, or collected live while it streamed), and a summary of those tools. */
function toView(messages: Msg[]): View[] {
  const out: View[] = [];
  let blocks: Block[] = [];
  let tools: { name: string; ok: boolean }[] = [];
  for (const m of messages) {
    if (m.role === "user") { blocks = []; tools = []; out.push({ ...m, blocks: [], tools: [] }); }
    else if (m.role === "tool") { blocks = blocks.concat(validateAll(m.blocks)); tools.push({ name: m.name, ok: !FAILED.test(m.content) }); }
    else if (m.role === "assistant") {
      if (m.live) out.push({ ...m, blocks: m.live.blocks, tools: m.live.tools });
      else if (!m.tool_calls.length) { out.push({ ...m, blocks, tools }); blocks = []; tools = []; }
      else if (m.content) out.push({ ...m, blocks: [], tools: [] });  // a thought between tool calls
    }
  }
  return out;
}

const STARTS: { icon: typeof Search; title: string; about: string; send?: string; fill?: string; attach?: boolean }[] = [
  { icon: Search, title: "Research a topic", about: "Searches, reads sources and cites them", fill: "Research " },
  { icon: FolderKanban, title: "Check my projects", about: "Goals, open questions, tasks and missions", send: "How is my most recent project going?" },
  { icon: CalendarRange, title: "Plan my week", about: "From your calendar and tasks", send: "Plan my week from my calendar and tasks" },
  { icon: BarChart3, title: "Show my AI spending", about: "Totals and a chart from real usage", send: "Show my AI model spending for the last 30 days" },
  { icon: FilePen, title: "Create a document", about: "Written and saved as a note you can download", fill: "Write a one-page document about " },
  { icon: FileSpreadsheet, title: "Analyse a spreadsheet", about: "Attach a file and ask about it", attach: true },
  { icon: Repeat, title: "Create a recurring reminder", about: "An automation on a schedule", fill: "Every Monday at 9am, remind me to " },
  { icon: Flag, title: "Start a mission", about: "A bigger job: you approve the plan first", fill: "/mission " },
  { icon: FolderTree, title: "Organise my files", about: "On your computer, with the ROOKS Companion", fill: "Organise the files in my Downloads folder into folders by type" },
  { icon: MonitorPlay, title: "Use my computer", about: "Apps and folders you allow, visible and stoppable", fill: "On my computer, " },
  { icon: BookOpenCheck, title: "Compare my AI models", about: "A table of the models you have connected", send: "Compare the AI models I have connected" },
];
type Step = { name: string; args: string; status: "running" | "ok" | "error" | "approval"; preview?: string; detail?: string; ms?: number; startedAt: number };
type Memory = { id: number; content: string; kind: string; source: string; updated_at: string; created_at?: string; conversation_id?: number | null;
  confidence?: number; previous?: string; matched_by?: string[] };
type MemStatus = { embed_model: string; memories: number; indexed: number; error: string };
type Used = { id: number; content: string; kind: string };
type Approval = { id: number; tool: string; args: string; reason: string; channel: string; conversation_id: number | null; status: string; created_at: string };

// Plain English for each real tool event the backend sends; unknown tools fall back to their name
const STEP_LABEL: Record<string, string> = {
  search_notes: "Searching your notes", read_note: "Reading a note", create_note: "Saving a note", append_to_note: "Adding to a note",
  list_tasks: "Checking your tasks", create_task: "Creating a task", update_task: "Updating a task",
  get_agenda: "Checking your calendar", create_event: "Adding a calendar event",
  search_email: "Searching your email", save_email_draft: "Preparing an email draft",
  web_search: "Searching the web", read_page: "Reading a web page", browse_web: "Browsing websites",
  remember: "Saving a memory", forget: "Forgetting a memory", search_memory: "Checking what it remembers", search_history: "Searching past conversations",
  load_skill: "Using a saved skill", save_skill: "Saving a reusable skill",
  schedule_task: "Setting up an automation", list_schedules: "Checking automations",
  delegate_task: "Handing work to a teammate", list_team: "Checking the team",
  run_command: "Running a command", workspace_list: "Looking at the workspace", workspace_read: "Reading a workspace file",
  workspace_write: "Writing a workspace file", workspace_edit: "Editing a workspace file",
  get_usage: "Checking your spending", list_models: "Checking your AI models", project_status: "Checking the project",
  list_missions: "Checking your missions", start_mission: "Starting a mission", start_research: "Starting research",
  search_files: "Searching your files", read_file: "Reading a file", github_search: "Searching GitHub", github_read_issue: "Reading a GitHub issue",
  github_create_issue: "Opening a GitHub issue", github_comment: "Commenting on GitHub", computer_observe: "Looking at the screen",
};
const WORKSPACE_TOOLS = new Set(["run_command", "workspace_list", "workspace_read", "workspace_write", "workspace_edit"]);

/** "exit code 1", "timed out", "stopped by you" from a terminal result, for the step's badge. */
function exitBadge(detail: string): { text: string; tone: string } | null {
  const m = /\((?:in [^;]*; )?(exit code (\d+)|timed out|stopped by you)/.exec(detail);
  if (!m) return null;
  if (m[2] !== undefined) return { text: `exit ${m[2]}`, tone: m[2] === "0" ? "ok" : "danger" };
  return { text: m[1], tone: "warn" };
}
type Note = { id: number; kind: "info" | "warn" | "err"; text: string; fix?: boolean; retry?: string; search?: boolean };
type MStatus = { default: { ref: string; source: string; error: string }; fallbacks: string[]; providers: { name: string; ok: boolean; checked: boolean; error: string }[] };

// Chat commands, in the style of Hermes and OpenClaw. They run here and are never sent to the model.
const COMMANDS: [string, string][] = [
  ["/help", "List commands"],
  ["/new", "Start a new conversation"],
  ["/mission", "Hand ROOKS a bigger job: /mission plan the team offsite and draft the invite"],
  ["/model", "Show the model, or switch: /model gemini-3.8-flash, /model auto"],
  ["/models", "Free and available models"],
  ["/status", "Which model answers, its backups and provider health"],
  ["/retry", "Send your last message again"],
  ["/stop", "Stop the current answer after this step"],
  ["/usage", "This month's tokens and cost"],
];


const SOURCE: Record<string, string> = { auto: "learned", user: "added by you", agent: "saved by assistant", import: "imported" };

// Attached files are stored inside the user's message; show them as chips instead of the extracted text
const ATTACH_RE = /\n*\[Attached file: (.+?) \(file #(\d+)\)[\].][\s\S]*?(?=\n*\[Attached file: |$)/g;
function splitAttachments(content: string): { text: string; files: string[] } {
  const files: string[] = [];
  const text = content.replace(ATTACH_RE, (_m, name: string) => { files.push(name); return ""; }).trim();
  return { text, files };
}

function argSummary(raw: string, name = ""): string {
  try {
    const a = JSON.parse(raw) as Record<string, unknown>;
    if (name === "run_command" && typeof a.command === "string") return `$ ${a.command}${a.cwd ? `  (in ${String(a.cwd)})` : ""}`;
    if (WORKSPACE_TOOLS.has(name) && typeof a.path === "string") return a.path || ".";
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
type Skill = { id: number; name: string; description: string; steps: string; uses: number; source: string; enabled: number; warnings?: string[] };

const CHANNEL: Record<string, string> = { web: "Web", cli: "Terminal", telegram: "Telegram", schedule: "Schedule" };

function Chat({ tabs }: { tabs: React.ReactNode }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [noModel, setNoModel] = useState(false);
  const [notes, setNotes] = useState<Note[]>([]);
  const [autoRef, setAutoRef] = useState("");
  const [menuIdx, setMenuIdx] = useState(0);
  const [attached, setAttached] = useState<FileMeta[]>([]);
  const [uploading, setUploading] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const attach = async (list: FileList | File[]) => {
    const arr = [...list].slice(0, 10 - attached.length);
    setUploading((n) => n + arr.length);
    for (const f of arr) {
      try {
        const meta = await uploadFile(f);
        setAttached((a) => [...a, meta]);
        if (!meta.chars) note("warn", `${f.name}: ${meta.note}`);
      } catch (e) { note("err", `${f.name}: ${(e as Error).message}`); }
      setUploading((n) => n - 1);
    }
  };
  const note = (kind: Note["kind"], text: string, extra: Partial<Note> = {}) => setNotes((n) => [...n, { id: Date.now() + Math.random(), kind, text, ...extra }]);
  const autoAsked = useRef(false);
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
  const [liveBlocks, setLiveBlocks] = useState<Block[]>([]);
  const [editing, setEditing] = useState<Msg | null>(null);
  const [openSteps, setOpenSteps] = useState(false);
  const [showList, setShowList] = useState(false);  // phones: the conversation list replaces the chat while open
  useEffect(() => setShowList(false), [id]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // A voice session in this chat; an accepted call invitation arrives with what ROOKS opens by saying
  const [voice, setVoice] = useState<{ opening?: string } | null>(() => {
    const st = location.state as { voice?: boolean; speak?: string } | null;
    return st?.voice ? { opening: st.speak } : null;
  });
  const { models } = useModels();
  const endRef = useRef<HTMLDivElement>(null);
  const keepTurn = useRef(false); // set when a new chat's first turn moves us to its URL: keep its steps on screen
  const toast = useToast();
  const cid = id ? Number(id) : null;
  // A new chat can start inside a project (/assistant?project=3); an existing one belongs to the project it was started in
  const startProject = Number(new URLSearchParams(location.search).get("project")) || null;
  const [convProject, setConvProject] = useState<number | null>(null);
  const [projectList, setProjectList] = useState<ProjectRef[]>([]);
  useEffect(() => { api<ProjectRef[]>("/projects?archived=true").then(setProjectList).catch(() => setProjectList([])); }, []);
  const projectId = cid ? convProject : startProject;
  const project = projectList.find((p) => p.id === projectId);

  const loadConvs = () => api<Conv[]>(`/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setConvs).catch(toast.err);
  const loadPending = () => api<Approval[]>("/approvals").then(setPending).catch(() => setPending([]));
  useEffect(() => { const t = setTimeout(loadConvs, 200); return () => clearTimeout(t); }, [q]);
  useEffect(() => { loadPending(); }, []);
  const [searchOn, setSearchOn] = useState(true);
  useEffect(() => { api<{ providers: number; search_provider: string }>("/getting-started").then((r) => { setNoModel(r.providers === 0); setSearchOn(!!r.search_provider); }).catch(() => setNoModel(false)); }, []);
  useEffect(() => { api<MStatus>("/models/status").then((r) => setAutoRef(r.default.ref)).catch(() => setAutoRef("")); }, []);
  useEffect(() => {
    if (keepTurn.current) { keepTurn.current = false; return; }
    setSteps([]);
    setUsed([]);
    setNotes([]);
    if (!cid) { setConvProject(null); return setMessages([]); }
    loadConversation(cid);
  }, [cid]);
  function loadConversation(id: number) {
    return api<{ messages: Msg[]; model: string; project_id: number | null }>(`/conversations/${id}`)
      .then((c) => { setMessages(c.messages); setModel(c.model || ""); setConvProject(c.project_id); }).catch(toast.err);
  }
  // Moving to another conversation closes a voice session (it stops listening); an accepted call opens one
  const firstCid = useRef(true);
  useEffect(() => {
    const st = location.state as { voice?: boolean; speak?: string } | null;
    if (st?.voice) setVoice({ opening: st.speak });
    else if (!firstCid.current) setVoice(null);
    firstCid.current = false;
  }, [cid, location.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const voiceClosed = (conversation: number | null) => {
    setVoice(null);
    if (location.state) navigate(location.pathname, { replace: true, state: null });
    loadConvs();
    if (conversation && conversation !== cid) navigate(`/assistant/${conversation}`);
    else if (conversation) loadConversation(conversation);
    else if (cid) navigate("/assistant");
  };
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, steps]);
  // A question typed on Home arrives here and is sent once. Declared after the effect that resets the chat for a
  // new conversation, so that reset runs first and cannot wipe the question.
  useEffect(() => {
    const st = location.state as { ask?: string; files?: number[]; fileNames?: string[] } | null;
    const asked = st?.ask;
    if (asked && !autoAsked.current && !cid) {
      autoAsked.current = true;
      navigate(location.pathname, { replace: true, state: null });
      send(undefined, asked, st?.files, st?.fileNames);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps


  const send = async (e?: FormEvent, override?: string, fileIds?: number[], fileNames?: string[], fromMessage?: Msg) => {
    e?.preventDefault();
    let msg = (override ?? text).trim();
    const replace = fromMessage ?? (override === undefined ? editing : null);
    if (!msg && !override && attached.length) msg = attached.length > 1 ? "Summarise the attached files." : "Summarise the attached file.";
    if (!msg) return;
    const cmd = msg.split(/\s+/)[0].toLowerCase();
    if (COMMANDS.some(([c]) => c === cmd)) { setText(""); await runCommand(cmd, msg.slice(cmd.length).trim()); return; }
    if (busy) return;
    if (replace && cid) {
      // edit and resend, or regenerate: the conversation continues from this message
      try { await api(`/conversations/${cid}/truncate`, { body: { from_message_id: replace.id } }); }
      catch (err) { note("err", (err as Error).message); return; }
      setMessages((m) => m.filter((x) => x.id < replace.id));
    }
    setEditing(null);
    setText("");
    setBusy(true);
    setSteps([]);
    setLiveBlocks([]);
    setOpenSteps(false);
    setUsed([]);
    setNotes([]);
    const turnBlocks: Block[] = [];
    const turnTools: { name: string; ok: boolean }[] = [];
    const files = fileIds ? fileIds.map((id, i) => ({ id, name: fileNames?.[i] ?? `file #${id}` })) : attached;
    setAttached([]);
    setMessages((m) => [...m, { id: Date.now(), role: "user", content: msg, tool_calls: [], name: "", files: files.map((f) => f.name) }]);
    let newId = cid;
    // Update the newest step with this name that is still open
    const settle = (name: string, patch: Partial<Step>) => setSteps((s) => {
      const i = s.map((x) => x.name === name && (x.status === "running" || x.status === "approval")).lastIndexOf(true);
      return i < 0 ? s : s.map((x, j) => (j === i ? { ...x, ...patch, status: x.status === "approval" && patch.status !== "approval" ? "approval" : patch.status ?? x.status } : x));
    });
    await stream("/assistant/chat", { message: msg, conversation_id: cid, model: model || null, file_ids: files.map((f) => f.id), project_id: cid ? null : startProject }, (ev) => {
      const t = ev.type as string | undefined;
      if (t === "start") { newId = Number(ev.conversation_id); setRunning(newId); }
      if (t === "memories") setUsed((ev.items as Used[]) ?? []);
      if (t === "tool_call") setSteps((s) => [...s, { name: String(ev.name), args: String(ev.args), status: "running", startedAt: Date.now() }]);
      if (t === "approval") { settle(String(ev.name), { status: "approval" }); loadPending(); }
      if (t === "tool_result") {
        settle(String(ev.name), { status: ev.ok ? "ok" : "error", preview: String(ev.preview), detail: ev.detail ? String(ev.detail) : undefined, ms: Number(ev.ms) || undefined });
        turnTools.push({ name: String(ev.name), ok: !!ev.ok });
        const got = validateAll(ev.blocks);
        if (got.length) { turnBlocks.push(...got); setLiveBlocks([...turnBlocks]); }
        if (ev.name === "web_search" && !ev.ok && /not set up/i.test(String(ev.preview)))
          setNotes((n) => n.some((x) => x.search) ? n : [...n, { id: Date.now(), kind: "warn", text: "Web search isn't turned on yet, so ROOKS couldn't look this up. A free Tavily key takes a minute (1,000 searches a month, no card).", search: true }]);
      }
      if (t === "message") {
        setMessages((m) => [...m, { id: Date.now() + 1, role: "assistant", content: String(ev.content), tool_calls: [], name: "",
          live: { blocks: [...turnBlocks], tools: [...turnTools] } }]);
        setLiveBlocks([]);
        if (!searchOn && /web search/i.test(String(ev.content)))
          setNotes((n) => n.some((x) => x.search) ? n : [...n, { id: Date.now() + 2, kind: "warn", text: "Web search isn't turned on yet. A free Tavily key takes a minute (1,000 searches a month, no card).", search: true }]);
      }
      if (t === "notice") note("warn", String(ev.text));
      if (t === "error" || (!t && ev.error)) {
        const text = String(ev.error);
        const fixable = /key|credit|model|provider|Settings|tools|connected|reach/i.test(text);
        note("err", text, { fix: fixable, retry: msg });
      }
    });
    setBusy(false);
    setRunning(null);
    setLiveBlocks([]);
    loadConvs();
    loadPending();
    if (newId && newId !== cid) { keepTurn.current = true; setConvProject(startProject); navigate(`/assistant/${newId}`, { replace: true }); }
    if (newId) loadConversation(newId);  // the stored messages carry real ids (for edit and regenerate) and rebuilt results
  };
  const regenerate = (v: View) => {
    const i = messages.findIndex((m) => m.id === v.id);
    const q = [...messages.slice(0, i)].reverse().find((m) => m.role === "user" && m.created_at);
    if (q) send(undefined, splitAttachments(q.content).text || q.content, undefined, undefined, q);
  };
  const copy = (t: string) => navigator.clipboard?.writeText(t).then(() => toast.ok("Copied")).catch(() => toast.err("Copying is not allowed here"));
  const startWith = (s: (typeof STARTS)[number]) => {
    if (s.send) send(undefined, s.send);
    else if (s.attach) fileInput.current?.click();
    else if (s.fill) { setText(s.fill); setTimeout(() => { inputRef.current?.focus(); inputRef.current?.setSelectionRange(s.fill!.length, s.fill!.length); }, 0); }
  };

  const lastUserText = () => [...messages].reverse().find((m) => m.role === "user")?.content ?? "";
  const runCommand = async (cmd: string, arg: string) => {
    try {
      if (cmd === "/help") note("info", COMMANDS.map(([c, d]) => `${c}  ${d}`).join("\n"));
      else if (cmd === "/mission") {
        if (arg.length < 5) { note("info", "Describe the job after /mission, e.g. /mission compare three CRM tools and save a summary note. ROOKS plans it first; nothing runs until you approve the plan."); return; }
        const m = await api<{ id: number }>("/missions", { body: { goal: arg, model: model || null } });
        navigate(`/missions/${m.id}`);
      }
      else if (cmd === "/new") { navigate("/assistant"); setMessages([]); setNotes([]); }
      else if (cmd === "/stop") { if (running) await stop(); else note("info", "Nothing is running."); }
      else if (cmd === "/retry") { const last = lastUserText(); if (last) await send(undefined, last); else note("info", "There's no earlier message to send again."); }
      else if (cmd === "/model") {
        if (!arg) {
          note("info", model ? `This chat uses ${shortRef(model, models)} (picked by you; it won't switch on failure). /model auto goes back to the default.`
            : autoRef ? `This chat uses the default: ${shortRef(autoRef, models)}, with automatic backups. Switch with /model <name>.` : "No model is connected yet.", { fix: !autoRef });
          return;
        }
        if (arg === "auto") { setModel(""); if (cid) await api(`/conversations/${cid}`, { method: "PATCH", body: { model: "" } }); note("info", "Back to the default model, with automatic backups."); return; }
        const exact = models.filter((m) => m.ref === arg || m.model === arg);
        const found = exact.length ? exact : models.filter((m) => m.model.toLowerCase().includes(arg.toLowerCase()));
        if (found.length !== 1) { note("warn", found.length ? `${found.length} models match "${arg}": ${found.slice(0, 8).map((m) => m.model).join(", ")}${found.length > 8 ? "…" : ""}` : `No model matches "${arg}". Try /models.`); return; }
        setModel(found[0].ref);
        if (cid) await api(`/conversations/${cid}`, { method: "PATCH", body: { model: found[0].ref } });
        note("info", `This chat now uses ${found[0].model} (${found[0].provider}). /model auto switches back.`);
      } else if (cmd === "/models") {
        const free = models.filter((m) => m.free);
        note("info", models.length ? `${models.length} models available, ${free.length} free.${free.length ? `\nFree: ${free.slice(0, 12).map((m) => m.model).join(", ")}${free.length > 12 ? "…" : ""}` : ""}\nSwitch with /model <name>.` : "No models yet.", { fix: true });
      } else if (cmd === "/status") {
        const st = await api<MStatus>("/models/status");
        const prov = st.providers.map((p) => `${p.name}: ${!p.checked ? "not checked" : p.ok ? "working" : `problem (${p.error.slice(0, 100)})`}`).join("\n");
        note(st.default.error ? "warn" : "info", `${st.default.ref ? `Default: ${shortRef(st.default.ref, models)} (${st.default.source === "chosen" ? "your choice" : "automatic"})` : st.default.error}${model ? `\nThis chat: ${shortRef(model, models)}` : ""}\nBackups: ${st.fallbacks.map((r) => shortRef(r, models)).join(", ") || "none"}\n${prov}`, { fix: true });
      } else if (cmd === "/usage") {
        const u = await api<{ month_cents: number; month_tokens: number }>("/usage");
        note("info", `This month: ${u.month_tokens.toLocaleString()} tokens, $${(u.month_cents / 100).toFixed(2)} where prices are known.`);
      }
    } catch (e) { note("err", (e as Error).message); }
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
    if (!confirm(`Forget "${m.content.slice(0, 80)}"? ROOKS will no longer recall it.`)) return;
    try { await api(`/memories/${m.id}`, { method: "DELETE" }); setUsed((u) => u.filter((x) => x.id !== m.id)); toast.ok("Forgotten"); } catch (e) { toast.err(e); }
  };

  const slashMatches = text.startsWith("/") && !text.includes(" ") ? COMMANDS.filter(([c]) => c.startsWith(text.toLowerCase())) : [];
  const slashOpen = slashMatches.length > 0 && !(slashMatches.length === 1 && slashMatches[0][0] === text.trim());
  const view = toView(messages);
  const lastUser = view.map((m) => m.role).lastIndexOf("user");
  const lastAnswer = view.map((m) => m.role).lastIndexOf("assistant");
  const doneSteps = steps.filter((s) => s.status === "ok").length;
  const current = [...steps].reverse().find((s) => s.status === "running" || s.status === "approval");
  const failed = steps.filter((s) => s.status === "error").length;
  const stepList = (
    <ol className="tsteps" aria-label="Steps">
      {steps.map((s, i) => (
        <li key={i} className={`tstep ${s.status}`}>
          <div className="row" style={{ gap: 6 }}>
            {s.status === "running" ? <Loader2 size={12} className="spin" /> : s.status === "ok" ? <Check size={12} /> : s.status === "approval" ? <ShieldAlert size={12} /> : <X size={12} style={{ color: "var(--danger)" }} />}
            <span>{STEP_LABEL[s.name] ?? s.name}</span>
            <span className="faint">{s.status === "approval" ? "waiting for your approval" : s.status === "running" ? "working…" : s.status === "error" ? "needs attention" : "done"}{s.ms !== undefined ? ` · ${s.ms < 1000 ? `${s.ms} ms` : `${(s.ms / 1000).toFixed(1)} s`}` : ""}</span>
          </div>
          {s.detail ? (() => {
            const badge = s.name === "run_command" ? exitBadge(s.detail) : null;
            return (
              <details open={s.status === "error"}>
                <summary className="mono truncate" style={{ cursor: "pointer" }}>
                  {badge && <span className={`badge ${badge.tone}`} style={{ marginRight: 6 }}>{badge.text}</span>}
                  {argSummary(s.args, s.name) || "no arguments"}
                </summary>
                <pre className="terminal" aria-label={`Output of ${STEP_LABEL[s.name] ?? s.name}`}>{s.detail.replace(/^Error: (?=\$ )/, "")}</pre>
              </details>
            );
          })() : (
            <details>
              <summary className="faint truncate" style={{ cursor: "pointer" }}>{argSummary(s.args, s.name) || "no arguments"}</summary>
              {s.preview && <div className="mono faint" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", marginTop: 4 }}>{s.preview}</div>}
            </details>
          )}
        </li>
      ))}
    </ol>
  );
  // The live activity of the turn in progress (or just finished): one line, expandable to every step
  const activity = steps.length > 0 && (
    <div className={`activity ${failed ? "has-error" : ""}`}>
      <button type="button" className="activity-head" aria-expanded={openSteps || !!current?.status.match(/approval/)} onClick={() => setOpenSteps(!openSteps)}>
        {busy ? <Loader2 size={13} className="spin" /> : failed ? <X size={13} style={{ color: "var(--danger)" }} /> : <Check size={13} />}
        <span className="grow truncate">{current ? `${STEP_LABEL[current.name] ?? current.name}${current.status === "approval" ? ": waiting for your approval" : "…"}`
          : `${busy ? "Working" : "Used"} ${steps.length} ${steps.length === 1 ? "step" : "steps"}${failed ? `, ${failed} needed attention` : ""}`}</span>
        <span className="faint small">{doneSteps}/{steps.length}</span>
        {openSteps ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {(openSteps || current?.status === "approval") && stepList}
    </div>
  );

  return (
    <div className={`split ${showList ? "" : "has-selection"}`} style={{ height: "calc(100vh - 0px)" }}>
      <div className="split-list">
        <div className="col" style={{ padding: 14, borderBottom: "1px solid var(--border)" }}>
          <div className="row"><h2 className="grow">Conversations</h2><button className="btn primary sm" onClick={() => { setShowList(false); navigate("/assistant"); }}><Plus size={14} /> New</button>
            <button type="button" className="btn ghost sm icon mobile-only" onClick={() => setShowList(false)} aria-label="Back to the chat" title="Back to the chat"><X size={15} /></button></div>
          <div className="row" style={{ position: "relative" }}>
            <Search size={14} className="faint" style={{ position: "absolute", left: 10 }} />
            <input className="input" style={{ paddingLeft: 30 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search all conversations" aria-label="Search all conversations" />
          </div>
        </div>
        <div className="scroll">
          {convs.length === 0 && <Empty icon={<MessageSquare size={22} />} title={q ? "No matches" : "No conversations yet"} />}
          {convs.map((c) => (
            <div key={c.id} className={`list-item ${c.id === cid ? "active" : ""}`} onClick={() => navigate(`/assistant/${c.id}`)}>
              <div className="grow">
                <div className="truncate">{c.title}</div>
                <div className="faint small">{CHANNEL[c.channel] ?? c.channel} · {relTime(c.updated_at)}
                  {c.project_id ? ` · ${projectList.find((p) => p.id === c.project_id)?.name ?? "project"}` : ""}</div>
              </div>
              <button className="btn ghost sm icon" aria-label={`Delete conversation ${c.title}`} title="Delete conversation" onClick={(e) => { e.stopPropagation(); remove(c); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main chat-main"
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragOver(true); } }}
        onDragLeave={(e) => { if (e.currentTarget === e.target) setDragOver(false); }}
        onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); setDragOver(false); attach(e.dataTransfer.files); } }}>
        {dragOver && <div className="drop-hint" aria-hidden="true">Drop to attach</div>}
        <div className="toolbar">
          <button type="button" className="btn ghost sm icon mobile-only" onClick={() => setShowList(true)} aria-label="Show conversations" title="Conversations"><MessageSquare size={16} /></button>
          <strong className="truncate grow chat-title" style={{ minWidth: 0 }}>{convs.find((c) => c.id === cid)?.title ?? (cid ? "Conversation" : "New conversation")}</strong>
          {!voice && <button type="button" className="btn sm" onClick={() => setVoice({})} disabled={busy} title="Talk with ROOKS: it listens, answers aloud, and you can interrupt"><Mic size={14} /> Voice</button>}
          {tabs}
        </div>
        <div className="chat-scroll">
          <div className="chat-column col gap-lg">
            {view.length === 0 && !busy && (
              <div className="chat-start">
                <div className="chat-hello"><Sparkles size={22} aria-hidden="true" /><h2>What can ROOKS do for you?</h2>
                  <p className="muted">Ask in plain English. Results come back as tables, cards and charts you can act on. Anything with lasting effects after reading the web waits for your approval.</p></div>
                {!noModel && <div className="start-grid" role="list">{STARTS.map((s) => (
                  <button key={s.title} type="button" role="listitem" className="start-card" onClick={() => startWith(s)}>
                    <s.icon size={18} aria-hidden="true" /><span className="start-title">{s.title}</span><span className="start-about">{s.about}</span>
                  </button>))}</div>}
              </div>
            )}
            {noModel && view.length === 0 && (
              <div className="alert" role="status">ROOKS needs an AI model before it can answer. <Link to="/settings#models">Connect one in Settings</Link>; a free OpenRouter or Gemini key works.</div>
            )}
            {view.map((m, i) => (
              <Fragment key={m.id}>
                {m.role === "user"
                  ? (() => {
                      const parsed = splitAttachments(m.content);
                      const names = m.files ?? parsed.files;
                      return (
                        <div className="msg-user">
                          <div className="bubble-user">
                            {parsed.text}
                            {names.length > 0 && <div className="row wrap" style={{ marginTop: 6, gap: 6 }}>{names.map((n, i) => <span key={i} className="attach-chip"><Paperclip size={12} aria-hidden="true" /><span className="name">{n}</span></span>)}</div>}
                          </div>
                          {m.created_at && !busy && cid && <div className="msg-actions">
                            <button type="button" className="btn ghost sm" onClick={() => { setEditing(m); setText(parsed.text); setTimeout(() => inputRef.current?.focus(), 0); }} title="Edit and send again"><Pencil size={12} /> Edit</button>
                          </div>}
                        </div>
                      );
                    })()
                  : (() => {
                      const sourceBlocks = m.blocks.filter((b) => b.type === "sources");
                      const cites = sourceBlocks.length === 1 && sourceBlocks[0].type === "sources" ? sourceBlocks[0].items.map((x, k) => x.n ?? k + 1) : [];
                      const showLive = i === lastAnswer && steps.length > 0;
                      return (
                        <div className="msg-assistant col" style={{ gap: 8 }}>
                          {showLive ? activity : m.tools.length > 0 && (
                            <details className="activity done">
                              <summary className="activity-head"><Wrench size={12} aria-hidden="true" /><span className="grow truncate">{(() => { const l = m.tools.map((t) => STEP_LABEL[t.name] ?? t.name).filter((v, k, a) => a.indexOf(v) === k).join(", ").toLowerCase(); return l.charAt(0).toUpperCase() + l.slice(1); })()}</span>
                                {m.tools.some((t) => !t.ok) && <span className="badge warn">{m.tools.filter((t) => !t.ok).length} failed</span>}</summary>
                              <ol className="tsteps">{m.tools.map((t, k) => <li key={k} className={`tstep ${t.ok ? "ok" : "error"}`}>{t.ok ? <Check size={12} /> : <X size={12} />} {STEP_LABEL[t.name] ?? t.name}</li>)}</ol>
                            </details>
                          )}
                          {m.content && <RichText text={m.content} sources={cites} />}
                          <Blocks blocks={m.blocks} />
                          {m.content && !m.tool_calls.length && (
                            <div className="msg-actions">
                              <button type="button" className="btn ghost sm" onClick={() => copy(m.content)} title="Copy the answer"><Copy size={12} /> Copy</button>
                              {i === lastAnswer && !busy && cid && <button type="button" className="btn ghost sm" onClick={() => regenerate(m)} title="Ask again for a new answer"><RotateCcw size={12} /> Regenerate</button>}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                {i === lastUser && busy && <div className="msg-assistant col" style={{ gap: 8 }}>{activity}<Blocks blocks={liveBlocks} /></div>}
              </Fragment>
            ))}
            {notes.map((n) => (
              <div key={n.id} className={`chat-note ${n.kind === "err" ? "err" : n.kind === "warn" ? "warn" : ""}`} role={n.kind === "err" ? "alert" : "status"}>
                <div style={{ whiteSpace: "pre-wrap" }}>{n.text}</div>
                {(n.fix || n.retry || n.search) && (
                  <div className="row wrap" style={{ marginTop: 8 }}>
                    {n.search && <Link className="btn sm primary" to="/settings#search">Set up web search</Link>}
                    {n.fix && <Link className="btn sm" to="/settings#models">Open AI model settings</Link>}
                    {n.retry && !busy && <button type="button" className="btn sm" onClick={() => send(undefined, n.retry)}>Try again</button>}
                  </div>
                )}
              </div>
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
            {busy && steps.length === 0 && <div className="row muted thinking" role="status"><Loader2 size={15} className="spin" /> Thinking…</div>}
            <Approvals items={pending} onDone={loadPending} toast={toast} />
            <div ref={endRef} />
          </div>
        </div>
        {voice && <VoiceSession key={cid ?? "new"} conversationId={cid} projectId={projectId} opening={voice.opening} onClose={voiceClosed} />}
        <form className="composer-wrap" onSubmit={send}>
          {editing && <div className="composer-editing row small"><Pencil size={12} /><span className="grow">Editing your message: sending replaces it and everything after it.</span>
            <button type="button" className="btn ghost sm" onClick={() => { setEditing(null); setText(""); }}>Cancel</button></div>}
          <div className="composer">
            {slashOpen && (
              <div className="slash-menu" role="listbox" aria-label="Commands">
                {slashMatches.map(([c, d], i) => (
                  <button type="button" key={c} role="option" aria-selected={i === menuIdx} onMouseDown={(e) => { e.preventDefault(); setText(`${c} `); }}>
                    <span className="mono">{c}</span><span className="muted small">{d}</span>
                  </button>
                ))}
              </div>
            )}
            {attached.length > 0 && (
              <div className="row wrap" style={{ gap: 6 }} aria-label="Attached files">
                {attached.map((f) => (
                  <span key={f.id} className="attach-chip"><Paperclip size={12} aria-hidden="true" /><span className="name">{f.name}</span>
                    <button type="button" className="btn ghost sm icon" style={{ height: 20, width: 20, minHeight: 0, minWidth: 0 }} onClick={() => setAttached((a) => a.filter((x) => x.id !== f.id))} aria-label={`Remove ${f.name}`} title="Remove"><X size={11} /></button></span>
                ))}
              </div>
            )}
            <textarea ref={inputRef} className="composer-input" rows={1} value={text} onChange={(e) => { setText(e.target.value); setMenuIdx(0); e.target.style.height = "auto"; e.target.style.height = `${Math.min(e.target.scrollHeight, 240)}px`; }}
              aria-label="Message ROOKS" placeholder="Ask ROOKS anything. / for commands"
              aria-autocomplete="list" aria-expanded={slashOpen}
              onKeyDown={(e) => {
                if (slashOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); setMenuIdx((i) => (i + (e.key === "ArrowDown" ? 1 : slashMatches.length - 1)) % slashMatches.length); return; }
                if (slashOpen && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && text.trim() !== slashMatches[menuIdx][0]))) { e.preventDefault(); setText(`${slashMatches[menuIdx][0]} `); return; }
                if (slashOpen && e.key === "Escape") { e.preventDefault(); setText(""); return; }
                if (e.key === "Escape" && editing) { setEditing(null); setText(""); return; }
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }} />
            <div className="composer-bar">
              <button type="button" className="btn ghost icon" onClick={() => fileInput.current?.click()} aria-label="Attach files" title="Attach files (PDF, Word, spreadsheets, text)" disabled={uploading > 0 || attached.length >= 10}>
                {uploading ? <Loader2 size={16} className="spin" /> : <Paperclip size={16} />}
              </button>
              <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) attach(e.target.files); e.target.value = ""; }} />
              <button type="button" className="btn ghost icon" onClick={() => setVoice({})} disabled={busy || !!voice} aria-label="Start a voice conversation" title="Talk instead of typing"><Mic size={16} /></button>
              <div className="composer-model"><ModelSelect value={model} onChange={setModel} models={models} defaultLabel={autoRef ? `Automatic (${shortRef(autoRef, models)})` : "Automatic"} /></div>
              {project && <Link to={`/projects/${project.id}`} className="badge accent truncate" style={{ maxWidth: 180 }} title="This conversation works in a project: its brief, memories and rules apply">{project.name}</Link>}
              <div className="spacer" />
              <span className="faint small hide-narrow">Shift+Enter for a new line</span>
              {busy && running
                ? <button type="button" className="btn danger-solid" onClick={stop} aria-label="Stop" title="Stop after the current step"><Square size={13} /> Stop</button>
                : <button className="btn primary" disabled={busy || uploading > 0 || (!text.trim() && !attached.length)} aria-label="Send message" title="Send"><Send size={15} /></button>}
            </div>
          </div>
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
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const toast = useToast();
  const load = () => api<Memory[]>(`/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`).then(setItems).catch(toast.err);
  const correct = async (m: Memory) => {
    try { await api(`/memories/${m.id}`, { method: "PUT", body: { content: editing!.text, kind: m.kind } }); setEditing(null); toast.ok("Corrected. The earlier wording is kept in case you want it back."); load(); }
    catch (e) { toast.err(e); }
  };
  const revert = async (m: Memory) => {
    try { await api(`/memories/${m.id}/revert`, { method: "POST" }); toast.ok("Change undone"); load(); } catch (e) { toast.err(e); }
  };
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
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memories (try everyday words: partner, doctor, holiday)" aria-label="Search memories" />
      {q && <div className="small muted">Best matches first. Each shows how it was found.</div>}
      {items.length === 0 && <Empty icon={<Brain size={24} />} title="Nothing remembered yet" hint="Chat normally; it learns as you go." />}
      {groups.filter(([, l]) => l.length).map(([title, list]) => (
        <div key={title}>
          <h3 style={{ marginBottom: 6 }}>{title}</h3>
          <div className="card flush">
            {list.map((m) => (
              <div key={m.id} className="list-item" style={{ cursor: "default", alignItems: "flex-start" }}>
                <div className="grow">{editing?.id === m.id ? (
                  <form className="row wrap" onSubmit={(e) => { e.preventDefault(); correct(m); }}>
                    <input className="input grow" aria-label="Corrected memory" value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} autoFocus />
                    <button className="btn primary sm" disabled={editing.text.trim().length < 3}>Save</button><button type="button" className="btn ghost sm" onClick={() => setEditing(null)}>Cancel</button>
                  </form>) : m.content}
                {m.matched_by && <div style={{ marginTop: 3 }}>{m.matched_by.map((x) => <span key={x} className="tag">found by {x}</span>)}</div>}
                {m.previous && <div className="small muted" style={{ marginTop: 3 }}>Before the last change: <s>{m.previous}</s> <button className="btn ghost sm" onClick={() => revert(m)}><RotateCcw size={11} /> Undo change</button></div>}
                <div className="faint small">
                  {SOURCE[m.source] ?? m.source}
                  {m.confidence !== undefined && <> · <span title="How sure ROOKS is: 100% for what you told it, lower for what it inferred">{Math.round(m.confidence * 100)}% sure</span></>}
                  {m.created_at && <> · <span title={new Date(m.created_at).toLocaleString()}>added {relTime(m.created_at)}</span></>}
                  {m.created_at && m.updated_at !== m.created_at && <> · edited {relTime(m.updated_at)}</>}
                  {m.conversation_id ? <> · <a href={`/assistant/${m.conversation_id}`}>from this chat</a></> : null}
                </div></div>
                <button className="btn ghost sm icon" aria-label="Correct memory" title="Correct it" onClick={() => setEditing({ id: m.id, text: m.content })}><Pencil size={13} /></button>
                <button className="btn ghost sm icon" aria-label="Delete memory" title="Delete memory" onClick={async () => { if (!confirm(`Delete this memory? ROOKS will no longer recall it.\n\n${m.content.slice(0, 120)}`)) return; await api(`/memories/${m.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      ))}
      <MeaningSearch />
      {toast.node}
    </div>
  );
}

function MeaningSearch() {
  const [st, setSt] = useState<MemStatus | null>(null);
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => { api<MemStatus>("/memory/status").then((s) => { setSt(s); setRef(s.embed_model); }).catch(() => undefined); }, []);
  const save = async (value: string) => {
    setBusy(true);
    try { const s = await api<MemStatus>("/memory/embeddings", { method: "PUT", body: { model: value } }); setSt(s); setRef(s.embed_model); toast.ok(value ? "Search by meaning is on" : "Search by meaning is off"); }
    catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  if (!st) return null;
  return (
    <details className="card">
      <summary style={{ cursor: "pointer" }}><strong>Search by meaning</strong> <span className="muted small">(optional) {st.embed_model ? `on, ${st.indexed} of ${st.memories} indexed` : "off"}</span></summary>
      <div className="col" style={{ marginTop: 8 }}>
        <p className="muted small" style={{ margin: 0 }}>Keyword search already understands common everyday wording and typos. For wording it does not know, connect an embedding model from a provider you already use, for example <span className="mono">nomic-embed-text</span> in Ollama. Memories are sent to that provider to be indexed, so pick a local one if they must stay on this computer.</p>
        <label className="field">Embedding model <span className="hint">provider id/model, as in Settings, Models</span>
          <input className="input mono" value={ref} onChange={(e) => setRef(e.target.value)} placeholder="1/nomic-embed-text" /></label>
        {st.error && <div className="alert err">{st.error}</div>}
        <div className="row"><button className="btn sm" disabled={busy || !ref.trim()} onClick={() => save(ref.trim())}>{busy ? "Testing…" : "Test and turn on"}</button>
          {st.embed_model && <button className="btn ghost sm" disabled={busy} onClick={() => save("")}>Turn off</button>}</div>
      </div>
      {toast.node}
    </details>
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
            {s.warnings && s.warnings.length > 0 && <div className="alert" style={{ padding: "4px 8px" }}>May not work: {s.warnings.join("; ")}.</div>}
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
      <WebSearchPanel />
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
