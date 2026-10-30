import {
  Activity, Gauge, Bot, Brain, Building2, CalendarClock, CalendarDays, ChevronRight, MessageSquare, CheckSquare, Globe, Columns3, ExternalLink, FileText, Home, LogOut, Mail, Menu, NotebookPen,
  Code2, FolderOpen, Globe2, GraduationCap, ListTree, Monitor, Palette as PaletteIcon, Sparkles, Puzzle, Search, Settings as SettingsIcon,
  Layers, // feat/v02-automations
  AudioLines, Users, // feat/v02-anywhere-voice
  Package, // feat/v02-packs
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CommandPalette } from "./Palette";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import { EdgeGlow, transitionClick } from "./motion";
import Assistant from "./pages/Assistant";
import Auth from "./pages/Auth";
import Appearance from "./pages/Appearance";
import MotionLab from "./pages/Motion";
import Automations from "./pages/Automations";
import Company from "./pages/Company";
import Browser from "./pages/Browser";
import Code from "./pages/Code";
import Computer from "./pages/Computer";
import CalendarPage from "./pages/Calendar";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import Docs from "./pages/Docs";
import FilesPage from "./pages/Files";
import SitesPage from "./pages/Sites";
import MailPage from "./pages/Mail";
import Research from "./pages/Research";
import Runs from "./pages/Runs";
import Status from "./pages/Status";
import Settings from "./pages/Settings";
import Skills from "./pages/Skills";
import Tasks from "./pages/Tasks";
import Welcome from "./pages/Welcome";
import Models from "./pages/Models";
import AboutYou from "./pages/AboutYou"; // adapt and learn (feat/learn-and-adapt)
import Teach from "./pages/Teach"; // adapt and learn (feat/learn-and-adapt)
import Plan from "./pages/Plan";
import { AccountLayer } from "./ProGate";
import JobsPage from "./v02/Jobs"; // feat/v02-automations: background jobs board
import JobNotifier from "./v02/JobNotifier"; // feat/v02-automations: desktop notifications for jobs
// feat/v02-anywhere-voice: ask bar, Talk, Meetings, their settings
import AskBar from "./pages/AskBar";
import Meetings from "./pages/Meetings";
import Talk from "./pages/Talk";
import VoiceSettings from "./pages/VoiceSettings";
import { FolderKanban, History as HistoryIcon } from "lucide-react"; // feat/v02-core
import Projects from "./pages/Projects"; // feat/v02-core
import HistoryPage from "./pages/History"; // feat/v02-core
import Packs from "./pages/Packs"; // feat/v02-packs
import PackTrade from "./pages/PackTrade"; // feat/v02-packs
import PackLegal from "./pages/PackLegal"; // feat/v02-packs
import PackStudy from "./pages/PackStudy"; // feat/v02-packs

export type Status = {
  setup_complete: boolean;
  signed_in: boolean;
  setup_code_required: boolean;
  workspace_name: string;
  chat_url: string;
  paperclip_url: string;
  hermes_url: string;
  browser_enabled: boolean;
  /** Rooks Desk (computer use) is running on this machine. */
  desk_enabled?: boolean;
  local_models_enabled: boolean;
  /** Running inside the native desktop app (desktop/), which signs the window in itself. */
  desktop?: boolean;
  welcome_done?: boolean;
  needs_models?: boolean;
};

type NavItem = { to: string; label: string; icon: typeof Home; addon?: "browser" | "desk" };
type NavGroup = { section?: string; collapsible?: boolean; items: NavItem[] };

/**
 * integration/v0.2 navigation, grouped so a non-technical person sees a short list:
 * the four places you live in, your day, your work, the helpers that act for you, then More and Advanced folded away.
 */
const NAV: NavGroup[] = [
  { items: [
    { to: "/", label: "Home", icon: Home },
    { to: "/assistant", label: "Chat", icon: MessageSquare },
    { to: "/projects", label: "Projects", icon: FolderKanban }, // feat/v02-core
    { to: "/jobs", label: "Jobs", icon: Layers }, // feat/v02-automations
  ] },
  { section: "Your day", items: [
    { to: "/mail", label: "Mail", icon: Mail },
    { to: "/calendar", label: "Calendar", icon: CalendarDays },
    { to: "/tasks", label: "Tasks", icon: CheckSquare },
    { to: "/notes", label: "Notes", icon: NotebookPen },
  ] },
  { section: "Your work", items: [
    { to: "/files", label: "Files", icon: FolderOpen },
    { to: "/sites", label: "Sites", icon: Globe2 },
    { to: "/automations", label: "Automations", icon: CalendarClock },
    { to: "/teach", label: "Show me how", icon: GraduationCap },
    { to: "/packs", label: "Packs", icon: Package }, // feat/v02-packs
    { to: "/meetings", label: "Meetings", icon: Users }, // feat/v02-anywhere-voice
  ] },
  { section: "Helpers", items: [
    { to: "/talk", label: "Talk", icon: AudioLines }, // feat/v02-anywhere-voice
    { to: "/research", label: "Research", icon: Search },
    { to: "/code", label: "Coding", icon: Code2 },
    { to: "/browser", label: "Browser", icon: Globe, addon: "browser" },
    { to: "/computer", label: "Computer", icon: Monitor, addon: "desk" },
  ] },
  { section: "More", collapsible: true, items: [
    { to: "/skills", label: "Skills", icon: Puzzle },
    { to: "/history", label: "History", icon: HistoryIcon }, // feat/v02-core: everything done, with undo
    { to: "/about-you", label: "About you", icon: Brain },
    { to: "/plan", label: "Your plan", icon: Sparkles },
    { to: "/settings", label: "Settings", icon: SettingsIcon },
    { to: "/appearance", label: "Appearance", icon: PaletteIcon },
    { to: "/models", label: "Models", icon: Gauge },
    { to: "/voice", label: "Ask anywhere & voice", icon: AudioLines }, // feat/v02-anywhere-voice
  ] },
  { section: "Advanced", collapsible: true, items: [
    { to: "/documents", label: "Documents", icon: FileText },
    { to: "/company", label: "Company", icon: Building2 },
    { to: "/compare", label: "Compare models", icon: Columns3 },
    { to: "/runs", label: "Run log", icon: ListTree },
    { to: "/status", label: "System status", icon: Activity },
  ] },
];

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MOD_KEY = isMac ? "⌘" : "Ctrl";

/** While Rooks Desk is driving the computer (from any page, chat or Rooks Code), the whole window's edge lights up. */
function DeskGlow({ enabled }: { enabled: boolean }) {
  const [job, setJob] = useState<{ id: string; goal: string } | null>(null);
  useEffect(() => {
    if (!enabled) return setJob(null);
    let stop = false;
    let t: ReturnType<typeof setTimeout>;
    const tick = () => api<{ id: string; goal: string; status: string }[]>("/desk/jobs")
      .then((js) => { if (!stop) setJob(js.find((j) => j.status === "running" || j.status === "queued" || j.status === "waiting") ?? null); })
      .catch(() => undefined)
      .finally(() => { if (!stop) t = setTimeout(tick, document.hidden ? 8000 : 2500); });
    tick();
    return () => { stop = true; clearTimeout(t); };
  }, [enabled]);
  return <EdgeGlow active={!!job} hotkey={isMac ? "⌃⌥Esc" : "Ctrl+Alt+Esc"}
    onStop={job ? () => { api(`/desk/jobs/${job.id}/cancel`, { method: "POST" }).catch(() => undefined); setJob(null); } : undefined} />;
}

function Boot() {
  return <div className="boot" role="status" aria-label="Loading"><div className="wordmark lg">AI Workspace</div></div>;
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  const refresh = useCallback(() => {
    api<Status>("/status")
      .then((s) => {
        setStatus(s);
        setError("");
        document.title = `${s.workspace_name} · Workbench`;
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(() => {
    const onUnauthorized = () => setStatus((s) => (s ? { ...s, signed_in: false } : s));
    window.addEventListener("wb:unauthorized", onUnauthorized);
    return () => window.removeEventListener("wb:unauthorized", onUnauthorized);
  }, []);
  // feat/v02-anywhere-voice: "Do it in Chat" from the ask bar (the desktop shell calls this in the main window)
  useEffect(() => {
    (window as unknown as { __wbHandoff?: (d: string) => void }).__wbHandoff = (draft) => navigate("/assistant", { state: { draft, at: Date.now() } });
  }, [navigate]);
  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  // Folded groups (More, Advanced): remembered per group, and always open while you're on one of their pages
  const [folds, setFolds] = useState<Record<string, boolean>>(() => ({
    More: localStorage.getItem("wb-nav-more") === "1", Advanced: localStorage.getItem("wb-nav-advanced") === "1" }));
  const nav = useMemo(
    () => NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.addon || (i.addon === "browser" && status?.browser_enabled) || (i.addon === "desk" && status?.desk_enabled)) })),
    [status?.browser_enabled, status?.desk_enabled],
  );
  const palettePages = useMemo(
    () => [...nav.flatMap((g) => g.items.map((i) => ({ to: i.to, label: i.label, icon: i.icon })))],
    [nav],
  );
  const inGroup = (g: NavGroup) => g.items.some((i) => location.pathname === i.to || location.pathname.startsWith(i.to + "/"));
  const toggleFold = (name: string) => setFolds((f) => {
    const open = !f[name];
    localStorage.setItem(name === "More" ? "wb-nav-more" : "wb-nav-advanced", open ? "1" : "0");
    return { ...f, [name]: open };
  });
  // Route transition key: top-level section only, so switching conversations or notes doesn't remount the page
  const section = "/" + (location.pathname.split("/")[1] ?? "");

  if (error) return <div className="auth"><div className="card auth-card col"><div className="alert err" role="alert">Cannot reach Workbench: {error}</div><button className="btn" onClick={refresh}>Try again</button></div></div>;
  if (!status) return <Boot />;
  if (!status.setup_complete || !status.signed_in) {
    if (status.desktop) {
      return <div className="auth"><div className="card auth-card"><div className="alert" role="status">Your session ended. Close and reopen AI Workspace to sign back in.</div></div></div>;
    }
    return <Auth status={status} onDone={refresh} />;
  }
  if (location.pathname === "/ask-bar") return <AskBar />; // feat/v02-anywhere-voice: the floating window, no sidebar
  if ((status.desktop || status.needs_models) && !status.welcome_done) {
    return <Welcome onDone={() => { refresh(); navigate("/assistant", { replace: true }); }} />;
  }

  const logout = async () => {
    await api("/logout", { method: "POST" });
    refresh();
  };

  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="wordmark truncate">{status.workspace_name}</span>
        </div>
        <button type="button" className="sidebar-search" onClick={() => setPaletteOpen(true)} aria-keyshortcuts="Meta+K Control+K">
          <Search size={14} /> <span>Search</span> <span className="kbd">{MOD_KEY} K</span>
        </button>
        {nav.map((g, gi) => {
          const open = !g.collapsible || !!folds[g.section ?? ""] || inGroup(g);
          return (
            <div key={gi} className="nav-group">
              {g.section && (g.collapsible ? (
                <button type="button" className="nav-section nav-toggle" aria-expanded={open} onClick={() => toggleFold(g.section ?? "")}>
                  <span className="grow">{g.section}</span><ChevronRight size={12} className="chev" />
                </button>
              ) : <div className="nav-section">{g.section}</div>)}
              {open && g.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link" onClick={transitionClick(() => navigate(item.to))}>
                  {({ isActive }) => <>{isActive && <span className="nav-ind" aria-hidden="true" />}<item.icon size={16} /> <span className="truncate">{item.label}</span></>}
                </NavLink>
              ))}
              {open && g.section === "Advanced" && (
                <>
                  {status.chat_url && <a className="nav-link" href={status.chat_url} target="_blank" rel="noreferrer"><MessageSquare size={16} /> Open WebUI <ExternalLink size={12} style={{ marginLeft: "auto" }} /></a>}
                  {status.paperclip_url && <a className="nav-link" href={status.paperclip_url} target="_blank" rel="noreferrer"><Building2 size={16} /> Paperclip <ExternalLink size={12} style={{ marginLeft: "auto" }} /></a>}
                  {status.hermes_url && <a className="nav-link" href={status.hermes_url} target="_blank" rel="noreferrer"><Bot size={16} /> Hermes agent <ExternalLink size={12} style={{ marginLeft: "auto" }} /></a>}
                </>
              )}
            </div>
          );
        })}
        <div className="spacer" />
        <div className="sidebar-foot">
          <div className="spacer" />
          {!status.desktop && <button className="btn ghost sm" onClick={logout}><LogOut size={14} /> Sign out</button>}
        </div>
      </aside>
      {menuOpen && <div className="modal-back" style={{ zIndex: 35 }} onClick={() => setMenuOpen(false)} />}
      <main className="main">
        <div className="mobile-bar">
          <button className="btn ghost icon" onClick={() => setMenuOpen(true)} aria-label="Open menu" aria-expanded={menuOpen}><Menu size={18} /></button>
          <strong className="truncate grow">{status.workspace_name}</strong>
          <button className="btn ghost icon" onClick={() => setPaletteOpen(true)} aria-label="Search or jump to"><Search size={17} /></button>
        </div>
        <div className="route" key={section}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/assistant" element={<Assistant tab="chat" />} />
          <Route path="/assistant/:id" element={<Assistant tab="chat" />} />
          <Route path="/assistant-memory" element={<Assistant tab="memory" />} />
          <Route path="/assistant-skills" element={<Navigate to="/skills" replace />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/about-you" element={<AboutYou />} />
          <Route path="/teach" element={<Teach />} />
          <Route path="/teach/:id" element={<Teach />} />
          {/* feat/v02-packs */}
          <Route path="/packs" element={<Packs />} />
          <Route path="/packs/trade" element={<PackTrade />} />
          <Route path="/packs/legal" element={<PackLegal />} />
          <Route path="/packs/study" element={<PackStudy />} />
          <Route path="/appearance" element={<Appearance />} />
          <Route path="/appearance/motion" element={<MotionLab />} />
          <Route path="/models" element={<Models />} />
          <Route path="/assistant-settings" element={<Assistant tab="settings" />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/jobs" element={<JobsPage />} />{/* feat/v02-automations */}
          <Route path="/company" element={<Company />} />
          <Route path="/research" element={<Research />} />
          <Route path="/files" element={<FilesPage />} />
          <Route path="/sites" element={<SitesPage />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/code" element={<Code />} />
          <Route path="/code/:id" element={<Code />} />
          <Route path="/browser" element={<Browser />} />
          <Route path="/browser/:id" element={<Browser />} />
          <Route path="/computer" element={<Computer />} />
          <Route path="/computer/:id" element={<Computer />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/projects" element={<Projects />} />{/* feat/v02-core */}
          <Route path="/projects/:id" element={<Projects />} />{/* feat/v02-core */}
          <Route path="/history" element={<HistoryPage />} />{/* feat/v02-core */}
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<Runs />} />
          <Route path="/status" element={<Status />} />
          <Route path="/notes" element={<Docs kind="note" />} />
          <Route path="/notes/:id" element={<Docs kind="note" />} />
          <Route path="/documents" element={<Docs kind="document" />} />
          <Route path="/documents/:id" element={<Docs kind="document" />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/mail" element={<MailPage />} />
          <Route path="/settings" element={<Settings onChanged={refresh} />} />
          <Route path="/plan" element={<Plan />} />
          {/* feat/v02-anywhere-voice */}
          <Route path="/talk" element={<Talk />} />
          <Route path="/meetings" element={<Meetings />} />
          <Route path="/meetings/:id" element={<Meetings />} />
          <Route path="/voice" element={<VoiceSettings />} />
          <Route path="/welcome" element={<Navigate to="/assistant" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </div>
      </main>
      <DeskGlow enabled={!!status.desk_enabled} />
      <JobNotifier />{/* feat/v02-automations */}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} pages={palettePages} />
      <AccountLayer />
    </div>
  );
}
