import {
  Activity, Bot, Brain, Building2, CalendarClock, CalendarDays, MessageSquare, CheckSquare, Globe, Columns3, ExternalLink, FileText, Home, LogOut, Mail, Menu, NotebookPen,
  ListTree, Search, Settings as SettingsIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { CommandPalette } from "./Palette";
import { ThemeSwitch } from "./theme";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import Assistant from "./pages/Assistant";
import Auth from "./pages/Auth";
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
import Tasks from "./pages/Tasks";
import Welcome from "./pages/Welcome";

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
};

const NAV = [
  { section: "Workspace" },
  { to: "/", label: "Home", icon: Home },
  { to: "/assistant", label: "Assistant", icon: MessageSquare },
  { to: "/assistant-memory", label: "Memory & skills", icon: Brain },
  { to: "/automations", label: "Automations", icon: CalendarClock },
  { to: "/company", label: "Company", icon: Building2 },
  { to: "/research", label: "Research", icon: Search },
  { to: "/browser", label: "Browser agent", icon: Globe, addon: "browser" },
  { to: "/compare", label: "Compare models", icon: Columns3 },
  { to: "/runs", label: "Run log", icon: ListTree },
  { to: "/status", label: "System status", icon: Activity },
  { section: "Your stuff" },
  { to: "/notes", label: "Notes", icon: NotebookPen },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/mail", label: "Mail", icon: Mail },
] as const;

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
  const navItems = useMemo(
    () => NAV.filter((item) => !("addon" in item) || (item.addon === "browser" && status?.browser_enabled)),
    [status?.browser_enabled],
  );
  const palettePages = useMemo(
    () => [...navItems.flatMap((i) => ("to" in i ? [{ to: i.to, label: i.label, icon: i.icon }] : [])), { to: "/settings", label: "Settings", icon: SettingsIcon }],
    [navItems],
  );
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
  if (status.desktop && !status.welcome_done) {
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
        {status.chat_url && (
          <a className="nav-link" href={status.chat_url} target="_blank" rel="noreferrer">
            <MessageSquare size={16} /> Open WebUI <ExternalLink size={12} style={{ marginLeft: "auto" }} />
          </a>
        )}
        {status.paperclip_url && (
          <a className="nav-link" href={status.paperclip_url} target="_blank" rel="noreferrer">
            <Building2 size={16} /> Company (Paperclip) <ExternalLink size={12} style={{ marginLeft: "auto" }} />
          </a>
        )}
        {status.hermes_url && (
          <a className="nav-link" href={status.hermes_url} target="_blank" rel="noreferrer">
            <Bot size={16} /> Hermes agent <ExternalLink size={12} style={{ marginLeft: "auto" }} />
          </a>
        )}
        {navItems.map((item, i) =>
          "section" in item ? (
            <div key={i} className="nav-section">{item.section}</div>
          ) : (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
              <item.icon size={16} /> <span className="truncate">{item.label}</span>
            </NavLink>
          ),
        )}
        <div className="spacer" />
        <NavLink to="/settings" className="nav-link"><SettingsIcon size={16} /> Settings</NavLink>
        <div className="sidebar-foot">
          <ThemeSwitch />
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
          <Route path="/assistant-skills" element={<Assistant tab="skills" />} />
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
