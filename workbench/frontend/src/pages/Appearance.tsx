import { RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { ACCENTS, DEFAULTS, FONTS, SIZES, resolveTheme, useAppearance } from "../appearance";
import { ThemeGrid } from "../ThemePicker";
import { Tracer } from "../Tracer";

/** One panel, six choices, all live. Nothing here needs explaining twice. */
export default function Appearance() {
  const [a, set] = useAppearance();
  const [osReduced, setOsReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    setOsReduced(mq.matches);
    const on = () => setOsReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const isDefault = JSON.stringify(a) === JSON.stringify(DEFAULTS);

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <p className="kicker">Settings</p>
          <h1>Appearance</h1>
          <p className="muted">Changes apply straight away and are saved on this device.</p>
        </div>
        <button className="btn ghost sm" disabled={isDefault} onClick={() => set(DEFAULTS)}><RotateCcw size={13} /> Reset</button>
      </div>
      <div className="ap-grid">
        <div>
          <section className="ap-section" aria-labelledby="ap-theme">
            <h3 id="ap-theme">Theme</h3>
            <ThemeGrid />
          </section>

          <section className="ap-section" aria-labelledby="ap-font">
            <h3 id="ap-font">Typeface</h3>
            <div className="ap-options" role="radiogroup" aria-labelledby="ap-font">
              {FONTS.map((f) => (
                <button key={f.id} type="button" role="radio" aria-checked={a.font === f.id} className="ap-option" onClick={() => set({ font: f.id })}>
                  <span className="ap-type" style={{ fontFamily: f.display }}>Ag</span>
                  <span className="name" style={{ fontFamily: f.body }}>{f.label}</span>
                  <span className="note">{f.note}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="ap-section" aria-labelledby="ap-size">
            <div className="row"><h3 id="ap-size" className="grow" style={{ margin: 0 }}>Text size</h3><span className="mono">{a.size}px</span></div>
            <div className="row" style={{ marginTop: 12, gap: 14 }}>
              <span className="small muted" aria-hidden="true">A</span>
              <input className="ap-range" type="range" min={SIZES.min} max={SIZES.max} step={1} value={a.size} aria-labelledby="ap-size"
                onChange={(e) => set({ size: Number(e.target.value) })} />
              <span className="muted" style={{ fontSize: 20 }} aria-hidden="true">A</span>
            </div>
          </section>

          <section className="ap-section" aria-labelledby="ap-density">
            <h3 id="ap-density">Spacing</h3>
            <Seg label="Spacing" value={a.density} onChange={(v) => set({ density: v })}
              options={[["comfortable", "Comfortable"], ["compact", "Compact"]]} />
          </section>

          <section className="ap-section" aria-labelledby="ap-motion">
            <h3 id="ap-motion">Motion</h3>
            <Seg label="Motion" value={a.motion} onChange={(v) => set({ motion: v })} options={[["full", "Full"], ["subtle", "Calm"], ["off", "Off"]]} />
            <p className="small muted" style={{ margin: "10px 0 0" }}>
              {osReduced ? "Your computer asks for reduced motion, so nothing moves whatever you pick here."
                : a.motion === "full" ? "Pages glide into each other, replies arrive word by word, cards settle on a spring and the window's edge lights up while I use your computer."
                : a.motion === "subtle" ? "Calm motion: the same moments as gentle fades. Nothing slides, bounces or spins."
                : "Nothing moves. Work in progress shows a still red outline."}
            </p>
            <Link to="/appearance/motion" className="btn sm" style={{ marginTop: 10 }}>See every animation</Link>
          </section>

          <section className="ap-section" aria-labelledby="ap-accent" style={{ borderBottom: "none" }}>
            <h3 id="ap-accent">Accent</h3>
            {["neon", "contrast"].includes(resolveTheme(a.theme)) && <p className="small muted" style={{ margin: "0 0 10px" }}>This theme keeps its own accent so everything stays easy to read.</p>}
            <div className="ap-swatches" role="radiogroup" aria-labelledby="ap-accent">
              {ACCENTS.map((c) => (
                <button key={c.id} type="button" role="radio" aria-checked={a.accent === c.id} aria-label={c.label} title={c.label}
                  className="ap-swatch" onClick={() => set({ accent: c.id })}><span style={{ background: c.color }} /></button>
              ))}
            </div>
          </section>
        </div>

        <aside className="ap-preview" aria-label="Preview">
          <p className="label">Preview</p>
          <div className="frame">
            <p className="kicker" style={{ margin: 0 }}>Tuesday edition</p>
            <h2>The week, edited.</h2>
            <p className="muted" style={{ margin: 0 }}>Three tasks are due and your launch checklist is half done. Want a plan for today?</p>
            <div className="row">
              <button className="btn primary sm" type="button">Plan my day</button>
              <button className="btn sm" type="button">Later</button>
              <span className="badge accent">2 new</span>
            </div>
            <div className="composer has-tracer">
              <div className="fake-input">Thinking about your week…</div>
              <Tracer active />
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="ap-seg" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  );
}
