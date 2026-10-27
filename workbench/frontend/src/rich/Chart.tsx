import { useEffect, useId, useMemo, useRef, useState } from "react";
import { fmt, Format } from "./schema";

/* Small SVG charts for verified data from tools (no chart library). Single series bars and lines use the theme
   accent and need no legend (the title names them); a donut uses the validated categorical slots and folds
   anything past three into "Other". Every chart has a hover readout and a "Show as table" view. */

type Point = { label: string; value: number };

function nice(max: number): number {
  if (max <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(max)));
  const n = max / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function Bars({ points, format }: { points: Point[]; format: Format }) {
  const max = nice(Math.max(...points.map((p) => p.value), 0));
  const [hover, setHover] = useState<number | null>(null);
  return (
    <div className="viz-bars" role="list">
      {points.map((p, i) => (
        <div key={i} className={`viz-bar-row ${hover === i ? "hover" : ""}`} role="listitem" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
          title={`${p.label}: ${fmt(p.value, format)}`}>
          <span className="viz-bar-label truncate">{p.label}</span>
          <span className="viz-bar-track"><span className="viz-bar" style={{ width: `${Math.max(0, p.value) / max * 100}%` }} /></span>
          <span className="viz-bar-value">{fmt(p.value, format)}</span>
        </div>
      ))}
    </div>
  );
}

function useWidth(ref: React.RefObject<HTMLElement>, fallback = 600): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function Line({ points, format, title }: { points: Point[]; format: Format; title: string }) {
  const box = useRef<HTMLDivElement>(null);
  const W = useWidth(box), H = W < 420 ? 170 : 200, L = 44, R = 16, T = 12, B = 26;  // drawn at its real size, so text stays 11px
  const max = nice(Math.max(...points.map((p) => p.value), 0));
  const x = (i: number) => L + (points.length === 1 ? 0 : (i / (points.length - 1)) * (W - L - R));
  const y = (v: number) => T + (1 - Math.max(0, v) / max) * (H - T - B);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = `${path} L${x(points.length - 1).toFixed(1)},${H - B} L${x(0).toFixed(1)},${H - B} Z`;
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const move = (e: React.PointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    points.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    setHover(best);
  };
  const ticks = [0, max / 2, max];
  const last = points.length - 1;
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(W / 90))));
  return (
    <div className="viz-line" ref={box}>
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}: ${points.length} points, from ${points[0].label} to ${points[last].label}`}
        onPointerMove={move} onPointerLeave={() => setHover(null)}>
        {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="viz-grid" />
          <text x={L - 6} y={y(t) + 4} textAnchor="end" className="viz-tick">{fmt(t, format)}</text></g>)}
        {points.map((p, i) => i % labelEvery === 0 || i === last ? <text key={i} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === last ? "end" : "middle"} className="viz-tick">{p.label.slice(5) || p.label}</text> : null)}
        <path d={area} className="viz-area" />
        <path d={path} className="viz-path" />
        <circle cx={x(last)} cy={y(points[last].value)} r={4.5} className="viz-dot" />
        {hover !== null && <>
          <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="viz-cross" />
          <circle cx={x(hover)} cy={y(points[hover].value)} r={4.5} className="viz-dot" />
        </>}
      </svg>
      <div className="viz-readout small" aria-live="polite">
        {hover !== null ? <><strong>{fmt(points[hover].value, format)}</strong> <span className="muted">{points[hover].label}</span></>
          : <><strong>{fmt(points[last].value, format)}</strong> <span className="muted">latest, {points[last].label}</span></>}
      </div>
    </div>
  );
}

const SLOTS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)", "var(--viz-other)"];

function Donut({ points, format, title }: { points: Point[]; format: Format; title: string }) {
  const sorted = [...points].sort((a, b) => b.value - a.value);
  const shown = sorted.length > 4 ? [...sorted.slice(0, 3), { label: "Other", value: sorted.slice(3).reduce((s, p) => s + p.value, 0) }] : sorted;
  const total = shown.reduce((s, p) => s + p.value, 0) || 1;
  let acc = 0;
  const R = 52, C = 2 * Math.PI * R;
  return (
    <div className="viz-donut">
      <svg viewBox="0 0 140 140" role="img" aria-label={`${title}: ${shown.map((p) => `${p.label} ${fmt(p.value, format)}`).join(", ")}`}>
        {shown.map((p, i) => {
          const len = (p.value / total) * C;
          const el = <circle key={i} cx={70} cy={70} r={R} fill="none" stroke={SLOTS[i]} strokeWidth={18}
            strokeDasharray={`${Math.max(0, len - 2)} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 70 70)" />;
          acc += len;
          return el;
        })}
        <text x={70} y={74} textAnchor="middle" className="viz-total">{fmt(total, format)}</text>
      </svg>
      <ul className="viz-legend">{shown.map((p, i) => <li key={i}><span className="viz-swatch" style={{ background: SLOTS[i] }} />{p.label}<span className="muted"> {fmt(p.value, format)}</span></li>)}</ul>
    </div>
  );
}

export default function Chart({ kind, title, points, format }: { kind: "bar" | "line" | "donut"; title: string; points: Point[]; format: Format }) {
  const [table, setTable] = useState(false);
  const id = useId();
  const total = useMemo(() => points.reduce((s, p) => s + p.value, 0), [points]);
  return (
    <figure className="viz" aria-labelledby={id}>
      <figcaption className="row">
        <span id={id} className="viz-title grow">{title}</span>
        <button type="button" className="btn ghost sm" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show as table"}</button>
      </figcaption>
      {table ? (
        <div className="table-wrap"><table className="table small"><thead><tr><th scope="col">Label</th><th scope="col" className="num">Value</th></tr></thead>
          <tbody>{points.map((p, i) => <tr key={i}><td>{p.label}</td><td className="num">{fmt(p.value, format)}</td></tr>)}
            {kind !== "line" && <tr><th scope="row">Total</th><td className="num"><strong>{fmt(total, format)}</strong></td></tr>}</tbody></table></div>
      ) : kind === "bar" ? <Bars points={points} format={format} /> : kind === "line" ? <Line points={points} format={format} title={title} /> : <Donut points={points} format={format} title={title} />}
    </figure>
  );
}
