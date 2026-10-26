import {
  CalendarDays, CheckSquare, Columns3, ExternalLink, FileText, Home, LogOut, Mail, Menu, Moon, NotebookPen,
  Search, Settings as SettingsIcon, Sun, Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import Auth from "./pages/Auth";
import CalendarPage from "./pages/Calendar";
import Compare from "./pages/Compare";
import Dashboard from "./pages/Dashboard";
import Docs from "./pages/Docs";
import MailPage from "./pages/Mail";
import Research from "./pages/Research";
import Settings from "./pages/Settings";
import Tasks from "./pages/Tasks";

export type Status = {
  setup_complete: boolean;
  signed_in: boolean;
  setup_code_required: boolean;
  workspace_name: string;
  chat_url: string;
};

const NAV = [
  { section: "Workspace" },
  { to: "/", label: "Home", icon: Home },
  { to: "/research", label: "Research", icon: Search },
  { to: "/compare", label: "Compare models", icon: Columns3 },
  { section: "Your stuff" },
  { to: "/notes", label: "Notes", icon: NotebookPen },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/tasks", label: "Tasks", icon: CheckSquare },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/mail", label: "Mail", icon: Mail },
] as const;

function ThemeToggle() {
  const [dark, setDark] = useState(() => {
    const t = document.documentElement.dataset.theme;
    return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  });
  const toggle = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("wb-theme", next);
    } catch {
      /* storage unavailable */
    }
    setDark(!dark);
  };
  return (
    <button className="btn ghost icon" onClick={toggle} title="Toggle theme" aria-label="Toggle theme">
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

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

  if (error) return <div className="auth"><div className="alert err">Cannot reach Workbench: {error}</div></div>;
  if (!status) return null;
  if (!status.setup_complete || !status.signed_in) return <Auth status={status} onDone={refresh} />;

  const logout = async () => {
    await api("/logout", { method: "POST" });
    refresh();
  };

  return (
    <div className="shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Sparkles size={16} /></div>
          <span className="truncate">{status.workspace_name}</span>
        </div>
        <a className="nav-link" href={status.chat_url} target="_blank" rel="noreferrer">
          <Sparkles size={16} /> Chat &amp; agents <ExternalLink size={12} style={{ marginLeft: "auto" }} />
        </a>
        {NAV.map((item, i) =>
          "section" in item ? (
            <div key={i} className="nav-section">{item.section}</div>
          ) : (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className="nav-link">
              <item.icon size={16} /> {item.label}
            </NavLink>
          ),
        )}
        <div className="spacer" />
        <NavLink to="/settings" className="nav-link"><SettingsIcon size={16} /> Settings</NavLink>
        <div className="row" style={{ padding: "6px 4px 0" }}>
          <ThemeToggle />
          <div className="spacer" />
          <button className="btn ghost sm" onClick={logout}><LogOut size={14} /> Sign out</button>
        </div>
      </aside>
      {menuOpen && <div className="modal-back" style={{ zIndex: 35 }} onClick={() => setMenuOpen(false)} />}
      <main className="main">
        <div className="mobile-bar">
          <button className="btn ghost icon" onClick={() => setMenuOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
          <strong className="truncate">{status.workspace_name}</strong>
        </div>
        <Routes>
          <Route path="/" element={<Dashboard chatUrl={status.chat_url} />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:id" element={<Research />} />
          <Route path="/compare" element={<Compare />} />
          <Route path="/notes" element={<Docs kind="note" />} />
          <Route path="/notes/:id" element={<Docs kind="note" />} />
          <Route path="/documents" element={<Docs kind="document" />} />
          <Route path="/documents/:id" element={<Docs kind="document" />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/calendar" element={<CalendarPage />} />
          <Route path="/mail" element={<MailPage />} />
          <Route path="/settings" element={<Settings onChanged={refresh} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
