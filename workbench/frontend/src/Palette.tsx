import { CornerDownLeft, Github, LucideIcon, Moon, Pause, Play, Plus, Search, Sun, Type } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FONTS, setAppearance, THEMES } from "./appearance";

export type PaletteItem = { id: string; label: string; group: string; icon: LucideIcon; hint?: string; keywords?: string; run: () => void };

/** Small fuzzy match: every query character in order, earlier and contiguous hits score higher. */
function score(text: string, q: string): number {
  if (!q) return 1;
  const t = text.toLowerCase();
  const direct = t.indexOf(q);
  if (direct >= 0) return 100 - direct;
  let ti = 0, s = 0, last = -2;
  for (const ch of q) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return 0;
    s += at === last + 1 ? 3 : 1;
    last = at;
    ti = at + 1;
  }
  return s;
}

export function CommandPalette({ open, onClose, pages }: { open: boolean; onClose: () => void; pages: { to: string; label: string; icon: PaletteItem["icon"] }[] }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const restore = useRef<HTMLElement | null>(null);

  const items: PaletteItem[] = useMemo(() => [
    { id: "new-chat", label: "New chat", group: "Actions", icon: Plus, keywords: "assistant ask message", run: () => navigate("/assistant") },
    { id: "new-note", label: "New note", group: "Actions", icon: Plus, keywords: "write doc", run: () => navigate("/notes") },
    { id: "new-task", label: "Add a task", group: "Actions", icon: Plus, keywords: "todo", run: () => navigate("/tasks") },
    { id: "add-skill", label: "Add a skill from GitHub", group: "Actions", icon: Github, keywords: "install skill mcp plugin", run: () => navigate("/skills?tab=add") },
    ...THEMES.map((t) => ({ id: `theme-${t.id}`, label: `${t.label} theme`, group: "Appearance", icon: t.id === "night" ? Moon : Sun, keywords: "appearance colour color dark light", run: () => setAppearance({ theme: t.id }) })),
    ...FONTS.map((f) => ({ id: `font-${f.id}`, label: `${f.label} type`, group: "Appearance", icon: Type, keywords: "appearance font typeface", run: () => setAppearance({ font: f.id }) })),
    { id: "motion-full", label: "Motion: full", group: "Appearance", icon: Play, keywords: "animation", run: () => setAppearance({ motion: "full" }) },
    { id: "motion-subtle", label: "Motion: subtle", group: "Appearance", icon: Play, keywords: "animation reduce", run: () => setAppearance({ motion: "subtle" }) },
    { id: "motion-off", label: "Motion: off", group: "Appearance", icon: Pause, keywords: "animation reduce stop", run: () => setAppearance({ motion: "off" }) },
    ...pages.map((p) => ({ id: `go-${p.to}`, label: p.label, group: "Go to", icon: p.icon, hint: "Page", run: () => navigate(p.to) })),
  ], [pages, navigate]);

  const query = q.trim().toLowerCase();
  const results = useMemo(() => items
    .map((it) => ({ it, s: Math.max(score(it.label, query), score(it.keywords ?? "", query) * 0.6) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => (query ? b.s - a.s : 0))
    .map((x) => x.it), [items, query]);

  useEffect(() => {
    if (!open) return;
    restore.current = document.activeElement as HTMLElement;
    setQ("");
    setSel(0);
    requestAnimationFrame(() => inputRef.current?.focus());
    return () => restore.current?.focus?.();
  }, [open]);
  useEffect(() => setSel(0), [query]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  if (!open) return null;
  const run = (it?: PaletteItem) => {
    if (!it) return;
    onClose();
    it.run();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(results.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); run(results[sel]); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
    else if (e.key === "Tab") e.preventDefault(); // keep focus inside the dialog
  };

  let lastGroup = "";
  return (
    <div className="modal-back palette-back" onMouseDown={onClose}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command menu" onMouseDown={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <div className="palette-input">
          <Search size={18} />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search pages and actions…"
            role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={results[sel] ? `pal-${results[sel].id}` : undefined} aria-autocomplete="list" />
          <span className="kbd">Esc</span>
        </div>
        <div className="palette-list" id="palette-list" role="listbox" ref={listRef}>
          {results.length === 0 && <div className="palette-empty">No results for “{q}”</div>}
          {results.map((it, i) => {
            const head = it.group !== lastGroup && !query ? it.group : "";
            lastGroup = it.group;
            return (
              <div key={it.id}>
                {head && <div className="palette-group" role="presentation">{head}</div>}
                <button id={`pal-${it.id}`} data-index={i} type="button" role="option" aria-selected={i === sel} className="palette-item" tabIndex={-1}
                  onMouseMove={() => sel !== i && setSel(i)} onClick={() => run(it)}>
                  <it.icon size={16} /> <span className="truncate">{it.label}</span>
                  {it.hint && <span className="hint">{it.hint}</span>}
                </button>
              </div>
            );
          })}
        </div>
        <div className="palette-foot">
          <span><span className="kbd">↑</span><span className="kbd">↓</span> to move</span>
          <span><span className="kbd"><CornerDownLeft size={11} /></span> to open</span>
          <span style={{ marginLeft: "auto" }}>AI Workspace</span>
        </div>
      </div>
    </div>
  );
}
