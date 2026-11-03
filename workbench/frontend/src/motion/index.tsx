import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { Markdown } from "../util";

/**
 * The motion system's runtime half. Tokens live in tokens.css, the moments in signature.css; this file holds the
 * few that need JavaScript: the word-by-word stream, view transitions, the edge glow trigger, the computer-use
 * cursor, live screen cross-fades, the completion tick and the voice orb. Everything moves transform/opacity only,
 * reads the motion level once per use, and does nothing at all when motion is Off or the OS asks for less.
 */
export type Level = "full" | "subtle" | "off";

export function motionLevel(): Level {
  if (typeof window === "undefined") return "off";
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return "off";
  const m = document.documentElement.dataset.motion;
  return m === "off" || m === "subtle" ? m : "full";
}

/** Re-render when Appearance changes the motion level (so loops stop the moment someone picks Off). */
export function useMotionLevel(): Level {
  const [l, setL] = useState<Level>(motionLevel);
  useEffect(() => {
    const on = () => setL(motionLevel());
    window.addEventListener("wb:appearance", on);
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    mq?.addEventListener?.("change", on);
    return () => { window.removeEventListener("wb:appearance", on); mq?.removeEventListener?.("change", on); };
  }, []);
  return l;
}

/* ------------------------------------------------------------------ view transitions */

type VTDoc = Document & { startViewTransition?: (cb: () => void) => { finished: Promise<void> } };

/** Run a DOM-changing update inside the View Transitions API when it exists and motion is Full; otherwise just run it. */
export function withViewTransition(update: () => void) {
  const d = document as VTDoc;
  if (!d.startViewTransition || motionLevel() !== "full") return update();
  document.documentElement.classList.add("vt-running");
  const t = d.startViewTransition(() => flushSync(update));
  t.finished.finally(() => document.documentElement.classList.remove("vt-running"));
}

/** onClick for router links: let the browser morph the page (shared elements carry view-transition-name). */
export function transitionClick(go: () => void) {
  return (e: React.MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const d = document as VTDoc;
    if (!d.startViewTransition || motionLevel() !== "full") return; // plain NavLink behaviour
    e.preventDefault();
    withViewTransition(go);
  };
}

/* ------------------------------------------------------------------ thinking */

/**
 * The assistant's name shimmers while it thinks (a light sweep through the letters, background-clip text, one
 * GPU-composited gradient). After 6 s it starts saying how long it has been, and it names the tool it is using
 * when there is one, so a long wait becomes a described one. The element stays mounted across re-renders so the
 * loop never restarts mid-cycle.
 */
export function Thinking({ phase, since }: { phase?: string; since: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const secs = Math.floor((now - since) / 1000);
  const label = phase ? `Using ${phase}` : "Thinking";
  return (
    <div className="thinking" role="status" aria-live="polite">
      <span className="shimmer-text">{label}</span>
      {secs >= 6 && <span className="thinking-time">{secs}s</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ streaming */

const WORD = /\S+\s*/g;

/** Word boundaries of a string (end index of each word with its trailing space). Exported for tests. */
export function wordEnds(text: string): number[] {
  const out: number[] = [];
  let m: RegExpExecArray | null;
  WORD.lastIndex = 0;
  while ((m = WORD.exec(text))) out.push(m.index + m[0].length);
  return out;
}

/** How many words to show `ms` after the reveal began: reading pace, faster for long replies, never past ~1.6 s. */
export function wordsAt(ms: number, total: number, maxMs = 1600): number {
  if (total <= 0) return 0;
  const per = Math.min(28, maxMs / total); // ms per word
  return Math.min(total, Math.floor(ms / per) + 1);
}

/**
 * The reply arrives word by word. Only the newest few words carry a span with a short opacity fade (inline text cannot be transformed without breaking layout); the rest
 * is plain, selectable text. Each tail word's animation is given a negative delay equal to its age, so re-rendering
 * the markdown every frame continues the fade instead of restarting it. Calm motion keeps the same gentle fade;
 * Off shows the whole reply at once.
 */
export function StreamText({ text, onDone }: { text: string; onDone?: () => void }) {
  const ends = useRef(wordEnds(text));
  const level = useRef(motionLevel());
  const start = useRef(performance.now());
  const [n, setN] = useState(() => (level.current === "off" ? ends.current.length : 1));
  const box = useRef<HTMLDivElement>(null);
  const done = useRef(false);
  const total = ends.current.length;
  const per = total ? Math.min(28, 1600 / total) : 0;

  // One frame loop for the whole reveal (a frame may not add a word, so it must not depend on n changing)
  useEffect(() => {
    if (n >= total) return;
    let raf = 0;
    const tick = () => {
      const next = wordsAt(performance.now() - start.current, total);
      setN(next);
      if (next < total) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [total]);
  useEffect(() => {
    if (n >= total && !done.current) { done.current = true; onDone?.(); }
  }, [n, total]);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el || n >= total || level.current === "off") return;
    const now = performance.now() - start.current;
    // Wrap the trailing words (those younger than the fade) in spans, walking text nodes from the end.
    const fadeMs = 220;
    let k = 0; // words wrapped so far
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    while (walker.nextNode()) nodes.push(walker.currentNode as Text);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const node = nodes[i];
      if (node.parentElement?.classList.contains("w-in")) break; // already wrapped this frame
      const parts = node.data.match(/\S+\s*|\s+/g) || [];
      const frag = document.createDocumentFragment();
      let stop = false;
      const tail: Node[] = [];
      for (let j = parts.length - 1; j >= 0; j--) {
        const p = parts[j];
        if (stop || !p.trim()) { tail.unshift(document.createTextNode(p)); continue; }
        const age = now - (n - 1 - k) * per;
        if (age > fadeMs || k > 10) { stop = true; tail.unshift(document.createTextNode(p)); continue; }
        const s = document.createElement("span");
        s.className = "w-in";
        s.style.animationDelay = `${-Math.max(0, age)}ms`;
        s.textContent = p;
        tail.unshift(s);
        k++;
      }
      tail.forEach((t) => frag.appendChild(t));
      node.replaceWith(frag);
      if (stop) break;
    }
  }, [n]);

  const shown = n >= total ? text : text.slice(0, ends.current[n - 1] ?? text.length);
  return <div ref={box}><Markdown text={shown} className={n < total ? "streaming" : ""} /></div>;
}

/* ------------------------------------------------------------------ edge glow */

/**
 * While Rooks Desk drives the computer the whole window gets a slow lit edge in the theme's colours (Apple
 * Intelligence's tell, done in the house style): a conic gradient spun by transform inside a static masked band,
 * so the compositor rotates one layer and nothing repaints. A small pill at the top says what is happening and
 * offers Stop. Calm motion holds the light still; High contrast is a solid keyline.
 */
export function EdgeGlow({ active, label = "Rooks Desk is using your computer", hotkey, onStop }: { active: boolean; label?: string; hotkey?: string; onStop?: () => void }) {
  const [shown, setShown] = useState(active);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (active) { setShown(true); setLeaving(false); return; }
    if (!shown) return;
    setLeaving(true);
    const t = setTimeout(() => { setShown(false); setLeaving(false); }, motionLevel() === "off" ? 0 : 640);
    return () => clearTimeout(t);
  }, [active]);
  if (!shown) return null;
  // Portalled to <body>: any transformed ancestor (a lifted card, a page turn) would otherwise trap position: fixed
  return createPortal(
    <>
      <div className={`edge-glow${leaving ? " leaving" : ""}`} aria-hidden="true">
        <div className="edge-glow-soft" />
        <div className="edge-glow-band"><div className="edge-glow-spin" /></div>
      </div>
      <div className={`edge-pill${leaving ? " leaving" : ""}`} role="status">
        <span className="edge-pill-dot" aria-hidden="true" />
        <span>{label}</span>
        {hotkey && <span className="kbd">{hotkey}</span>}
        {onStop && <button className="btn sm" onClick={onStop}>Stop</button>}
      </div>
    </>,
    document.body,
  );
}

/* ------------------------------------------------------------------ cursor overlay */

export type Pointer = { x: number; y: number; kind: "click" | "move" | "type" };

/**
 * Rooks Desk reports pointer actions in screenshot pixels, e.g. "click at (412, 230) on button 'Save'" or
 * "moved to (10, 20)". Returns the position as a fraction of the screenshot, or null. Exported for tests.
 */
export function parsePointer(result: string, action: string, w: number, h: number): Pointer | null {
  const m = /(?:\bat|\bto|->)\s*\((\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)\)/.exec(result || "");
  if (!m || !w || !h) return null;
  const x = Math.min(1, Math.max(0, Number(m[1]) / w));
  const y = Math.min(1, Math.max(0, Number(m[2]) / h));
  return { x, y, kind: /click/.test(action) ? "click" : action === "type" ? "type" : "move" };
}

/**
 * The agent's own cursor drawn over the live screen: it glides to each new point on a gentle spring (a transform
 * on a full-size track, so positions are percentages and nothing lays out), leaves a fading trail of its last
 * moves, and rings where it clicks. Never moves the real mouse.
 */
export function CursorOverlay({ points }: { points: Pointer[] }) {
  if (!points.length) return null;
  const cur = points[points.length - 1];
  const trail = points.slice(-5);
  return (
    <div className="cursor-layer" aria-hidden="true">
      <svg className="cursor-trail" viewBox="0 0 100 100" preserveAspectRatio="none">
        {trail.length > 1 && (
          <polyline key={trail.length + "-" + cur.x + cur.y} points={trail.map((p) => `${p.x * 100},${p.y * 100}`).join(" ")} pathLength={100} vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      <div className="cursor-track" style={{ transform: `translate(${cur.x * 100}%, ${cur.y * 100}%)` }}>
        {cur.kind === "click" && <span key={`${cur.x},${cur.y},${points.length}`} className="cursor-ripple" />}
        <svg className="cursor-arrow" width="20" height="22" viewBox="0 0 20 22"><path d="M2 1.5 L2 17.5 L6.4 13.6 L9.3 20 L12.2 18.7 L9.4 12.4 L15.5 12.4 Z" /></svg>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ live screen */

/** A screenshot that cross-fades to each new frame once it has loaded, instead of blinking. */
export function LiveShot({ src, alt, scanning = false, children, onSize }:
  { src: string; alt: string; scanning?: boolean; children?: React.ReactNode; onSize?: (w: number, h: number) => void }) {
  const [frames, setFrames] = useState<{ src: string; ready: boolean }[]>([{ src, ready: false }]);
  useEffect(() => {
    setFrames((f) => (f[f.length - 1]?.src === src ? f : [...f.slice(-1), { src, ready: false }]));
  }, [src]);
  return (
    <div className={`live-shot${scanning ? " scanning" : ""}`}>
      {frames.map((f, i) => (
        <img key={f.src} src={f.src} alt={i === frames.length - 1 ? alt : ""} aria-hidden={i === frames.length - 1 ? undefined : true}
          className={f.ready ? "ready" : ""}
          onLoad={(e) => {
            const im = e.currentTarget;
            onSize?.(im.naturalWidth, im.naturalHeight);
            setFrames((fs) => fs.map((x) => (x.src === f.src ? { ...x, ready: true } : x)));
            // drop the frame underneath once the new one has faded in
            setTimeout(() => setFrames((fs) => (fs.length > 1 && fs[fs.length - 1].src === f.src ? fs.slice(-1) : fs)), 400);
          }} />
      ))}
      {scanning && <span className="scan-line" aria-hidden="true" />}
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ completion */

/** The finishing moment: a ring draws itself, the tick strokes in and the badge settles on a spring. */
export function DoneTick({ size = 22, label = "Done" }: { size?: number; label?: string }) {
  return (
    <svg className="done-tick" width={size} height={size} viewBox="0 0 24 24" role="img" aria-label={label}>
      <circle className="done-ring" cx="12" cy="12" r="10.5" pathLength={100} />
      <path className="done-check" d="M7 12.4 L10.4 15.6 L17 8.8" pathLength={100} />
    </svg>
  );
}

/* ------------------------------------------------------------------ exits */

/** Keep an item on screen for its exit animation before it is removed. `leaving(id)` → class name. */
export function useExit<T extends string | number>(ms = 200) {
  const [out, setOut] = useState<Set<T>>(new Set());
  const run = (id: T, then: () => void) => {
    if (motionLevel() === "off") return then();
    setOut((s) => new Set(s).add(id));
    setTimeout(() => { then(); setOut((s) => { const n = new Set(s); n.delete(id); return n; }); }, ms);
  };
  return { run, leaving: (id: T) => out.has(id) };
}

/* ------------------------------------------------------------------ voice */

type Rec = { start: () => void; stop: () => void; abort: () => void; continuous: boolean; interimResults: boolean; lang: string;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null; onerror: ((e: { error: string }) => void) | null };

export function dictationSupported(): boolean {
  const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
  return typeof window !== "undefined" && !!(w.SpeechRecognition || w.webkitSpeechRecognition);
}

/**
 * Talk instead of type: the browser's own speech-to-text, with the microphone level read by an AnalyserNode and
 * written straight to the orb's CSS variable every frame (no React re-render per frame).
 */
export function useDictation(onText: (t: string, final: boolean) => void) {
  const [on, setOn] = useState(false);
  const [err, setErr] = useState("");
  const orb = useRef<HTMLButtonElement>(null);
  const stopAll = useRef<() => void>(() => undefined);
  const start = async () => {
    setErr("");
    const w = window as unknown as { SpeechRecognition?: new () => Rec; webkitSpeechRecognition?: new () => Rec };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) { setErr("Voice isn't available in this browser."); return; }
    const rec = new Ctor();
    rec.continuous = true; rec.interimResults = true; rec.lang = navigator.language || "en-AU";
    let finalText = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
      }
      onText((finalText + interim).trim(), !interim);
    };
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let raf = 0;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      ctx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      let smooth = 0;
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        const rms = Math.min(1, Math.sqrt(sum / buf.length) * 4);
        smooth = smooth * 0.7 + rms * 0.3;
        orb.current?.style.setProperty("--lvl", smooth.toFixed(3));
        raf = requestAnimationFrame(tick);
      };
      if (motionLevel() !== "off") tick();
    } catch {
      /* no level meter without mic access; recognition may still prompt on its own */
    }
    stopAll.current = () => {
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      ctx?.close().catch(() => undefined);
      orb.current?.style.setProperty("--lvl", "0");
      try { rec.stop(); } catch { /* already stopped */ }
      setOn(false);
    };
    rec.onend = () => stopAll.current();
    rec.onerror = (e) => { setErr(e.error === "not-allowed" ? "Allow the microphone to talk to me." : `Voice stopped: ${e.error}`); stopAll.current(); };
    rec.start();
    setOn(true);
  };
  useEffect(() => () => stopAll.current(), []);
  return { on, err, orb, toggle: () => (on ? stopAll.current() : start()) };
}

/** The voice orb: a soft disc that breathes while idle-listening and swells with your voice level (--lvl 0..1). */
export function VoiceOrb({ on, orbRef, onClick, disabled }: { on: boolean; orbRef: React.RefObject<HTMLButtonElement>; onClick: () => void; disabled?: boolean }) {
  return (
    <button ref={orbRef} type="button" className={`voice-orb${on ? " on" : ""}`} onClick={onClick} disabled={disabled}
      aria-pressed={on} aria-label={on ? "Stop listening" : "Talk instead of typing"} title={on ? "Stop listening" : "Talk instead of typing"}>
      <span className="orb-halo" aria-hidden="true" />
      <span className="orb-core" aria-hidden="true" />
      <svg className="orb-mic" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
    </button>
  );
}
