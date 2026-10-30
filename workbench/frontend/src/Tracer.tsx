import { useLayoutEffect, useRef, useState } from "react";

/**
 * The signature "working" border: a thin cherry stroke with an ink tip that travels around the element while the
 * assistant or a job is busy. SVG stroke-dashoffset on a rect with pathLength=100, so it moves at an even speed on
 * any shape and only repaints a hairline. Motion "Subtle" slows it down; "Off" (or the OS reduced-motion setting)
 * shows a still cherry keyline instead. The parent needs position: relative (add the .has-tracer class).
 */
export function Tracer({ active, label }: { active: boolean; label?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [active]);
  if (!active) return null;
  const r = box && box.w > 2 && box.h > 2 ? { x: 0.75, y: 0.75, width: box.w - 1.5, height: box.h - 1.5 } : null;
  return (
    <span ref={ref} className="tracer" aria-hidden={label ? undefined : true} role={label ? "status" : undefined} aria-label={label}>
      {r && (
        <svg width={box!.w} height={box!.h} focusable="false">
          <rect className="tracer-base" {...r} pathLength={100} />
          <rect className="tracer-dash" {...r} pathLength={100} />
          <rect className="tracer-tip" {...r} pathLength={100} />
        </svg>
      )}
    </span>
  );
}
