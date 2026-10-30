import {
  Activity, Gauge, Bot, Brain, Building2, CalendarClock, CalendarDays, ChevronRight, MessageSquare, CheckSquare, Globe, Columns3, ExternalLink, FileText, Home, LogOut, Mail, Menu, NotebookPen,
  GraduationCap, ListTree, Palette as PaletteIcon, Puzzle, Search, Settings as SettingsIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CommandPalette } from "./Palette";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import Assistant from "./pages/Assistant";
import Auth from "./pages/Auth";
import Appearance from "./pages/Appearance";
import Automations from "./pages/Automations";
import Company from "./pages/Company";
import Browser from "./pages/Browser";
import CalendarPage from "./pages/Calendar";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import Docs from "./pages/Docs";
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

export type Status = {
  setup_complete: boolean;
  signed_in: boolean;
  setup_code_required: boolean;
  workspace_name: string;
  chat_url: string;
  paperclip_url: string;
  hermes_url: string;
  browser_enabled: boolean;
  local_models_enabled: boolean;
  /** Running inside the native desktop app (desktop/), which signs the window in itself. */
  desktop?: boolean;
  welcome_done?: boolean;
  needs_models?: boolean;
};

type NavItem = { to: string; label: string; icon: typeof Home; addon?: "browser" };
type NavGroup = { section?: string; collapsible?: boolean; items: NavItem[] };

/** Six everyday places first, the agent tools second, everything else folded under More. */
const NAV: NavGroup[] = [
  { items: [
    { to: "/", label: "Home", icon: Home },
    { to: "/assistant", label: "Chat", icon: MessageSquare },
    { to: "/tasks", label: "Tasks", icon: CheckSquare },
    { to: "/notes", label: "Notes", icon: NotebookPen },
    { to: "/calendar", label: "Calendar", icon: CalendarDays },
    { to: "/mail", label: "Mail", icon: Mail },
  ] },
  { section: "Agents", items: [
    { to: "/automations", label: "Automations", icon: CalendarClock },
    { to: "/teach", label: "Show me how", icon: GraduationCap },
    { to: "/skills", label: "Skills", icon: Puzzle },
    { to: "/research", label: "Research", icon: Search },
    { to: "/browser", label: "Browser agent", icon: Globe, addon: "browser" },
  ] },
  { section: "More", collapsible: true, items: [
    { to: "/documents", label: "Documents", icon: FileText },
    { to: "/about-you", label: "What I know", icon: Brain },
    { to: "/company", label: "Company", icon: Building2 },
    { to: "/compare", label: "Compare models", icon: Columns3 },
    { to: "/runs", label: "Run log", icon: ListTree },
    { to: "/status", label: "System status", icon: Activity },
  ] },
];

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const MOD_KEY = isMac ? "⌘" : "Ctrl";

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
  const [moreOpen, setMoreOpen] = useState(() => localStorage.getItem("wb-nav-more") === "1");
  const nav = useMemo(
    () => NAV.map((g) => ({ ...g, items: g.items.filter((i) => !i.addon || (i.addon === "browser" && status?.browser_enabled)) })),
    [status?.browser_enabled],
  );
  const palettePages = useMemo(
    () => [...nav.flatMap((g) => g.items.map((i) => ({ to: i.to, label: i.label, icon: i.icon }))),
      { to: "/appearance", label: "Appearance", icon: PaletteIcon }, { to: "/settings", label: "Settings", icon: SettingsIcon }],
    [nav],
  );
  // Keep More open while you're on one of its pages
  const inMore = NAV[2].items.some((i) => location.pathname === i.to || location.pathname.startsWith(i.to + "/"));
  const toggleMore = () => setMoreOpen((o) => { localStorage.setItem("wb-nav-more", o ? "0" : "1"); return !o; });
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
          const open = !g.collapsible || moreOpen || inMore;
          return (
            <div key={gi} className="nav-group">
              {g.section && (g.collapsible ? (
                <button type="button" className="nav-section nav-toggle" aria-expanded={open} onClick={toggleMore}>
                  <span className="grow">{g.section}</span><ChevronRight size={12} className="chev" />
                </button>
              ) : <div className="nav-section">{g.section}</div>)}
              {open && g.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
                  <item.icon size={16} /> <span className="truncate">{item.label}</span>
                </NavLink>
              ))}
              {open && g.collapsible && (
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
        <NavLink to="/models" className="nav-link"><Gauge size={16} /> Models</NavLink>
        <NavLink to="/appearance" className="nav-link"><PaletteIcon size={16} /> Appearance</NavLink>
        <NavLink to="/settings" className="nav-link"><SettingsIcon size={16} /> Settings</NavLink>
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
          <Route path="/appearance" element={<Appearance />} />
          <Route path="/models" element={<Models />} />
          <Route path="/assistant-settings" element={<Assistant tab="settings" />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/company" element={<Company />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/browser" element={<Browser />} />
          <Route path="/browser/:id" element={<Browser />} />
          <Route path="/compare" element={<Compare />} />
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
          <Route path="/welcome" element={<Navigate to="/assistant" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </div>
      </main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} pages={palettePages} />
    </div>
  );
}
