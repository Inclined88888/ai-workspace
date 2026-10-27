import { Eye, EyeOff, X } from "lucide-react";
import { ReactNode, useEffect, useId, useRef, useState } from "react";

/** Accessible dialog: announced as a dialog, closes on Escape or backdrop click, keeps Tab inside, returns focus. */
export function Modal({ title, onClose, children, width = 520 }: { title: string; onClose: () => void; children: ReactNode; width?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      if (e.key !== "Tab" || !ref.current) return;
      const items = [...ref.current.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), input:not(:disabled), select, textarea, [tabindex]:not([tabindex='-1'])")];
      if (!items.length) return;
      const [a, z] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); before?.focus?.(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} className="modal col gap-lg" role="dialog" aria-modal="true" aria-labelledby={titleId} style={{ width: `min(${width}px, 100%)` }}>
        <div className="row">
          <h2 id={titleId} className="grow">{title}</h2>
          <button type="button" data-close className="btn ghost sm icon" onClick={onClose} aria-label="Close" title="Close"><X size={15} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Password field with a show/hide toggle. */
export function PasswordInput({ value, onChange, autoFocus, autoComplete, id, invalid, describedBy }: {
  value: string; onChange: (v: string) => void; autoFocus?: boolean; autoComplete?: string; id?: string; invalid?: boolean; describedBy?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="input-wrap">
      <input id={id} className="input" type={show ? "text" : "password"} value={value} autoFocus={autoFocus} autoComplete={autoComplete}
        aria-invalid={invalid || undefined} aria-describedby={describedBy} onChange={(e) => onChange(e.target.value)} />
      <button type="button" className="btn ghost sm icon input-addon" onClick={() => setShow(!show)}
        aria-label={show ? "Hide password" : "Show password"} title={show ? "Hide password" : "Show password"} aria-pressed={show}>
        {show ? <EyeOff size={15} /> : <Eye size={15} />}
      </button>
    </div>
  );
}

/** A labelled step indicator for multi step flows. */
export function Steps({ labels, current }: { labels: string[]; current: number }) {
  return (
    <ol className="stepper" aria-label={`Step ${current + 1} of ${labels.length}: ${labels[current]}`}>
      {labels.map((l, i) => (
        <li key={l} className={i < current ? "done" : i === current ? "current" : ""} aria-current={i === current ? "step" : undefined}>
          <span className="bar" /><span className="label">{l}</span>
        </li>
      ))}
    </ol>
  );
}
