/**
 * Settings → Ask from anywhere & voice (feat/v02-anywhere-voice): the shortcut, what the ask bar picks up, how I listen
 * and how I speak. Plain choices with the best one picked for you.
 */
import { Keyboard, Mic, RotateCcw, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { inDesktopShell, invoke } from "../desktop";
import { useToast } from "../util";
import { DictateButton } from "../voice/DictateButton";
import { isMac, speak, systemVoices, voiceStatus, VoiceStatus } from "../voice/engine";
import { accelFromEvent, accelLabel } from "../voice/logic";
import "../voice/voice.css";

type AnySettings = { grab_selection: boolean; screenshot: boolean; shortcut: string; desk: boolean; desktop: boolean; tier: string };
type ShortcutInfo = { shortcut: string; default: string; enabled: boolean; registered: boolean };

export function PlanBadge({ tier }: { tier?: string }) {
  return tier === "pro" ? <span className="badge plan-pro">Pro</span> : <span className="badge">Free</span>;
}

function ShortcutRecorder({ info, onSave }: { info: ShortcutInfo; onSave: (accel: string, enabled: boolean) => Promise<void> }) {
  const [recording, setRecording] = useState(false);
  const mac = isMac();
  return (
    <div className="row wrap" style={{ gap: 8 }}>
      <button type="button" className={`shortcut-key ${recording ? "recording" : ""}`} aria-label={recording ? "Press the new shortcut" : `Shortcut: ${accelLabel(info.shortcut, mac)}. Click to change`}
        onClick={() => setRecording(true)} onBlur={() => setRecording(false)}
        onKeyDown={async (e) => {
          if (!recording) return;
          e.preventDefault();
          if (e.key === "Escape") return setRecording(false);
          const accel = accelFromEvent(e.nativeEvent);
          if (accel) { setRecording(false); await onSave(accel, true); }
        }}>
        <Keyboard size={14} /> {recording ? "Press the keys…" : accelLabel(info.shortcut, mac)}
      </button>
      {info.shortcut !== info.default && <button className="btn ghost sm" onClick={() => onSave(info.default, true)}><RotateCcw size={13} /> Back to {accelLabel(info.default, mac)}</button>}
      <label className="row small" style={{ gap: 6 }}>
        <input type="checkbox" checked={info.enabled} onChange={(e) => onSave(info.shortcut, e.target.checked)} /> On
      </label>
      {info.enabled && !info.registered && <span className="small" style={{ color: "var(--danger-text)" }}>Another app is using this shortcut. Pick a different one.</span>}
    </div>
  );
}

export default function VoiceSettings() {
  const [any, setAny] = useState<AnySettings | null>(null);
  const [sc, setSc] = useState<ShortcutInfo | null>(null);
  const [st, setSt] = useState<VoiceStatus | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [heard, setHeard] = useState("");
  const toast = useToast();
  const desktop = inDesktopShell();

  useEffect(() => {
    api<AnySettings>("/anywhere/settings").then(setAny).catch(toast.err);
    voiceStatus().then(setSt).catch(toast.err);
    if (desktop) invoke<ShortcutInfo>("get_ask_shortcut").then(setSc).catch(() => undefined);
    const load = () => setVoices(systemVoices());
    load();
    if (typeof speechSynthesis !== "undefined") speechSynthesis.onvoiceschanged = load;
  }, []);

  const saveShortcut = async (shortcut: string, enabled: boolean) => {
    try {
      const r = await invoke<ShortcutInfo>("set_ask_shortcut", { shortcut, enabled });
      setSc(r);
      await api("/anywhere/settings", { method: "PUT", body: { shortcut } });
      toast.ok(enabled ? `Shortcut set to ${accelLabel(shortcut, isMac())}` : "Shortcut off");
    } catch (e) { toast.err(e); }
  };
  const putAny = async (body: Partial<AnySettings>) => {
    try { setAny(await api<AnySettings>("/anywhere/settings", { method: "PUT", body })); } catch (e) { toast.err(e); }
  };
  const putVoice = async (body: Record<string, string>) => {
    try { setSt(await api<VoiceStatus>("/voice/settings", { method: "PUT", body })); } catch (e) { toast.err(e); }
  };

  const s = st?.settings;
  const lang = navigator.language?.split("-")[0];
  const shownVoices = voices.filter((v) => !s?.language || v.lang.toLowerCase().startsWith(s.language.toLowerCase()) || v.lang.startsWith(lang ?? ""));

  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head"><div><h1>Ask from anywhere & voice</h1><p className="muted">Ask about whatever you're looking at, in any app, and talk instead of typing.</p></div></div>
      <div className="col gap-lg">
        <section className="card col gap-lg">
          <div className="row"><h2 className="grow">Ask from anywhere</h2><PlanBadge tier="free" /></div>
          <p className="muted small" style={{ margin: 0 }}>Select some text in any app (an email, a web page, a document) and press the shortcut. A small ask bar opens on top with your text in it. Ask about it, or have it rewritten, then paste the answer back.</p>
          {desktop ? (sc ? <label className="field">Shortcut<ShortcutRecorder info={sc} onSave={saveShortcut} /></label> : <div className="faint small">Loading the shortcut…</div>)
            : <div className="alert" role="status"><span>The shortcut works in the AI Workspace desktop app for Mac and Windows. In the browser you can still <Link to="/ask-bar">open the ask bar</Link>.</span></div>}
          {any && <>
            <label className="row small"><input type="checkbox" checked={any.grab_selection} onChange={(e) => putAny({ grab_selection: e.target.checked })} />
              Bring in the text I have selected <span className="faint">(it's copied for a moment, then your clipboard is put back)</span></label>
            <label className="row small"><input type="checkbox" checked={any.screenshot} disabled={!any.desk} onChange={(e) => putAny({ screenshot: e.target.checked })} />
              Include a picture of the window I'm in {!any.desk && <span className="faint">(needs Computer use, set up on the Computer page)</span>}</label>
            {any.screenshot && <div className="small faint">Pictures go to the model you've chosen for Chat. Use a model that can see images.</div>}
          </>}
        </section>

        <section className="card col gap-lg">
          <div className="row"><Mic size={16} /><h2 className="grow">Listening</h2><PlanBadge tier="free" /></div>
          {st && s && <>
            <div className="small">Right now I listen with <b>{st.engine === "local" ? "Whisper on this computer" : st.engine === "cloud" ? `${st.engines.cloud?.name} (cloud)` : "your browser"}</b>.</div>
            <label className="field">How should I turn speech into text?
              <select className="input" value={s.stt} onChange={(e) => putVoice({ stt: e.target.value })}>
                <option value="auto">Best available (recommended)</option>
                <option value="local" disabled={!st.engines.local}>Only on this computer (private, works offline){st.engines.local ? "" : " – not installed"}</option>
                <option value="cloud" disabled={!st.engines.cloud}>Cloud speech model{st.engines.cloud ? ` – ${st.engines.cloud.name}` : " – connect Groq or OpenAI in Models"}</option>
                <option value="browser">The browser's own recognition</option>
              </select>
            </label>
            {!st.engines.local && <div className="small faint">For private, offline listening, install Whisper on this computer: <span className="mono">pip install faster-whisper</span> (the desktop app's installer can add it).</div>}
            {st.engines.local && <label className="field">Whisper size <span className="hint">Bigger is more accurate but slower.</span>
              <select className="input" value={s.local_model} onChange={(e) => putVoice({ local_model: e.target.value })}>
                {["tiny", "base", "small", "medium", "turbo", "large-v3"].map((m) => <option key={m} value={m}>{m}{m === "base" ? " (recommended)" : ""}</option>)}
              </select></label>}
            <label className="field">Language <span className="hint">Leave on Detect unless it keeps guessing wrong.</span>
              <select className="input" value={s.language} onChange={(e) => putVoice({ language: e.target.value })}>
                <option value="">Detect automatically</option>
                {[["en", "English"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["nl", "Dutch"], ["ja", "Japanese"], ["zh", "Chinese"], ["hi", "Hindi"], ["ar", "Arabic"]].map(([c, n]) => <option key={c} value={c}>{n}</option>)}
              </select></label>
            <div className="row small" style={{ gap: 8 }}>Try it: <DictateButton onText={setHeard} className="btn sm icon" /> <span className="muted">{heard ? `“${heard}”` : "click the mic and say something"}</span></div>
          </>}
        </section>

        <section className="card col gap-lg">
          <div className="row"><Volume2 size={16} /><h2 className="grow">Speaking and hands-free</h2><PlanBadge tier="pro" /></div>
          {st && s && <>
            {!st.features.voice_conversation?.allowed && <div className="alert" role="status">Hands-free conversation, the wake word and answering approvals out loud are part of Pro. Dictation stays free.</div>}
            <label className="field">My voice
              <select className="input" value={s.tts} onChange={(e) => putVoice({ tts: e.target.value })}>
                <option value="system">Your computer's own voices (free, private)</option>
                <option value="cloud" disabled={!st.engines.cloud_voice}>A natural cloud voice{st.engines.cloud_voice ? ` – ${st.engines.cloud_voice.name}` : " – connect OpenAI in Models"}</option>
              </select></label>
            {s.tts === "system" && <label className="field">Voice
              <select className="input" value={s.name} onChange={(e) => putVoice({ name: e.target.value })}>
                <option value="">Default</option>
                {shownVoices.map((v) => <option key={v.name} value={v.name}>{v.name} ({v.lang})</option>)}
              </select></label>}
            {s.tts === "cloud" && <label className="field">Cloud voice
              <select className="input" value={s.cloud_voice} onChange={(e) => putVoice({ cloud_voice: e.target.value })}>
                {["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"].map((v) => <option key={v}>{v}</option>)}
              </select></label>}
            <label className="field">Speed
              <select className="input" value={s.rate} onChange={(e) => putVoice({ rate: e.target.value })}>
                {[["0.8", "Slower"], ["1", "Normal"], ["1.2", "Faster"], ["1.5", "Fast"]].map(([v, n]) => <option key={v} value={v}>{n}</option>)}
              </select></label>
            <div><button className="btn sm" onClick={() => speak("Hi! This is how I'll sound when I answer you.", s)}><Volume2 size={14} /> Hear it</button></div>
            <label className="field">Wake word <span className="hint">Say this to start talking hands-free. Only listens on this computer, never in the cloud.</span>
              <input className="input" defaultValue={s.wake_phrase} onBlur={(e) => e.target.value.trim() && e.target.value !== s.wake_phrase && putVoice({ wake_phrase: e.target.value })} /></label>
            {!st.wake_ok && <div className="small faint">The wake word needs Whisper on this computer or the browser's own recognition.</div>}
            <div className="small muted">When something needs your OK (like sending an email), I show the request on screen and read it out. Only a clear “yes, send it” approves it; anything unclear I ask again.</div>
            <div><Link className="btn primary sm" to="/talk">Open Talk</Link></div>
          </>}
        </section>
      </div>
      {toast.node}
    </div>
  );
}
