import { Check } from "lucide-react";
import { FONTS, THEMES, type ThemeInfo, pickTheme, useAppearance } from "./appearance";

/** A tiny drawing of the app in a theme's own colours, so every card previews itself whatever theme is on now. */
function Mini({ t }: { t: ThemeInfo }) {
  const s = t.swatch;
  if (t.mode === "auto") {
    const light = THEMES.find((x) => x.id === "sunshine")!.swatch;
    const dark = THEMES.find((x) => x.id === "night")!.swatch;
    return (
      <span className="look-mini split" aria-hidden="true">
        {[light, dark].map((p, i) => (
          <span key={i} className="main" style={{ background: p.paper, color: p.text }}>
            <span className="h">Aa</span>
            <span className="l" style={{ background: p.muted, width: "80%" }} />
            <span className="b" style={{ background: p.accent, color: p.onAccent }}>Go</span>
          </span>
        ))}
      </span>
    );
  }
  return (
    <span className="look-mini" aria-hidden="true" style={{ background: s.paper, borderColor: s.border }}>
      <span className="side" style={{ borderRight: `1px solid ${s.border}` }}>
        <i style={{ background: s.text, width: "80%" }} />
        <i style={{ background: s.muted, width: "60%" }} />
        <i style={{ background: s.accent, width: "70%", opacity: 1 }} />
        <i style={{ background: s.muted, width: "50%" }} />
      </span>
      <span className="main" style={{ background: s.panel, color: s.text }}>
        <span className="h">Good morning</span>
        <span className="l" style={{ background: s.muted, width: "85%" }} />
        <span className="l" style={{ background: s.muted, width: "60%" }} />
        <span className="b" style={{ background: s.accent, color: s.onAccent }}>Plan my day</span>
      </span>
    </span>
  );
}

/** Theme cards: picking one restyles the whole app at once (that is the live preview). */
export function ThemeGrid() {
  const [a] = useAppearance();
  return (
    <div className="look-grid" role="radiogroup" aria-label="Theme">
      {THEMES.map((t) => (
        <button key={t.id} type="button" role="radio" aria-checked={a.theme === t.id} className="look-card" data-theme-option={t.id}
          onClick={() => pickTheme(t.id)}>
          <Mini t={t} />
          <span className="name">{t.label}{a.theme === t.id && <Check size={13} aria-label="chosen" />}</span>
          <span className="note">{t.note}</span>
        </button>
      ))}
    </div>
  );
}

const SETUP_FONTS = ["modern", "editorial", "classic"] as const;
const SETUP_SIZES: [number, string][] = [[13, "Smaller"], [14, "Standard"], [16, "Larger"]];

/** Typeface and text size, the two other things people want to set before they start. */
export function TypeChoices() {
  const [a, set] = useAppearance();
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="look-row">
        <span className="small muted" style={{ minWidth: 70 }}>Typeface</span>
        <div className="ap-seg" role="radiogroup" aria-label="Typeface">
          {SETUP_FONTS.map((id) => {
            const f = FONTS.find((x) => x.id === id)!;
            return <button key={id} type="button" role="radio" aria-checked={a.font === id} style={{ fontFamily: f.display }} onClick={() => set({ font: id })}>{f.label}</button>;
          })}
        </div>
      </div>
      <div className="look-row">
        <span className="small muted" style={{ minWidth: 70 }}>Text size</span>
        <div className="ap-seg" role="radiogroup" aria-label="Text size">
          {SETUP_SIZES.map(([px, label]) => (
            <button key={px} type="button" role="radio" aria-checked={a.size === px} style={{ fontSize: px }} onClick={() => set({ size: px })}>{label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
