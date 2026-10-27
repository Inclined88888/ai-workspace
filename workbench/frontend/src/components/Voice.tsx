import { Loader2, Mic, MicOff, PhoneOff, Square, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, speakAudio, stream, transcribe } from "../api";

/* A voice session on top of the ordinary assistant: ROOKS listens, answers aloud, and listens again. Half duplex with
   barge-in (speaking while ROOKS talks interrupts it); this is not full duplex streaming voice. The microphone is on
   only while listening, or while ROOKS speaks if interrupting by voice is on, and the header says which. */

type VoiceSettings = {
  stt: "browser" | "provider"; stt_model: string; tts: "browser" | "provider"; tts_model: string; browser_notice_seen: boolean;
  consent: Record<string, { ref: string }>; stt_destination: { local?: boolean; label?: string }; tts_destination: { local?: boolean; label?: string };
};
type Phase = "loading" | "notice" | "ready" | "listening" | "transcribing" | "thinking" | "speaking" | "ending" | "blocked";
type Line = { who: "you" | "rooks" | "note"; text: string };

type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean; start(): void; abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null;
};
type RecognitionCtor = new () => Recognition;

const BRIEFING = "Give me a short spoken briefing of what needs my attention now, then ask what I'd like to do.";
const END_WORDS = /^(stop|end|end (the )?(call|session)|goodbye|bye|that's all|that is all)[.!]?$/i;
const STATUS: Record<Phase, string> = {
  loading: "Getting ready", notice: "Before you start", ready: "Paused", listening: "Listening", transcribing: "Recognising",
  thinking: "Thinking", speaking: "Speaking", ending: "Session ended", blocked: "Voice is not available",
};

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Text as it should sound: no markdown symbols, links read as their words. */
export function spoken(text: string): string {
  return text.replace(/```[\s\S]*?```/g, " ").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[*_#`>|]/g, "").replace(/\s+/g, " ").trim();
}

export default function VoiceSession({ conversationId, projectId, opening, onClose }: {
  conversationId: number | null; projectId: number | null; opening?: string; onClose: (conversation: number | null) => void;
}) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [lines, setLines] = useState<Line[]>([]);
  const [interim, setInterim] = useState("");
  const [mic, setMic] = useState(false);
  const [problem, setProblem] = useState("");
  const [briefing, setBriefing] = useState(!opening);
  const [bargeIn, setBargeIn] = useState(true);
  const [ending, setEnding] = useState(false);
  const settings = useRef<VoiceSettings | null>(null);
  const active = useRef(false);
  const cid = useRef<number | null>(conversationId);
  const mark = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const recog = useRef<Recognition | null>(null);
  const media = useRef<{ stream: MediaStream; ctx: AudioContext; analyser: AnalyserNode } | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const timer = useRef<number | null>(null);
  const quiet = useRef(0); // listening rounds in a row that heard nothing
  const speech = useRef(0); // which reply is being spoken; interrupting moves it on, so late audio is never played
  const bargeRef = useRef(bargeIn);
  bargeRef.current = bargeIn;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const logEnd = useRef<HTMLDivElement>(null);

  const say = (who: Line["who"], text: string) => setLines((l) => [...l, { who, text }]);
  useEffect(() => { logEnd.current?.scrollIntoView({ block: "nearest" }); }, [lines, interim]);

  useEffect(() => {
    api<VoiceSettings>("/voice").then((s) => {
      settings.current = s;
      const why = blockedReason(s);
      if (why) { setProblem(why); setPhase("blocked"); }
      else setPhase(s.stt === "browser" && !s.browser_notice_seen ? "notice" : "ready");
    }).catch((e) => { setProblem((e as Error).message); setPhase("blocked"); });
    return () => { active.current = false; halt(); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  function blockedReason(s: VoiceSettings): string {
    if (s.stt === "browser" && !recognitionCtor()) return "This browser cannot recognise speech. Use Chrome, Edge or Safari, or connect a speech provider in Settings, Voice.";
    if (s.stt === "provider" && (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined")) return "This browser cannot record audio here. Voice needs a secure page (https or localhost).";
    if (s.tts === "browser" && !("speechSynthesis" in window)) return "This browser cannot speak. Connect a speech provider in Settings, Voice.";
    for (const k of ["stt", "tts"] as const) {
      const dest = k === "stt" ? s.stt_destination : s.tts_destination;
      if (s[k] === "provider" && dest && dest.local === false && s.consent[`${k}_model`]?.ref !== s[`${k}_model`])
        return `Audio would go to ${dest.label}. Agree to that in Settings, Voice, first.`;
    }
    return "";
  }

  const clearTimer = () => { if (timer.current !== null) { window.clearInterval(timer.current); timer.current = null; } };

  async function micOn() {
    if (media.current) return media.current;
    const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(s).connect(analyser);
    media.current = { stream: s, ctx, analyser };
    setMic(true);
    return media.current;
  }

  function micOff() {
    const m = media.current;
    media.current = null;
    if (m) { m.stream.getTracks().forEach((t) => t.stop()); m.ctx.close().catch(() => undefined); }
    if (!recog.current) setMic(false);
  }

  function level(): number {
    const m = media.current;
    if (!m) return 0;
    const buf = new Float32Array(m.analyser.fftSize);
    m.analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    return Math.sqrt(sum / buf.length);
  }

  function stopSpeaking() {
    speech.current += 1;
    clearTimer();
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    if (audio.current) { audio.current.onended = null; audio.current.pause(); URL.revokeObjectURL(audio.current.src); audio.current = null; }
  }

  /** Stop everything that is running: recognition, recording, speech, the model's turn, and the microphone. */
  function halt() {
    clearTimer();
    abort.current?.abort();
    abort.current = null;
    if (recog.current) { const r = recog.current; recog.current = null; r.onend = null; r.onresult = null; r.onerror = null; r.abort(); }
    if (recorder.current && recorder.current.state !== "inactive") { recorder.current.ondataavailable = null; recorder.current.onstop = null; recorder.current.stop(); }
    recorder.current = null;
    stopSpeaking();
    micOff();
    setMic(false);
    setInterim("");
  }

  function heardNothing() {
    quiet.current += 1;
    if (quiet.current >= 3) { setPhase("ready"); say("note", "Paused after hearing nothing. Press Talk to carry on."); micOff(); return; }
    listen();
  }

  function listen() {
    if (!active.current) return;
    setPhase("listening");
    if (settings.current?.stt === "provider") void listenRecorded();
    else listenBrowser();
  }

  function listenBrowser() {
    micOff();
    const R = recognitionCtor();
    if (!R) { setProblem("This browser cannot recognise speech."); setPhase("blocked"); return; }
    const r = new R();
    r.lang = navigator.language || "en-AU";
    r.interimResults = true;
    r.continuous = false;
    let final = "";
    r.onresult = (e) => {
      let partial = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) final += res[0].transcript; else partial += res[0].transcript;
      }
      setInterim(partial);
    };
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        recog.current = null; setMic(false);
        setProblem("The microphone is blocked for this page. Allow it in the browser's site settings, then start again.");
        setPhase("blocked"); active.current = false;
      } else if (e.error !== "no-speech" && e.error !== "aborted") say("note", `Speech recognition stopped (${e.error}).`);
    };
    r.onend = () => {
      if (recog.current !== r) return;
      recog.current = null;
      setMic(false);
      setInterim("");
      if (!active.current) return;
      if (final.trim()) void heard(final);
      else heardNothing();
    };
    recog.current = r;
    setMic(true);
    r.start();
  }

  async function listenRecorded() {
    let m;
    try { m = await micOn(); } catch {
      setProblem("The microphone is blocked or missing. Allow it in the browser's site settings, then start again.");
      setPhase("blocked"); active.current = false; return;
    }
    if (!active.current) return;
    const rec = new MediaRecorder(m.stream);
    const chunks: Blob[] = [];
    let spoke = false;
    let lastVoice = 0;
    let floor = 0;
    const started = Date.now();
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = async () => {
      clearTimer();
      recorder.current = null;
      if (!active.current) return;
      micOff();
      if (!spoke) { heardNothing(); return; }
      setPhase("transcribing");
      try {
        const text = await transcribe(new Blob(chunks, { type: rec.mimeType || "audio/webm" }), cid.current);
        if (active.current) await heard(text);
      } catch (e) {
        say("note", (e as Error).message);
        setPhase("ready");
      }
    };
    recorder.current = rec;
    rec.start(250);
    timer.current = window.setInterval(() => {
      const now = Date.now();
      const v = level();
      if (now - started < 300) { floor = Math.max(floor, v); return; } // measure the room first
      const threshold = Math.max(0.02, floor * 2.5);
      if (v > threshold) { spoke = true; lastVoice = now; }
      if ((spoke && now - lastVoice > 1200) || (!spoke && now - started > 8000) || now - started > 30000) {
        clearTimer();
        if (rec.state !== "inactive") rec.stop();
      }
    }, 50);
  }

  async function heard(raw: string) {
    const text = raw.trim();
    if (!text) { heardNothing(); return; }
    quiet.current = 0;
    say("you", text);
    if (END_WORDS.test(text)) { finish(); return; }
    await think(text);
  }

  async function think(text: string, shown = true) {
    if (!active.current) return;
    if (!shown) say("note", "Asked for a briefing.");
    setPhase("thinking");
    const ctl = new AbortController();
    abort.current = ctl;
    const replies: string[] = [];
    let approval = false;
    let failure = "";
    try {
      await stream("/assistant/chat", { message: text, conversation_id: cid.current, project_id: cid.current ? null : projectId, voice: true }, (ev) => {
        const t = ev.type as string | undefined;
        if (t === "start" && !cid.current) cid.current = Number(ev.conversation_id);
        if (t === "message" && ev.content) replies.push(String(ev.content));
        if (t === "approval") approval = true;
        if (t === "error" || (!t && ev.error)) failure = String(ev.error);
      }, ctl.signal);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      failure = (e as Error).message;
    }
    if (!active.current) return;
    if (failure) { say("note", failure); setPhase("ready"); return; }
    let reply = replies.join(" ");
    if (approval) reply += " One step needs your approval. It is waiting in the chat.";
    say("rooks", reply || "I have nothing to add.");
    await speak(reply || "I have nothing to add.");
  }

  async function speak(text: string) {
    if (!active.current) return;
    setPhase("speaking");
    const mine = ++speech.current;
    const words = spoken(text);
    const done = () => { clearTimer(); if (active.current) listen(); };
    if (bargeRef.current) {
      try {
        await micOn();
        const recent: boolean[] = []; // the last half second, in 50 ms frames; speech dips between syllables
        let floor = 0;
        const started = Date.now();
        timer.current = window.setInterval(() => {
          const v = level();
          if (Date.now() - started < 400) { floor = Math.max(floor, v); return; }
          recent.push(v > Math.max(0.05, floor * 3));
          if (recent.length > 10) recent.shift();
          if (recent.filter(Boolean).length >= 6) { say("note", "Interrupted."); stopSpeaking(); listen(); } // about 300 ms of your voice
        }, 50);
      } catch { /* no microphone for interrupting: the Interrupt button still works */ }
    }
    if (!active.current || speech.current !== mine) return;
    if (settings.current?.tts === "provider") {
      const ctl = new AbortController();
      abort.current = ctl;
      try {
        const blob = await speakAudio(words, cid.current, ctl.signal);
        if (!active.current || speech.current !== mine) return;
        const a = new Audio(URL.createObjectURL(blob));
        audio.current = a;
        a.onended = () => { URL.revokeObjectURL(a.src); audio.current = null; done(); };
        await a.play();
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        say("note", `Could not speak that: ${(e as Error).message}`);
        done();
      }
    } else {
      const u = new SpeechSynthesisUtterance(words);
      u.lang = navigator.language || "en-AU";
      u.onend = done;
      u.onerror = (e) => { if (e.error !== "interrupted" && e.error !== "canceled") done(); };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    }
  }

  async function start() {
    active.current = true;
    quiet.current = 0;
    setProblem("");
    try { mark.current = (await api<{ mark: number }>("/voice/sessions/start", { body: { conversation_id: cid.current } })).mark; }
    catch (e) { setProblem((e as Error).message); setPhase("blocked"); active.current = false; return; }
    if (opening) { say("rooks", opening); await speak(opening); }
    else if (briefing) await think(BRIEFING, false);
    else listen();
  }

  function resume() {
    active.current = true;
    quiet.current = 0;
    listen();
  }

  function interrupt() {
    if (phaseRef.current === "speaking") { stopSpeaking(); listen(); }
    else if (phaseRef.current === "thinking") {
      abort.current?.abort();
      if (cid.current) api(`/assistant/stop/${cid.current}`, { method: "POST" }).catch(() => undefined);
      say("note", "Stopped.");
      setPhase("ready");
    }
  }

  function finish() {
    const wasThinking = phaseRef.current === "thinking";
    active.current = false;
    halt();
    if (wasThinking && cid.current) api(`/assistant/stop/${cid.current}`, { method: "POST" }).catch(() => undefined);
    if (cid.current) setPhase("ending"); else onClose(conversationId);
  }

  async function end(keep: "transcript" | "summary" | "nothing") {
    if (!cid.current) { onClose(conversationId); return; }
    setEnding(true);
    try {
      const r = await api<{ conversation: number | null }>(`/voice/sessions/${cid.current}/end`, { body: { keep, mark: mark.current } });
      onClose(r.conversation);
    } catch (e) { setProblem((e as Error).message); }
    finally { setEnding(false); }
  }

  const acceptNotice = async () => {
    try { settings.current = await api<VoiceSettings>("/voice", { method: "PUT", body: { browser_notice_seen: true } }); setPhase("ready"); }
    catch (e) { setProblem((e as Error).message); }
  };

  const running = active.current && ["listening", "transcribing", "thinking", "speaking"].includes(phase);
  const s = settings.current;
  return (
    <div className="voice-panel card col" role="dialog" aria-modal="false" aria-label="Voice session">
      <div className="row wrap">
        <strong className="row" style={{ gap: 6 }}><Volume2 size={16} aria-hidden="true" /> Voice</strong>
        <span className={`badge ${phase === "listening" ? "accent" : phase === "blocked" ? "danger" : ""}`} aria-live="polite">
          {phase === "thinking" || phase === "transcribing" ? <Loader2 size={11} className="spin" aria-hidden="true" /> : null} {STATUS[phase]}
        </span>
        <span className={`badge ${mic ? "danger" : ""}`} title={mic ? "ROOKS is using your microphone" : "The microphone is off"}>
          {mic ? <Mic size={11} aria-hidden="true" /> : <MicOff size={11} aria-hidden="true" />} {mic ? "Microphone on" : "Microphone off"}
        </span>
        <div className="spacer" />
        {phase !== "ending" && <button type="button" className="btn sm danger" onClick={finish}><PhoneOff size={13} /> End</button>}
      </div>

      {phase === "notice" && (
        <div className="col">
          <p className="small" style={{ margin: 0 }}>Your browser will turn your speech into text. Chrome and Edge do this on Google's or Microsoft's
            servers, so your voice goes to them; Safari may do it on the device. ROOKS keeps no audio. To keep speech on your own network,
            connect a local speech provider in <Link to="/settings#voice">Settings, Voice</Link>.</p>
          <div className="row"><button type="button" className="btn primary sm" onClick={acceptNotice}>I understand, continue</button>
            <button type="button" className="btn sm" onClick={() => onClose(conversationId)}>Cancel</button></div>
        </div>
      )}

      {phase === "blocked" && (
        <div className="alert err" role="alert">{problem} {/Settings/.test(problem) && <Link to="/settings#voice">Open voice settings</Link>}</div>
      )}

      {phase === "ready" && !active.current && lines.length === 0 && (
        <div className="col">
          <div className="small muted">
            Listening: {s?.stt === "provider" ? s.stt_destination?.label ?? "your speech provider" : "this browser"} · Speaking: {s?.tts === "provider" ? s.tts_destination?.label ?? "your speech provider" : "this browser"}.
            ROOKS listens, then answers aloud. Say "goodbye" or press End to finish.
          </div>
          {!opening && <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={briefing} onChange={(e) => setBriefing(e.target.checked)} /> Start with a briefing</label>}
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={bargeIn} onChange={(e) => setBargeIn(e.target.checked)} />
            Interrupt by speaking (keeps the microphone on while ROOKS talks; headphones work best)</label>
          <div className="row"><button type="button" className="btn primary" onClick={start}><Mic size={15} /> Start talking</button></div>
        </div>
      )}

      {(lines.length > 0 || interim) && (
        <div className="voice-log col" aria-label="What was said" aria-live="polite">
          {lines.map((l, i) => (
            <div key={i} className={`small ${l.who === "note" ? "faint" : ""}`}><strong>{l.who === "you" ? "You" : l.who === "rooks" ? "ROOKS" : ""}</strong>{l.who !== "note" && ": "}{l.text}</div>
          ))}
          {interim && <div className="small faint"><strong>You</strong>: {interim}…</div>}
          <div ref={logEnd} />
        </div>
      )}

      {phase !== "ending" && phase !== "blocked" && phase !== "notice" && (lines.length > 0 || active.current) && (
        <div className="row wrap">
          {(phase === "speaking" || phase === "thinking") && <button type="button" className="btn sm" onClick={interrupt}><Square size={12} /> {phase === "speaking" ? "Interrupt" : "Stop thinking"}</button>}
          {phase === "ready" && <button type="button" className="btn primary sm" onClick={resume}><Mic size={13} /> Talk</button>}
          {running && phase === "listening" && <span className="small muted">Speak now.</span>}
        </div>
      )}

      {phase === "ending" && (
        <div className="col">
          <div className="small">What should ROOKS keep from this session? Anything it did, such as tasks it created, stays either way.</div>
          {problem && <div className="alert err" role="alert">{problem}</div>}
          <div className="row wrap">
            <button type="button" className="btn sm" disabled={ending} onClick={() => end("nothing")}>Delete what was said</button>
            <button type="button" className="btn sm" disabled={ending} onClick={() => end("summary")}>{ending ? <Loader2 size={12} className="spin" /> : null} Keep a short summary</button>
            <button type="button" className="btn primary sm" disabled={ending} onClick={() => end("transcript")}>Keep the transcript</button>
          </div>
        </div>
      )}
    </div>
  );
}
