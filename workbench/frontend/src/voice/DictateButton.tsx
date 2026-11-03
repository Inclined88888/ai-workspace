/**
 * Mic button for any text box (feat/v02-anywhere-voice; Free: dictation).
 * Click to talk (stops when you pause), or hold it down and let go when you're done (push-to-talk).
 */
import { Loader2, Mic, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { listenOnce, Mic as MicT, openMic, osDictationHint, voiceStatus, VoiceStatus } from "./engine";

export function DictateButton({ onText, size = 15, className = "btn ghost sm icon", label = "Speak instead of typing", orb = false, disabled = false, onStart }:
  { onText: (text: string) => void; size?: number; className?: string; label?: string;
    /** integration/v0.2: draw it as the motion system's voice orb, which swells with the microphone level */
    orb?: boolean; disabled?: boolean; onStart?: () => void }) {
  const [state, setState] = useState<"idle" | "listening" | "working">("idle");
  const [level, setLevel] = useState(0);
  const [hint, setHint] = useState("");
  const st = useRef<VoiceStatus | null>(null);
  const abort = useRef<AbortController | null>(null);
  const downAt = useRef(0);
  const micRef = useRef<MicT | null>(null);

  useEffect(() => () => { abort.current?.abort(); micRef.current?.close(); }, []);

  const start = async () => {
    setHint("");
    onStart?.();
    try {
      st.current ??= await voiceStatus();
      const s = st.current;
      if (!s.features.voice_dictation?.allowed) throw new Error("Dictation isn't included in your plan.");
      abort.current = new AbortController();
      const mic = s.engine === "browser" ? null : await openMic();
      micRef.current = mic;
      setState("listening");
      const text = await listenOnce(mic, s, { signal: abort.current.signal, onLevel: setLevel, waitMs: 10000 })
        .finally(() => { mic?.close(); micRef.current = null; });
      setState("working");
      if (text) onText(text);
      else setHint("I didn't hear anything. Try again a little closer to the mic.");
    } catch (e) {
      const msg = (e as Error).message || "";
      setHint(/Permission|NotAllowed|denied/i.test(msg) ? "Microphone access is off. Allow it in your system settings, then try again."
        : (e as { status?: number }).status === 409 ? `Use your computer's dictation instead: ${osDictationHint()}.` : msg);
    } finally {
      setState("idle");
      setLevel(0);
    }
  };
  const stop = () => abort.current?.abort();

  const lvl = Math.min(level * 14, 1);
  const handlers = {
    onPointerDown: () => { if (state === "idle") { downAt.current = Date.now(); start(); } else if (state === "listening") stop(); },
    onPointerUp: () => { if (downAt.current && Date.now() - downAt.current > 600) stop(); downAt.current = 0; },
    onClick: (e: React.MouseEvent) => e.preventDefault(),
    onKeyDown: (e: React.KeyboardEvent) => { if ((e.key === "Enter" || e.key === " ") && !e.repeat) { e.preventDefault(); if (state === "idle") start(); else stop(); } },
  };
  const title = state === "listening" ? "Listening… click to stop" : `${label} (click, or hold to talk)`;

  return (
    <span className="dictate" data-state={state}>
      {orb ? (
        <button type="button" className={`voice-orb${state === "listening" ? " on" : ""}`} disabled={disabled && state === "idle"}
          aria-label={state === "listening" ? "Stop listening" : label} title={title} aria-pressed={state === "listening"}
          style={{ ["--lvl" as string]: lvl.toFixed(3) } as React.CSSProperties} {...handlers}>
          <span className="orb-halo" aria-hidden="true" />
          <span className="orb-core" aria-hidden="true" />
          {state === "working" ? <Loader2 size={14} className="spin orb-mic" /> :
            <svg className="orb-mic" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>}
        </button>
      ) : (
      <button type="button" className={className} aria-label={state === "listening" ? "Stop listening" : label} title={title}
        aria-pressed={state === "listening"} disabled={disabled && state === "idle"} {...handlers}>
        {state === "working" ? <Loader2 size={size} className="spin" /> : state === "listening" ? <Square size={size - 3} /> : <Mic size={size} />}
        {state === "listening" && <span className="dictate-level" style={{ transform: `scale(${1 + Math.min(level * 14, 0.9)})` }} aria-hidden="true" />}
      </button>
      )}
      {hint && <span className="dictate-hint small" role="status">{hint}</span>}
    </span>
  );
}
