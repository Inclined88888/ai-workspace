/**
 * Listening and speaking in the page (feat/v02-anywhere-voice).
 *
 * Speech to text, best first: Whisper on this computer → a cloud speech model you've connected → the browser's own
 * recognition (Web Speech) → the operating system's dictation (Fn Fn on a Mac, Windows + H) typed into the box.
 * Text to speech: the computer's own voices, or a cloud voice when chosen in Settings.
 */
import { api } from "../api";
import { sentences } from "./logic";

export type Feature = { feature: string; tier: "free" | "pro"; allowed: boolean };
export type VoiceStatus = {
  settings: { stt: string; tts: string; language: string; name: string; rate: string; wake_phrase: string; local_model: string; cloud_voice: string };
  engines: { local: boolean; cloud: { name: string; model: string } | null; cloud_voice: { name: string; model: string } | null; ffmpeg: boolean };
  /** "local" | "cloud" | "browser": who turns speech into text right now */
  engine: string;
  wake_ok: boolean;
  features: Record<string, Feature>;
};

export const voiceStatus = () => api<VoiceStatus>("/voice/status");

type SR = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null;
  start: () => void; stop: () => void; abort: () => void;
};
const SRClass = () => (window as unknown as { SpeechRecognition?: new () => SR; webkitSpeechRecognition?: new () => SR }).SpeechRecognition
  ?? (window as unknown as { webkitSpeechRecognition?: new () => SR }).webkitSpeechRecognition;
export const browserRecognition = () => !!SRClass();
export const canRecord = () => typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
export const isMac = () => /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const osDictationHint = () => (isMac() ? "press the Fn key twice (or 🎤 on the keyboard)" : "press Windows + H");

export function mimeType(): string {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(t)) return t;
  }
  return "";
}
export const extFor = (mime: string) => (mime.includes("ogg") ? "ogg" : mime.includes("mp4") ? "m4a" : "webm");

/** Speech to text for recorded audio, on this computer or in the cloud (see the module comment). */
export async function transcribeBlob(blob: Blob, lang = ""): Promise<string> {
  const res = await fetch(`/api/voice/transcribe?name=speech.${extFor(blob.type)}&lang=${encodeURIComponent(lang)}`, {
    method: "POST", credentials: "same-origin", headers: { "X-Workbench": "1", "Content-Type": blob.type || "application/octet-stream" }, body: blob,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail || `Couldn't turn that into text (${res.status})`), { status: res.status });
  return String(body.text || "").trim();
}

// ---- Microphone, level meter and "you stopped talking" ---------------------------------------------------------------
export type Mic = { stream: MediaStream; level: () => number; close: () => void };

export async function openMic(): Promise<Mic> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new AudioContext();
  const src = ctx.createMediaStreamSource(stream);
  const an = ctx.createAnalyser();
  an.fftSize = 1024;
  src.connect(an);
  const buf = new Float32Array(an.fftSize);
  return {
    stream,
    level: () => {
      an.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      return Math.sqrt(sum / buf.length);
    },
    close: () => { stream.getTracks().forEach((t) => t.stop()); ctx.close().catch(() => undefined); },
  };
}

const SPEECH = 0.025; // RMS above this counts as talking (after the OS's noise suppression)

/**
 * Record one utterance: waits up to `waitMs` for speech to start, then stops after `silenceMs` of quiet (or `maxMs`).
 * Resolves null when nobody spoke. `onLevel` drives the meter.
 */
export function recordUtterance(mic: Mic, opts: { waitMs?: number; silenceMs?: number; maxMs?: number; signal?: AbortSignal; onLevel?: (l: number) => void; manual?: boolean } = {}): Promise<Blob | null> {
  const { waitMs = 8000, silenceMs = 1100, maxMs = 60000, signal, onLevel, manual = false } = opts;
  return new Promise((resolve) => {
    const mime = mimeType();
    const rec = new MediaRecorder(mic.stream, mime ? { mimeType: mime } : undefined);
    const chunks: Blob[] = [];
    let spoke = manual;
    let quietSince = 0;
    const started = Date.now();
    rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = () => { clearInterval(t); resolve(spoke && chunks.length ? new Blob(chunks, { type: rec.mimeType || mime }) : null); };
    const stop = () => { if (rec.state !== "inactive") rec.stop(); };
    signal?.addEventListener("abort", stop); // "stop": keep what was said so far
    const t = setInterval(() => {
      const l = mic.level();
      onLevel?.(l);
      const now = Date.now();
      if (l > SPEECH) { spoke = true; quietSince = 0; } else if (spoke && !quietSince) quietSince = now;
      if (manual) { if (now - started > maxMs) stop(); return; }
      if (!spoke && now - started > waitMs) stop();
      else if (spoke && quietSince && now - quietSince > silenceMs) stop();
      else if (now - started > maxMs) stop();
    }, 60);
    rec.start(250);
  });
}

/** One phrase with the browser's own recognition (used when there's no Whisper and no cloud model). */
export function recogniseOnce(lang = "", signal?: AbortSignal, onInterim?: (t: string) => void): Promise<string> {
  const C = SRClass();
  if (!C) return Promise.reject(new Error(`Speech recognition isn't available here. Use your computer's dictation: ${osDictationHint()}.`));
  return new Promise((resolve, reject) => {
    const r = new C();
    r.lang = lang || navigator.language || "en-US";
    r.continuous = false;
    r.interimResults = true;
    let finalText = "";
    r.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) finalText += res[0].transcript; else interim += res[0].transcript;
      }
      onInterim?.((finalText + interim).trim());
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      reject(new Error(e.error === "not-allowed" ? "Microphone access is off. Allow it in your system settings." :
        e.error === "network" ? `This app can't reach the speech service. Use your computer's dictation: ${osDictationHint()}.` : `Speech recognition stopped (${e.error}).`));
    };
    r.onend = () => resolve(finalText.trim());
    signal?.addEventListener("abort", () => r.stop());
    r.start();
  });
}

// ---- Speaking ------------------------------------------------------------------------------------------------------
let audio: HTMLAudioElement | null = null;
let speakToken = 0;

export function stopSpeaking() {
  speakToken++;
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  if (audio) { audio.pause(); audio = null; }
}

export const systemVoices = () => (typeof speechSynthesis !== "undefined" ? speechSynthesis.getVoices() : []);

/** Say a reply out loud, a sentence at a time. Resolves when finished or stopped (stopSpeaking / barge-in). */
export async function speak(text: string, s: { tts: string; name: string; rate: string; language?: string }): Promise<void> {
  stopSpeaking();
  const token = ++speakToken;
  const parts = sentences(text);
  if (!parts.length) return;
  if (s.tts === "cloud") {
    try {
      const res = await fetch("/api/voice/speak", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Workbench": "1" }, body: JSON.stringify({ text: parts.join(" ").slice(0, 4000) }) });
      if (!res.ok) throw new Error();
      const url = URL.createObjectURL(await res.blob());
      if (token !== speakToken) return;
      audio = new Audio(url);
      audio.playbackRate = Number(s.rate) || 1;
      await new Promise<void>((done) => { audio!.onended = () => done(); audio!.onpause = () => done(); audio!.play().catch(() => done()); });
      URL.revokeObjectURL(url);
      return;
    } catch {
      /* fall through to the computer's own voice */
    }
  }
  if (typeof speechSynthesis === "undefined") return;
  const voice = systemVoices().find((v) => v.name === s.name);
  for (const p of parts) {
    if (token !== speakToken) return;
    await new Promise<void>((done) => {
      const u = new SpeechSynthesisUtterance(p);
      if (voice) u.voice = voice;
      if (s.language) u.lang = s.language;
      u.rate = Number(s.rate) || 1;
      u.onend = () => done();
      u.onerror = () => done();
      speechSynthesis.speak(u);
    });
  }
}

/** While speaking, listen for the person talking over it; calls onBargeIn (once) and returns a stop function. */
export function watchBargeIn(mic: Mic, onBargeIn: () => void): () => void {
  let loud = 0;
  let fired = false;
  const t = setInterval(() => {
    // Echo cancellation removes most of our own voice; require a sustained, louder level to count as talking over it
    if (mic.level() > SPEECH * 1.8) loud += 60; else loud = Math.max(0, loud - 30);
    if (loud >= 300 && !fired) { fired = true; stopSpeaking(); onBargeIn(); }
  }, 60);
  return () => clearInterval(t);
}

/** Listen for one phrase with whichever engine is set up. "" when nothing was said. */
export async function listenOnce(mic: Mic | null, st: VoiceStatus, o: { signal?: AbortSignal; onLevel?: (l: number) => void; onInterim?: (t: string) => void; waitMs?: number; manual?: boolean } = {}): Promise<string> {
  if (st.engine === "browser" || !mic) return recogniseOnce(st.settings.language, o.signal, o.onInterim);
  const blob = await recordUtterance(mic, { signal: o.signal, onLevel: o.onLevel, waitMs: o.waitMs, manual: o.manual });
  if (!blob) return "";
  return transcribeBlob(blob, st.settings.language);
}
