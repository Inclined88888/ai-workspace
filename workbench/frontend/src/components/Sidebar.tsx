import {
  Bot, Brain, Building2, CalendarDays, ChevronRight, Columns3, ExternalLink, Flag, FolderKanban, FolderOpen, Globe, LayoutDashboard, LogOut, Mail,
  MonitorPlay, NotebookPen, PanelLeftClose, PanelLeftOpen, Search, Settings as SettingsIcon, Sparkles, SquarePen, Trash2, X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { NeedsYouIcon, useAttentionCount } from "../pages/NeedsYou";
import { openPalette } from "./CommandPalette";
import RookMark from "./RookMark";

type Conv = { id: number; title: string; updated_at: string; channel: string; project_id: number | null };
type Status = { workspace_name: string; browser_enabled: boolean; chat_url: string; paperclip_url: string; hermes_url: string };

/** Pages for detailed work, one fold below the everyday items. Every one is also in Search (Ctrl K). */
const WORKSPACE = [
  { to: "/notes", label: "Notes and documents", icon: NotebookPen, also: ["/documents"] },
  { to: "/files", label: "Files", icon: FolderOpen, also: [] },
  { to: "/research", label: "Research", icon: Search, also: [] },
  { to: "/tasks", label: "Tasks and calendar", icon: CalendarDays, also: ["/calendar", "/automations"] },
  { to: "/missions", label: "Missions", icon: Flag, also: ["/company"] },
  { to: "/computer", label: "Computer", icon: MonitorPlay, also: [] },
  { to: "/mail", label: "Mail", icon: Mail, also: [] },
  { to: "/assistant-memory", label: "Memory and skills", icon: Brain, also: ["/assistant-skills", "/assistant-settings"] },
  { to: "/overview", label: "Overview", icon: LayoutDashboard, also: [] },
  { to: "/capabilities", label: "What ROOKS can do", icon: Sparkles, also: [] },
  { to: "/compare", label: "Compare models", icon: Columns3, also: [] },
] as const;

/** Tell the sidebar the conversation list changed (a new chat, a new title, a deletion). */
export function conversationsChanged() {
  window.dispatchEvent(new Event("rooks:convs"));
}

function Count() {
  const n = useAttentionCount();
  return n > 0 ? <span className="nav-count" aria-label={`${n} waiting`}>{n > 99 ? "99+" : n}</span> : null;
}

function Recent({ collapsed }: { collapsed: boolean }) {
  const [convs, setConvs] = useState<Conv[] | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const load = useCallback(() => {
    api<Conv[]>("/conversations").then((c) => setConvs(c.slice(0, 40))).catch(() => setConvs([]));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    window.addEventListener("rooks:convs", load);
    return () => window.removeEventListener("rooks:convs", load);
  }, [load]);
  const current = Number(/^\/assistant\/(\d+)/.exec(location.pathname)?.[1] ?? 0);
  useEffect(() => { if (current && convs && !convs.some((c) => c.id === current)) load(); }, [current]); // eslint-disable-line react-hooks/exhaustive-deps
  if (collapsed) return null;
  const remove = async (c: Conv) => {
    if (!confirm(`Delete "${c.title}"? Its messages are removed; anything it created stays.`)) return;
    await api(`/conversations/${c.id}`, { method: "DELETE" }).catch(() => undefined);
    if (c.id === current) navigate("/");
    load();
  };
  return (
    <section className="recent" aria-label="Recent conversations">
      <div className="nav-section"><span>Recent</span></div>
      {convs === null ? <div className="recent-empty"><span className="skeleton" style={{ display: "block", height: 14 }} /></div>
        : convs.length === 0 ? <div className="recent-empty small faint">Your conversations will appear here.</div>
        : (
          <ul className="recent-list">
            {convs.map((c) => (
              <li key={c.id} className={`recent-item ${c.id === current ? "active" : ""}`}>
                <Link to={`/assistant/${c.id}`} className="recent-link" aria-current={c.id === current ? "page" : undefined} title={c.title}>
                  <span className="truncate">{c.title}</span>
                </Link>
                <button type="button" className="recent-del" aria-label={`Delete conversation ${c.title}`} title="Delete" onClick={() => remove(c)}>
                  <Trash2 size={13} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
    </section>
  );
}

export default function Sidebar({ status, collapsed, open, onClose, onToggle, onLogout }: {
  status: Status; collapsed: boolean; open: boolean; onClose: () => void; onToggle: () => void; onLogout: () => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const inWorkspace = WORKSPACE.some((w) => [w.to, ...w.also].some((p) => location.pathname === p || location.pathname.startsWith(`${p}/`)));
  const [wsOpen, setWsOpen] = useState(inWorkspace);
  useEffect(() => { if (inWorkspace) setWsOpen(true); }, [inWorkspace]);
  const addons = [
    status.chat_url && { href: status.chat_url, label: "Open WebUI", icon: Sparkles },
    status.paperclip_url && { href: status.paperclip_url, label: "Paperclip", icon: Building2 },
    status.hermes_url && { href: status.hermes_url, label: "Hermes", icon: Bot },
  ].filter(Boolean) as { href: string; label: string; icon: typeof Bot }[];
  const tip = (label: string) => (collapsed ? label : undefined);
  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Sidebar">
      <div className="brand">
        <Link to="/" className="brand-home" aria-label="ROOKS, new chat" title={tip("ROOKS")}>
          <span className="brand-mark"><RookMark size={17} /></span>
          <span className="nav-label brand-text">
            <span className="wordmark">ROOKS</span>
            {status.workspace_name !== "ROOKS" && <span className="brand-sub truncate">{status.workspace_name}</span>}
          </span>
        </Link>
        <button className="btn ghost icon desktop-only sidebar-toggle" onClick={onToggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
        <button className="btn ghost sm icon mobile-only" style={{ marginLeft: "auto" }} onClick={onClose} aria-label="Close menu" title="Close menu"><X size={16} /></button>
      </div>
      <nav aria-label="Main" className="nav-main">
        <button type="button" className="nav-link nav-new" onClick={() => { navigate("/"); onClose(); window.dispatchEvent(new Event("rooks:newchat")); }} title={tip("New chat")}>
          <SquarePen size={17} aria-hidden="true" /><span className="nav-label">New chat</span>
        </button>
        <button type="button" className="nav-link" onClick={openPalette} title={tip("Search (Ctrl K)")} aria-keyshortcuts="Control+K">
          <Search size={17} aria-hidden="true" /><span className="nav-label grow">Search</span><kbd className="kbd nav-label">Ctrl K</kbd>
        </button>
        <NavLink to="/projects" className="nav-link" title={tip("Projects")}><FolderKanban size={17} aria-hidden="true" /><span className="nav-label">Projects</span></NavLink>
        <NavLink to="/needs-you" className="nav-link" title={tip("Needs your review")}>
          <NeedsYouIcon size={17} aria-hidden="true" /><span className="nav-label grow">Needs your review</span><Count />
        </NavLink>
      </nav>
      <div className="sidebar-scroll">
        <Recent collapsed={collapsed} />
      </div>
      <div className="sidebar-bottom">
        <div className={`nav-fold ${wsOpen ? "open" : ""}`}>
          <button type="button" className="nav-link" aria-expanded={wsOpen} aria-controls="workspace-pages" onClick={() => setWsOpen(!wsOpen)} title={tip("Workspace")}>
            <ChevronRight size={17} aria-hidden="true" className="fold-chevron" /><span className="nav-label">Workspace</span>
          </button>
          <div id="workspace-pages" className="fold-body" hidden={!wsOpen}>
            {WORKSPACE.map((w) => (
              <NavLink key={w.to} to={w.to} title={tip(w.label)}
                className={({ isActive }) => `nav-link sub ${isActive || w.also.some((p) => location.pathname.startsWith(p)) ? "active" : ""}`}>
                <w.icon size={16} aria-hidden="true" /><span className="nav-label">{w.label}</span>
              </NavLink>
            ))}
            {status.browser_enabled && <NavLink to="/browser" className="nav-link sub" title={tip("Browser agent")}><Globe size={16} aria-hidden="true" /><span className="nav-label">Browser agent</span></NavLink>}
            {addons.map((a) => (
              <a key={a.href} className="nav-link sub" href={a.href} target="_blank" rel="noreferrer" title={tip(`${a.label} (opens in a new tab)`)}>
                <a.icon size={16} aria-hidden="true" /><span className="nav-label grow">{a.label}</span><ExternalLink size={12} className="nav-label" aria-label="opens in a new tab" />
              </a>
            ))}
          </div>
        </div>
        <NavLink to="/settings" className="nav-link" title={tip("Settings")}><SettingsIcon size={17} aria-hidden="true" /><span className="nav-label">Settings</span></NavLink>
        <button type="button" className="nav-link" onClick={onLogout} title={tip("Sign out")}><LogOut size={17} aria-hidden="true" /><span className="nav-label">Sign out</span></button>
      </div>
    </aside>
  );
}
