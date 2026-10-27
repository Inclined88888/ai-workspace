import { Check, Palette, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Appearance, apply, cache, cached, contrast, DEFAULTS, findTheme, FONTS, normalise, PRESETS, Theme, tokens } from "../appearance";
import { useToast } from "../util";

function Segmented<T extends string | number>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="field">{label}</span>
      <div className="segmented" role="radiogroup" aria-label={label}>
        {options.map(([v, l]) => <button type="button" key={String(v)} role="radio" aria-checked={value === v} onClick={() => onChange(v)}>{l}</button>)}
      </div>
    </div>
  );
}

function Swatch({ t, selected, onPick, accent }: { t: Theme | null; selected: boolean; onPick: () => void; accent: string }) {
  // "System" previews as split light/dark
  const tk = t ? tokens(t, accent) : null;
  return (
    <button type="button" role="radio" aria-checked={selected} className="swatch" onClick={onPick} title={t ? t.name : "Follows your device's light or dark setting"}>
      {tk ? (
        <div className="preview" style={{ background: tk["--bg"] }} aria-hidden="true">
          <div className="side" style={{ background: tk["--panel"] }} />
          <div className="body">
            <div className="line" style={{ background: tk["--text"], width: "70%" }} />
            <div className="line" style={{ background: tk["--muted"], width: "45%" }} />
            <div className="line" style={{ background: tk["--accent"], width: "30%", height: 12 }} />
          </div>
        </div>
      ) : (
        <div className="preview" style={{ background: "linear-gradient(135deg, #f7f7f8 50%, #0f0f12 50%)" }} aria-hidden="true" />
      )}
      <div className="label"><span>{t ? t.name : "Match my device"}</span>{selected && <Check size={14} aria-label="Selected" />}</div>
    </button>
  );
}

/** Themes, fonts and layout comfort. Changes apply instantly and are saved to this workspace. */
export default function AppearancePanel() {
  const [a, setA] = useState<Appearance>(cached);
  const [editing, setEditing] = useState<Theme | null>(null);
  const saveTimer = useRef<number>();
  const toast = useToast();

  useEffect(() => { api<unknown>("/appearance").then((r) => { const n = normalise(r && Object.keys(r as object).length ? r : cached()); setA(n); apply(n); cache(n); }).catch(() => undefined); }, []);

  const update = (patch: Partial<Appearance>) => {
    const n = normalise({ ...a, ...patch });
    setA(n);
    apply(n);
    cache(n);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => { api("/appearance", { method: "PUT", body: n }).catch(toast.err); }, 400);
  };

  const startCustom = () => {
    const base = findTheme(a) ?? PRESETS[0];
    setEditing({ ...base, id: `custom-${Date.now().toString(36)}`, name: `My ${base.name}`, font: undefined });
  };
  const saveCustom = () => {
    if (!editing) return;
    const custom = [...a.custom.filter((t) => t.id !== editing.id), editing].slice(-12);
    update({ custom, theme: editing.id });
    setEditing(null);
    toast.ok("Theme saved");
  };
  const removeCustom = (t: Theme) => {
    if (!confirm(`Delete the theme "${t.name}"?`)) return;
    update({ custom: a.custom.filter((x) => x.id !== t.id), theme: a.theme === t.id ? "system" : a.theme });
  };

  // Live preview of the theme being edited, then restore when the editor closes
  useEffect(() => {
    if (editing) apply({ ...a, custom: [...a.custom.filter((t) => t.id !== editing.id), editing], theme: editing.id });
    else apply(a);
  }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps

  const tk = editing ? tokens(editing, a.accent) : null;
  const ratio = (x: string, y: string) => contrast(x, y).toFixed(1);

  return (
    <div className="card col gap-lg" id="appearance" style={{ scrollMarginTop: 16 }}>
      <div className="row"><Palette size={16} aria-hidden="true" /><h2 className="grow">Appearance</h2>
        <button type="button" className="btn ghost sm" onClick={() => update({ ...DEFAULTS, custom: a.custom })} title="Back to the defaults (keeps your saved themes)"><RotateCcw size={13} /> Reset</button></div>

      <div className="col" style={{ gap: 8 }}>
        <span className="field">Theme <span className="hint">Every theme is checked for readable contrast, including the ones you make.</span></span>
        <div className="swatch-grid" role="radiogroup" aria-label="Theme">
          <Swatch t={null} selected={a.theme === "system"} onPick={() => update({ theme: "system" })} accent={a.accent} />
          {PRESETS.map((t) => <Swatch key={t.id} t={t} selected={a.theme === t.id} accent={a.accent} onPick={() => update({ theme: t.id, ...(t.font && a.font === "system" ? { font: t.font } : {}) })} />)}
          {a.custom.map((t) => (
            <div key={t.id} style={{ position: "relative" }}>
              <Swatch t={t} selected={a.theme === t.id} accent={a.accent} onPick={() => update({ theme: t.id })} />
              <button type="button" className="btn ghost sm icon" style={{ position: "absolute", top: 4, right: 4, background: "var(--panel)" }} onClick={() => setEditing(t)} aria-label={`Edit ${t.name}`} title="Edit"><Palette size={12} /></button>
            </div>
          ))}
          <button type="button" className="swatch" onClick={startCustom} style={{ display: "grid", placeItems: "center", minHeight: 96 }}><span className="row"><Plus size={15} /> Make your own</span></button>
        </div>
      </div>

      {editing && tk && (
        <div className="card col" style={{ background: "var(--panel-2)" }}>
          <div className="row"><h3 className="grow">Custom theme</h3>{a.custom.some((t) => t.id === editing.id) && <button type="button" className="btn ghost sm danger" onClick={() => { removeCustom(editing); setEditing(null); }}><Trash2 size={13} /> Delete</button>}</div>
          <label className="field">Name<input className="input" maxLength={40} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
          <div className="grid two">
            {(["bg", "panel", "text", "accent"] as const).map((k) => (
              <label key={k} className="field">{{ bg: "Page background", panel: "Panels and cards", text: "Text", accent: "Accent (buttons, links)" }[k]}
                <div className="row"><input type="color" value={editing[k]} onChange={(e) => setEditing({ ...editing, [k]: e.target.value })} style={{ width: 44, height: 34, padding: 2, border: "1px solid var(--border-strong)", borderRadius: 7, background: "var(--panel)" }} aria-label={`${k} colour`} />
                  <input className="input mono" value={editing[k]} onChange={(e) => /^#[0-9a-fA-F]{0,6}$/.test(e.target.value) && setEditing({ ...editing, [k]: e.target.value })} aria-label={`${k} hex value`} /></div>
              </label>
            ))}
          </div>
          <div className="muted small" aria-live="polite">
            Contrast as shown (adjusted where needed): text {ratio(tk["--text"], tk["--panel"])}:1, secondary text {ratio(tk["--muted"], tk["--panel"])}:1, links {ratio(tk["--accent-text"], tk["--panel"])}:1, button labels {ratio(tk["--on-accent"], tk["--accent"])}:1. WCAG AA needs 4.5:1.
          </div>
          <div className="row"><button type="button" className="btn primary" onClick={saveCustom} disabled={!(["bg", "panel", "text", "accent"] as const).every((k) => /^#[0-9a-fA-F]{6}$/.test(editing[k]))}>Save theme</button><button type="button" className="btn ghost" onClick={() => setEditing(null)}>Cancel</button></div>
        </div>
      )}

      <div className="col" style={{ gap: 8 }}>
        <span className="field">Font <span className="hint">Bundled with ROOKS; nothing is fetched from font services.</span></span>
        <div className="swatch-grid" role="radiogroup" aria-label="Font">
          {FONTS.map((f) => (
            <button type="button" key={f.id} role="radio" aria-checked={a.font === f.id} className="choice compact" onClick={() => update({ font: f.id })} style={{ fontFamily: f.stack }}>
              <span className="grow"><strong>{f.name}</strong>{f.note && <><br /><span className="muted small">{f.note}</span></>}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="row wrap" style={{ gap: 20, alignItems: "flex-start" }}>
        <Segmented label="Text size" value={a.size} onChange={(v) => update({ size: v })} options={[[13, "Small"], [14, "Default"], [15, "Large"], [16, "Larger"], [18, "Largest"]]} />
        <Segmented label="Density" value={a.density} onChange={(v) => update({ density: v })} options={[["compact", "Compact"], ["comfortable", "Comfortable"], ["spacious", "Spacious"]]} />
        <Segmented label="Corners" value={a.corners} onChange={(v) => update({ corners: v })} options={[["square", "Square"], ["soft", "Soft"], ["round", "Round"]]} />
        <Segmented label="Background" value={a.pattern} onChange={(v) => update({ pattern: v })} options={[["none", "Plain"], ["dots", "Dots"], ["grid", "Grid"]]} />
      </div>

      <div className="row wrap" style={{ alignItems: "flex-end" }}>
        <label className="field">Accent colour <span className="hint">Optional. Overrides the theme's accent; adjusted if needed so labels stay readable.</span>
          <div className="row"><input type="color" value={a.accent || (findTheme(a)?.accent ?? "#3f4bd1")} onChange={(e) => update({ accent: e.target.value })} style={{ width: 44, height: 34, padding: 2, border: "1px solid var(--border-strong)", borderRadius: 7, background: "var(--panel)" }} aria-label="Accent colour" />
            {a.accent && <button type="button" className="btn ghost sm" onClick={() => update({ accent: "" })}>Use the theme's accent</button>}</div>
        </label>
      </div>
      {toast.node}
    </div>
  );
}
