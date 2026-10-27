import {
  Bot, Brain, Flag, FolderOpen, MonitorPlay, Building2, CalendarClock, CalendarDays, MessageSquare, CheckSquare, Globe, Columns3, ExternalLink, FileText, Home, LogOut, Mail, Menu, Moon, NotebookPen,
  Palette, PanelLeftClose, PanelLeftOpen, Search, Settings as SettingsIcon, Sun, Sparkles, X, FolderKanban,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import Auth from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import RookMark from "./components/RookMark";
import { apply, cache, cached, normalise } from "./appearance";
import ComputerBar from "./components/ComputerBar";
import CallInvites from "./components/CallInvites";
import NeedsYou, { NeedsYouIcon, useAttentionCount } from "./pages/NeedsYou";
import CommandPalette, { openPalette } from "./components/CommandPalette";

// Pages load on first visit, so the first screen downloads only what it needs. Once the app is idle every page is
// fetched in the background, so moving between pages stays instant.
const preloads: (() => Promise<unknown>)[] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function page<T extends React.ComponentType<any>>(load: () => Promise<{ default: T }>) {
  preloads.push(load);
  return lazy(load);
}
const Assistant = page(() => import("./pages/Assistant"));
const Automations = page(() => import("./pages/Automations"));
const Company = page(() => import("./pages/Company"));
const Browser = page(() => import("./pages/Browser"));
const CalendarPage = page(() => import("./pages/Calendar"));
const Compare = page(() => import("./pages/Compare"));
const Docs = page(() => import("./pages/Docs"));
const MailPage = page(() => import("./pages/Mail"));
const Research = page(() => import("./pages/Research"));
const Settings = page(() => import("./pages/Settings"));
const Tasks = page(() => import("./pages/Tasks"));
const Files = page(() => import("./pages/Files"));
const Missions = page(() => import("./pages/Missions"));
const Projects = page(() => import("./pages/Projects"));
const Computer = page(() => import("./pages/Computer"));
const Capabilities = page(() => import("./pages/Capabilities"));
function preloadPages() {
  const run = () => preloads.forEach((p) => p().catch(() => undefined));
  const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
  if (w.requestIdleCallback) w.requestIdleCallback(run, { timeout: 3000 }); else setTimeout(run, 1500);
}
function PageLoading() {
  return <div className="page" aria-busy="true"><div className="skeleton" style={{ height: 28, width: 220, marginBottom: 16 }} /><div className="skeleton" style={{ height: 120 }} /></div>;
}

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
};

// Grouped around what ROOKS does for you; every route existed before, so deep links keep working
const NAV = [
  { to: "/", label: "Home", icon: Home },
  { to: "/needs-you", label: "Needs you", icon: NeedsYouIcon, count: true },
  { section: "ROOKS" },
  { to: "/assistant", label: "Assistant", icon: MessageSquare },
  { to: "/projects", label: "Projects", icon: FolderKanban },
  { to: "/missions", label: "Missions", icon: Flag },
  { to: "/research", label: "Research", icon: Search },
  { to: "/computer", label: "Computer", icon: MonitorPlay },
  { to: "/automations", label: "Automations", icon: CalendarClock },
  { to: "/capabilities", label: "What ROOKS can do", icon: Sparkles },
  { section: "Your workspace" },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/notes", label: "Notes", icon: NotebookPen },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/files", label: "Files", icon: FolderOpen },
  { to: "/mail", label: "Mail", icon: Mail },
  { section: "More" },
  { to: "/assistant-memory", label: "Memory & skills", icon: Brain },
  { to: "/company", label: "Agent team", icon: Building2 },
  { to: "/browser", label: "Browser agent", icon: Globe, addon: "browser" },
  { to: "/compare", label: "Compare models", icon: Columns3 },
] as const;

function NavCount() {
  const n = useAttentionCount();
  return n > 0 ? <span className="nav-count" aria-label={`${n} waiting`}>{n > 99 ? "99+" : n}</span> : null;
}

function readCollapsed(): boolean {
  try { return localStorage.getItem("wb-sidebar") === "collapsed"; } catch { return false; }
}

function ThemeToggle() {
  const [dark, setDark] = useState(() => {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  });
  const toggle = () => {
    const a = cached();
    if (a.theme === "system") {
      // Device-following mode: flip light and dark as before
      const next = dark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("wb-theme", next); } catch { /* storage unavailable */ }
    } else {
      // A chosen theme: switch to the ROOKS theme of the other mode (pick others under Settings, Appearance)
      const n = { ...a, theme: dark ? "rooks" : "rooks-dark" };
      apply(n); cache(n); api("/appearance", { method: "PUT", body: n }).catch(() => undefined);
    }
    setDark(!dark);
  };
  return (
    <button className="btn ghost icon" onClick={toggle} title={dark ? "Switch to light theme" : "Switch to dark theme"} aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}>
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const location = useLocation();
  const toggleCollapsed = () => {
    setCollapsed((c) => {
      try { localStorage.setItem("wb-sidebar", c ? "open" : "collapsed"); } catch { /* storage unavailable */ }
      return !c;
    });
  };

  const refresh = useCallback(() => {
    api<Status>("/status")
      .then((s) => {
        setStatus(s);
        setError("");
        document.title = s.workspace_name === "ROOKS" ? "ROOKS" : `${s.workspace_name} · ROOKS`;
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(() => {
    if (!status?.signed_in) return;
    api<unknown>("/appearance").then((r) => { if (r && Object.keys(r as object).length) { const n = normalise(r); apply(n); cache(n); } }).catch(() => undefined);
  }, [status?.signed_in]);
  useEffect(() => {
    const onUnauthorized = () => setStatus((s) => (s ? { ...s, signed_in: false } : s));
    window.addEventListener("wb:unauthorized", onUnauthorized);
    return () => window.removeEventListener("wb:unauthorized", onUnauthorized);
  }, []);
  useEffect(() => { if (status?.signed_in) preloadPages(); }, [status?.signed_in]);
  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {  // one overlay at a time: opening search closes the phone menu
    const close = () => setMenuOpen(false);
    window.addEventListener("rooks:overlay", close);
    return () => window.removeEventListener("rooks:overlay", close);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  if (error) return <div className="auth"><div className="alert err">Cannot reach ROOKS: {error}</div></div>;
  if (!status) return null;
  if (!status.setup_complete || !status.signed_in) return <Auth status={status} onDone={refresh} />;

  const logout = async () => {
    await api("/logout", { method: "POST" });
    refresh();
  };

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className={`sidebar ${menuOpen ? "open" : ""}`} aria-label="Sidebar">
        <div className="brand">
          <div className="brand-mark"><RookMark size={18} /></div>
          <div className="nav-label" style={{ minWidth: 0 }}>
            <span className="wordmark">ROOKS</span>
            {status.workspace_name !== "ROOKS" && <span className="brand-sub truncate">{status.workspace_name}</span>}
          </div>
          <button className="btn ghost sm icon mobile-only" style={{ marginLeft: "auto" }} onClick={() => setMenuOpen(false)} aria-label="Close menu" title="Close menu"><X size={16} /></button>
        </div>
        <button type="button" className="nav-search" onClick={openPalette} title="Search and jump (Ctrl+K)" aria-label="Search and jump (Ctrl+K)">
          <Search size={15} aria-hidden="true" /><span className="nav-label grow">Search</span><kbd className="kbd nav-label">Ctrl K</kbd>
        </button>
        <nav aria-label="Main" className="col" style={{ gap: 2 }}>
          {NAV.filter((item) => !("addon" in item) || (item.addon === "browser" && status.browser_enabled)).map((item, i) =>
            "section" in item ? (
              <div key={i} className="nav-section"><span className="nav-label">{item.section}</span></div>
            ) : (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link" title={collapsed ? item.label : undefined}>
                <item.icon size={17} aria-hidden="true" /> <span className="nav-label">{item.label}</span>
                {"count" in item && <NavCount />}
              </NavLink>
            ),
          )}
          {(status.chat_url || status.paperclip_url || status.hermes_url) && <div className="nav-section"><span className="nav-label">Add-ons</span></div>}
          {status.chat_url && (
            <a className="nav-link" href={status.chat_url} target="_blank" rel="noreferrer" title={collapsed ? "Open WebUI (opens in a new tab)" : undefined}>
              <Sparkles size={17} aria-hidden="true" /> <span className="nav-label">Open WebUI</span> <ExternalLink size={12} className="nav-label" style={{ marginLeft: "auto" }} aria-label="opens in a new tab" />
            </a>
          )}
          {status.paperclip_url && (
            <a className="nav-link" href={status.paperclip_url} target="_blank" rel="noreferrer" title={collapsed ? "Paperclip (opens in a new tab)" : undefined}>
              <Building2 size={17} aria-hidden="true" /> <span className="nav-label">Paperclip</span> <ExternalLink size={12} className="nav-label" style={{ marginLeft: "auto" }} aria-label="opens in a new tab" />
            </a>
          )}
          {status.hermes_url && (
            <a className="nav-link" href={status.hermes_url} target="_blank" rel="noreferrer" title={collapsed ? "Hermes (opens in a new tab)" : undefined}>
              <Bot size={17} aria-hidden="true" /> <span className="nav-label">Hermes</span> <ExternalLink size={12} className="nav-label" style={{ marginLeft: "auto" }} aria-label="opens in a new tab" />
            </a>
          )}
        </nav>
        <div className="spacer" />
        <div className="nav-section"><span className="nav-label">System</span></div>
        <NavLink to="/settings" className="nav-link" title={collapsed ? "Settings" : undefined}><SettingsIcon size={17} aria-hidden="true" /> <span className="nav-label">Settings</span></NavLink>
        <div className="sidebar-foot">
          <ThemeToggle />
          <Link to="/settings#appearance" className="btn ghost icon" aria-label="Appearance: themes and fonts" title="Themes and fonts"><Palette size={16} /></Link>
          <button className="btn ghost icon desktop-only" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>
            {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          </button>
          <div className="spacer nav-label" />
          <button className="btn ghost sm" onClick={logout} title="Sign out" aria-label="Sign out"><LogOut size={14} /> <span className="nav-label">Sign out</span></button>
        </div>
      </aside>
      {menuOpen && <div className="modal-back" style={{ zIndex: 35 }} onClick={() => setMenuOpen(false)} />}
      <main className="main" id="main" tabIndex={-1}>
        <ComputerBar />
        <CallInvites />
        <CommandPalette />
        <div className="mobile-bar">
          <button className="btn ghost icon" onClick={() => setMenuOpen(true)} aria-label="Open menu" title="Open menu" aria-expanded={menuOpen}><Menu size={18} /></button>
          <RookMark size={18} />
          <strong className={`truncate ${status.workspace_name === "ROOKS" ? "wordmark" : ""}`}>{status.workspace_name}</strong>
          <div className="spacer" />
          <button type="button" className="btn ghost icon" onClick={openPalette} aria-label="Search and jump" title="Search and jump"><Search size={18} /></button>
          <Link to="/assistant" className="btn ghost icon" aria-label="Ask ROOKS" title="Ask ROOKS"><MessageSquare size={18} /></Link>
        </div>
        <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/assistant" element={<Assistant tab="chat" />} />
          <Route path="/assistant/:id" element={<Assistant tab="chat" />} />
          <Route path="/assistant-memory" element={<Assistant tab="memory" />} />
          <Route path="/assistant-skills" element={<Assistant tab="skills" />} />
          <Route path="/assistant-settings" element={<Assistant tab="settings" />} />
          <Route path="/computer" element={<Computer />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<Projects />} />
          <Route path="/missions" element={<Missions />} />
          <Route path="/missions/:id" element={<Missions />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/needs-you" element={<NeedsYou />} />
          <Route path="/capabilities" element={<Capabilities />} />
          <Route path="/company" element={<Company />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/browser" element={<Browser />} />
          <Route path="/browser/:id" element={<Browser />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/notes" element={<Docs kind="note" />} />
          <Route path="/notes/:id" element={<Docs kind="note" />} />
          <Route path="/documents" element={<Docs kind="document" />} />
          <Route path="/documents/:id" element={<Docs kind="document" />} />
          <Route path="/files" element={<Files />} />
          <Route path="/files/:id" element={<Files />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/mail" element={<MailPage />} />
          <Route path="/settings" element={<Settings onChanged={refresh} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
