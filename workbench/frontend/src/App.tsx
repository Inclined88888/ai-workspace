import { Menu, Moon, Search, SquarePen, Sun } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import Auth from "./pages/Auth";
import Assistant from "./pages/Assistant";
import RookMark from "./components/RookMark";
import { apply, cache, cached, normalise } from "./appearance";
import ComputerBar from "./components/ComputerBar";
import CallInvites from "./components/CallInvites";
import NeedsYou from "./pages/NeedsYou";
import Sidebar from "./components/Sidebar";
import CommandPalette, { openPalette } from "./components/CommandPalette";

// Pages load on first visit, so the first screen downloads only what it needs. Once the app is idle every page is
// fetched in the background, so moving between pages stays instant.
const preloads: (() => Promise<unknown>)[] = [];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function page<T extends React.ComponentType<any>>(load: () => Promise<{ default: T }>) {
  preloads.push(load);
  return lazy(load);
}
const Dashboard = page(() => import("./pages/Dashboard"));
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

// Pages that are views of one thing share a row of tabs, so each is one click from the others. The sidebar's Workspace
// fold names the group (Notes and documents, Tasks and calendar, Missions); every page is also in Search (Ctrl K).
const GROUPS = [
  { label: "Notes and documents", pages: [["/notes", "Notes"], ["/documents", "Documents"]] },
  { label: "Tasks and calendar", pages: [["/tasks", "Tasks"], ["/calendar", "Calendar"], ["/automations", "Automations"]] },
  { label: "Missions", pages: [["/missions", "Missions"], ["/company", "Agent team"]] },
] as const;

function PageTabs() {
  const { pathname } = useLocation();
  const group = GROUPS.find((g) => g.pages.some(([to]) => pathname === to || pathname.startsWith(`${to}/`)));
  if (!group) return null;
  return (
    <nav className="area-tabs" aria-label={group.label}>
      {group.pages.map(([to, label]) => (
        <NavLink key={to} to={to} className={({ isActive }) => `area-tab ${isActive ? "active" : ""}`}>{label}</NavLink>
      ))}
    </nav>
  );
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
  const navigate = useNavigate();
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
      <Sidebar status={status} collapsed={collapsed} open={menuOpen} onClose={() => setMenuOpen(false)} onToggle={toggleCollapsed} onLogout={logout} />
      {menuOpen && <div className="modal-back drawer-back" onClick={() => setMenuOpen(false)} />}
      <main className="main" id="main" tabIndex={-1}>
        <ComputerBar />
        <CallInvites />
        <CommandPalette />
        <div className="mobile-bar">
          <button className="btn ghost icon" onClick={() => setMenuOpen(true)} aria-label="Open menu" title="Open menu" aria-expanded={menuOpen}><Menu size={18} /></button>
          <RookMark size={18} />
          <strong className={`truncate ${status.workspace_name === "ROOKS" ? "wordmark" : ""}`}>{status.workspace_name}</strong>
          <div className="spacer" />
          <ThemeToggle />
          <button type="button" className="btn ghost icon" onClick={openPalette} aria-label="Search" title="Search"><Search size={18} /></button>
          <button type="button" className="btn ghost icon" onClick={() => { navigate("/"); window.dispatchEvent(new Event("rooks:newchat")); }} aria-label="New chat" title="New chat"><SquarePen size={18} /></button>
        </div>
        <PageTabs />
        <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="/" element={<Assistant tab="chat" />} />
          <Route path="/overview" element={<Dashboard />} />
          <Route path="/library" element={<Navigate to="/notes" replace />} />
          <Route path="/planner" element={<Navigate to="/tasks" replace />} />
          <Route path="/work" element={<Navigate to="/missions" replace />} />
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
