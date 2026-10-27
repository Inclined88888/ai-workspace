import { ArrowRight, CheckSquare, FileText, Flag, FolderKanban, FolderOpen, MessageSquare, Plus, Search, Settings, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

/* Find anything and jump there: Ctrl+K or Cmd+K anywhere, or the search button. Actions only navigate, or create
   something you asked for in plain view (a task, a mission plan that still waits for your approval); nothing here
   runs tools or skips an approval. */

type Item = { id: string; group: string; label: string; hint?: string; icon: typeof Search; run: () => void | Promise<void> };

const PAGES: [string, string][] = [["/", "Home"], ["/needs-you", "Needs you"], ["/assistant", "Assistant"], ["/projects", "Projects"], ["/missions", "Missions"],
  ["/research", "Research"], ["/computer", "Computer"], ["/automations", "Automations"], ["/capabilities", "What ROOKS can do"], ["/tasks", "Tasks"],
  ["/calendar", "Calendar"], ["/notes", "Notes"], ["/documents", "Documents"], ["/files", "Files"], ["/mail", "Mail"], ["/assistant-memory", "Memory and skills"],
  ["/company", "Agent team"], ["/compare", "Compare models"]];
const SETTINGS: [string, string][] = [["s-models", "AI models"], ["s-voice", "Voice and calls"], ["s-computer", "Computer Use settings"], ["s-connections", "Connections"],
  ["s-privacy", "Memory and privacy"], ["s-security", "Security and permissions"], ["s-usage", "Usage and budgets"], ["s-appearance", "Appearance"]];

export function openPalette() { window.dispatchEvent(new Event("wb:palette")); }

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const [found, setFound] = useState<Item[]>([]);
  const [msg, setMsg] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const back = useRef<HTMLElement | null>(null);
  const navigate = useNavigate();

  // Where focus was before opening, recorded before the search box takes it, so closing can give it back
  const remember = () => { const a = document.activeElement as HTMLElement | null; if (a && a !== document.body && !a.closest(".palette")) back.current = a; };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => { if (o) { setQ(""); setFound([]); setIdx(0); setMsg(""); } else remember(); return !o; });
      }
    };
    const ev = () => { remember(); setOpen(true); };
    window.addEventListener("keydown", key);
    window.addEventListener("wb:palette", ev);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("wb:palette", ev); };
  }, []);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) { window.dispatchEvent(new Event("rooks:overlay")); input.current?.focus(); }  // other overlays (the phone menu) close
    else if (wasOpen.current) { const el = back.current; if (el && el.isConnected) el.focus(); }
    wasOpen.current = open;
  }, [open]);
  // Closing clears the search there and then, so the next opening always starts empty
  const close = () => { setOpen(false); setQ(""); setFound([]); setIdx(0); setMsg(""); };
  const go = (to: string) => { close(); navigate(to); };
  const text = q.trim();
  // Search your content as you type (debounced); every result opens the page it lives on
  useEffect(() => {
    if (!open || text.length < 2) { setFound([]); return; }
    const t = setTimeout(async () => {
      const enc = encodeURIComponent(text);
      const [convs, projects, docs, files, missions] = await Promise.all([
        api<{ id: number; title: string }[]>(`/conversations?q=${enc}`).catch(() => []),
        api<{ id: number; name: string }[]>(`/projects?q=${enc}`).catch(() => []),
        api<{ id: number; title: string; kind: string }[]>(`/docs?q=${enc}`).catch(() => []),
        api<{ id: number; name: string }[]>(`/files?q=${enc}`).catch(() => []),
        api<{ id: number; goal: string; status: string }[]>("/missions").catch(() => []),
      ]);
      const low = text.toLowerCase();
      setFound([
        ...projects.slice(0, 4).map((p) => ({ id: `p${p.id}`, group: "Projects", label: p.name, icon: FolderKanban, run: () => go(`/projects/${p.id}`) })),
        ...missions.filter((m) => m.goal.toLowerCase().includes(low)).slice(0, 4).map((m) => ({ id: `m${m.id}`, group: "Missions", label: m.goal, hint: m.status, icon: Flag, run: () => go(`/missions/${m.id}`) })),
        ...convs.slice(0, 5).map((c) => ({ id: `c${c.id}`, group: "Conversations", label: c.title, icon: MessageSquare, run: () => go(`/assistant/${c.id}`) })),
        ...docs.slice(0, 5).map((d) => ({ id: `d${d.id}`, group: "Notes and documents", label: d.title || "Untitled", hint: d.kind, icon: FileText, run: () => go(`/${d.kind === "document" ? "documents" : "notes"}/${d.id}`) })),
        ...files.slice(0, 4).map((f) => ({ id: `f${f.id}`, group: "Files", label: f.name, icon: FolderOpen, run: () => go(`/files/${f.id}`) })),
      ]);
    }, 180);
    return () => clearTimeout(t);
  }, [text, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const items = useMemo<Item[]>(() => {
    const low = text.toLowerCase();
    const match = (s: string) => !low || s.toLowerCase().includes(low);
    const actions: Item[] = text ? [
      { id: "ask", group: "Do", label: `Ask ROOKS: ${text}`, icon: Sparkles, run: () => { close(); navigate("/assistant", { state: { ask: text } }); } },
      { id: "research", group: "Do", label: `Research: ${text}`, hint: "opens Research with the question", icon: Search, run: () => { close(); navigate("/research", { state: { question: text } }); } },
      { id: "task", group: "Do", label: `Add task: ${text}`, icon: CheckSquare, run: async () => {
        await api("/tasks", { body: { title: text } }); setMsg(`Task added: ${text}`); setQ("");
      } },
      { id: "mission", group: "Do", label: `Plan a mission: ${text}`, hint: "nothing runs until you approve the plan", icon: Flag, run: async () => {
        const m = await api<{ id: number }>("/missions", { body: { goal: text } }); go(`/missions/${m.id}`);
      } },
    ] : [
      { id: "new-chat", group: "Do", label: "New conversation", icon: Plus, run: () => go("/assistant") },
      { id: "new-mission", group: "Do", label: "New mission", icon: Flag, run: () => go("/missions") },
      { id: "new-research", group: "Do", label: "Start research", icon: Search, run: () => go("/research") },
    ];
    const pages = PAGES.filter(([, l]) => match(l)).map(([to, l]) => ({ id: `go${to}`, group: "Go to", label: l, icon: ArrowRight, run: () => go(to) }));
    const settings = SETTINGS.filter(([, l]) => match(l) || match(`settings ${l}`)).map(([id, l]) => ({ id: `s-${id}`, group: "Settings", label: l, icon: Settings, run: () => go(`/settings#${id}`) }));
    // what you typed matches a page, a setting or your content: that comes first; otherwise ask, research or add it
    if (!text) return [...actions, ...pages, ...settings];
    return [...pages.slice(0, 6), ...settings.slice(0, 4), ...found, ...actions];
  }, [text, found]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setIdx(0), [text]);
  if (!open) return null;
  const run = async (it: Item) => { try { await it.run(); } catch (e) { setMsg((e as Error).message); } };
  let lastGroup = "";
  return (
    <div className="modal-back palette-back" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search and jump">
        <div className="palette-input row">
          <Search size={16} aria-hidden="true" className="faint" />
          <input ref={input} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search, jump to a page, or type something to ask, research or add"
            role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={items[idx] ? `pal-${items[idx].id}` : undefined}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.preventDefault(); close(); }
              else if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
              else if (e.key === "Enter" && items[idx]) { e.preventDefault(); run(items[idx]); }
            }} />
          <kbd className="kbd">Esc</kbd>
        </div>
        {msg && <div className="small palette-msg" role="status">{msg}</div>}
        <ul id="palette-list" className="palette-list" role="listbox" aria-label="Results">
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? it.group : "";
            lastGroup = it.group;
            return [
              head && <li key={`h-${head}`} className="palette-group" role="presentation">{head}</li>,
              <li key={it.id} id={`pal-${it.id}`} role="option" aria-selected={i === idx} className={i === idx ? "on" : ""}
                onMouseEnter={() => setIdx(i)} onMouseDown={(e) => { e.preventDefault(); run(it); }}>
                <it.icon size={15} aria-hidden="true" /><span className="grow truncate">{it.label}</span>{it.hint && <span className="faint small">{it.hint}</span>}
              </li>,
            ];
          })}
          {items.length === 0 && <li className="palette-group">Nothing found</li>}
        </ul>
      </div>
    </div>
  );
}
