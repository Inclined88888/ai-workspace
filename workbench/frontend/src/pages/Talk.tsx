/**
 * Hands-free voice (feat/v02-anywhere-voice). Free: dictation (the mic button in Chat, the ask bar and here, push to
 * talk). Pro: a spoken back-and-forth with the assistant, a wake word, talking over it to interrupt (barge-in), and
 * answering approval requests out loud. A spoken "yes" never skips the card: the card is on screen first, the words are
 * checked again on the server, and anything but a clear yes ("yes, send it") is asked again or treated as no.
 */
import { Ear, Loader2, Mic, MicOff, ShieldAlert, Square, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, stream } from "../api";
import { Markdown, useToast } from "../util";
import { listenOnce, Mic as MicT, openMic, osDictationHint, speak, stopSpeaking, voiceStatus, VoiceStatus, watchBargeIn } from "../voice/engine";
import { afterWake } from "../voice/logic";
import "../voice/voice.css";

type Turn = { id: number; role: "user" | "assistant" | "note"; text: string };
type Approval = { id: number; tool: string; args: string; effect: string; reason: string };
type Phase = "off" | "waiting" | "listening" | "thinking" | "speaking" | "confirming";
type Mode = "conversation" | "wake";

const PHASE: Record<Phase, string> = {
  off: "Not listening", waiting: "Say the wake word", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking (talk to interrupt)", confirming: "Waiting for your yes or no",
};

export default function Talk() {
  const [st, setSt] = useState<VoiceStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("off");
  const [mode, setMode] = useState<Mode>("conversation");
  const [mute, setMute] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [interim, setInterim] = useState("");
  const [level, setLevel] = useState(0);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [cid, setCid] = useState<number | null>(null);
  const toast = useToast();
  const run = useRef(0); // bumps to stop the loop
  const mic = useRef<MicT | null>(null);
  const abort = useRef<AbortController | null>(null);
  const cidRef = useRef<number | null>(null);
  const muteRef = useRef(false);
  const tapped = useRef(new Set<number>()); // approvals answered with the card's buttons while I was listening
  muteRef.current = mute;

  useEffect(() => { voiceStatus().then(setSt).catch(toast.err); return () => stop(); }, []);

  const pro = st?.features.voice_conversation?.allowed ?? false;
  const add = (role: Turn["role"], text: string) => setTurns((t) => [...t, { id: Date.now() + Math.random(), role, text }]);

  const say = async (text: string, token: number) => {
    if (!st || muteRef.current || token !== run.current) return;
    setPhase("speaking");
    const m = mic.current;
    let interrupted = false;
    const unwatch = m ? watchBargeIn(m, () => { interrupted = true; }) : () => undefined;
    await speak(text, { ...st.settings, language: st.settings.language || undefined });
    unwatch();
    if (interrupted) add("note", "You interrupted. Go ahead.");
  };

  const hear = async (token: number, waitMs = 8000) => {
    if (!st) return "";
    abort.current = new AbortController();
    setInterim("");
    const text = await listenOnce(mic.current, st, { signal: abort.current.signal, onLevel: setLevel, onInterim: setInterim, waitMs });
    setLevel(0); setInterim("");
    return token === run.current ? text.trim() : "";
  };

  /** One turn: send what was said to the assistant, show and speak the answer, and handle any approval out loud. */
  const turn = async (text: string, token: number) => {
    add("user", text);
    setPhase("thinking");
    let reply = "";
    const held: Approval[] = [];
    await stream("/assistant/chat", { message: text, conversation_id: cidRef.current }, (ev) => {
      if (ev.type === "start") { cidRef.current = Number(ev.conversation_id); setCid(cidRef.current); }
      if (ev.type === "message") reply = String(ev.content || "");
      if (ev.type === "approval_required") held.push({ id: Number(ev.id), tool: String(ev.tool), args: String(ev.args), effect: String(ev.effect), reason: String(ev.reason) });
      if (ev.type === "error" || (!ev.type && ev.error)) toast.err(String(ev.error));
    });
    if (token !== run.current) return;
    if (reply) { add("assistant", reply); await say(reply, token); }
    for (const a of held) await confirmByVoice(a, token);
  };

  const confirmByVoice = async (a: Approval, token: number) => {
    setApproval(a); // the card is on screen before anything is said
    const prompt = await api<{ say: string }>(`/voice/approval-prompt/${a.id}`).then((p) => p.say).catch(() => "I need your OK. It's on screen.");
    if (!pro) { await say(prompt.replace(/Say .*$/, "Tap Approve or Deny on the card."), token); return; }
    let ask = prompt;
    for (let tries = 0; tries < 3 && token === run.current; tries++) {
      await say(ask, token);
      setPhase("confirming");
      const heard = await hear(token, 10000);
      if (tapped.current.has(a.id)) return;
      if (!heard) { ask = "I didn't catch that. Say yes, send it, or no."; continue; }
      add("user", heard);
      // The server checks the words again and only a clear yes runs it
      const r = await api<{ decision: string; status?: string; result?: string; say?: string }>(`/voice/approvals/${a.id}`, { body: { utterance: heard } })
        .catch((e) => { toast.err(e); return null; });
      if (!r) return;
      if (r.decision === "unclear") { ask = r.say || "Sorry, I need a clear yes or no."; continue; }
      setApproval(null);
      const outcome = r.decision === "approve" ? (r.status === "failed" ? `That didn't work: ${r.result ?? ""}` : "Done.") : "OK, I won't do that.";
      add("note", outcome);
      await say(outcome, token);
      return;
    }
    if (token === run.current) await say("I'll leave it on screen. Tap Approve or Deny when you're ready.", token);
  };

  const decideByTap = async (a: Approval, approve: boolean) => {
    try {
      tapped.current.add(a.id);
      const r = await api<{ status: string; result: string }>(`/approvals/${a.id}`, { body: { approve } });
      setApproval(null);
      add("note", approve ? (r.status === "failed" ? `That didn't work: ${r.result}` : "Approved and done.") : "Denied.");
      abort.current?.abort();
    } catch (e) { toast.err(e); }
  };

  const loop = async (m: Mode) => {
    const token = ++run.current;
    try {
      mic.current = st?.engine === "browser" ? null : await openMic();
    } catch {
      toast.err("Microphone access is off. Allow it in your system settings, then try again.");
      setPhase("off");
      return;
    }
    let awake = m === "conversation";
    let lastTalk = Date.now();
    while (token === run.current) {
      setPhase(awake ? "listening" : "waiting");
      let text = "";
      try { text = await hear(token, awake ? 9000 : 15000); } catch (e) { toast.err(e); break; }
      if (token !== run.current) break;
      if (!text) {
        // In wake mode, go back to waiting for the wake word after a quiet spell
        if (m === "wake" && Date.now() - lastTalk > 12000) awake = false;
        continue;
      }
      if (!awake) {
        const rest = afterWake(text, st?.settings.wake_phrase || "hey workspace");
        if (rest === null) continue;
        awake = true;
        lastTalk = Date.now();
        if (!rest) { await say("Yes?", token); continue; }
        text = rest;
      }
      if (/^(stop listening|goodbye|bye|that's all|thats all)\b/i.test(text)) { await say("OK. Talk soon.", token); break; }
      await turn(text, token);
      lastTalk = Date.now();
    }
    if (token === run.current) stop();
  };

  const start = (m: Mode) => {
    if (!st) return;
    setMode(m);
    loop(m);
  };

  function stop() {
    run.current++;
    abort.current?.abort();
    stopSpeaking();
    mic.current?.close();
    mic.current = null;
    setPhase("off");
    setLevel(0);
  }

  /** Free: push to talk once (hold the button, or click and pause), then send it to Chat as text. */
  const pushToTalk = async () => {
    if (!st || phase !== "off") return;
    const token = ++run.current;
    try { mic.current = st.engine === "browser" ? null : await openMic(); } catch { toast.err("Microphone access is off. Allow it in your system settings."); return; }
    setPhase("listening");
    try {
      const text = await hear(token, 10000);
      if (text) await turn(text, token);
    } catch (e) { toast.err(e); }
    if (token === run.current) stop();
  };

  const engineName = !st ? "" : st.engine === "local" ? "Whisper on this computer (private, offline)"
    : st.engine === "cloud" ? `${st.engines.cloud?.name ?? "Cloud"} speech (${st.engines.cloud?.model ?? ""})` : "your browser's speech recognition";
  const on = phase !== "off";
  const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant");

  return (
    <div className="page talk-page">
      <div className="page-head">
        <div><h1>Talk</h1><p className="muted">Talk to your workspace instead of typing. Ask, plan, and get things done out loud.</p></div>
      </div>

      <section className="card talk-stage" aria-live="polite">
        <button type="button" className={`talk-orb ${phase}`} style={{ ["--level" as string]: String(Math.min(level * 14, 1)) }}
          onClick={() => (on ? stop() : pro ? start("conversation") : pushToTalk())} aria-label={on ? "Stop" : "Start talking"}>
          {phase === "thinking" ? <Loader2 size={34} className="spin" /> : phase === "speaking" ? <Volume2 size={34} /> : on ? <Square size={28} /> : <Mic size={34} />}
        </button>
        <div className="talk-phase">{PHASE[phase]}</div>
        {interim && <div className="talk-interim muted">{interim}</div>}
        {!on && lastAssistant && <div className="talk-last small muted truncate">{lastAssistant.text.slice(0, 160)}</div>}
        <div className="row wrap" style={{ justifyContent: "center", gap: 8 }}>
          {!on && pro && <button className="btn sm" onClick={() => start("wake")} disabled={!st?.wake_ok}
            title={st?.wake_ok ? `Say "${st.settings.wake_phrase}" to start` : "The wake word only listens on this computer. Turn on Whisper in Settings → Voice."}><Ear size={14} /> Wake word mode</button>}
          {!on && !pro && <button className="btn sm" onClick={pushToTalk}><Mic size={14} /> Push to talk</button>}
          <button className="btn ghost sm" onClick={() => { setMute((x) => !x); if (!mute) stopSpeaking(); }} aria-pressed={mute}>
            {mute ? <VolumeX size={14} /> : <Volume2 size={14} />} {mute ? "Voice replies off" : "Voice replies on"}
          </button>
          {on && <button className="btn ghost sm" onClick={stop}><MicOff size={14} /> Stop</button>}
        </div>
        {mode === "wake" && phase === "waiting" && <div className="small faint">Say “{st?.settings.wake_phrase}”, then your question. Say “stop listening” when you're done.</div>}
      </section>

      {approval && (
        <div className="card col approval-card" style={{ gap: 10 }} role="alertdialog" aria-label={`Approve ${approval.tool}?`}>
          <div className="row"><ShieldAlert size={16} style={{ color: "var(--warn-text)" }} /> <b>Approve {approval.tool.replace(/_/g, " ")}?</b></div>
          <div className="small muted">{approval.reason} {approval.effect === "persistent" ? "This would change how the assistant behaves from now on." : "This acts outside the workspace."}</div>
          <pre className="small mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{approval.args}</pre>
          <div className="small faint">{pro ? "Say “yes, send it” or “no”, or tap a button. Anything unclear is asked again." : "Tap a button to answer."}</div>
          <div className="row">
            <button className="btn primary sm" onClick={() => decideByTap(approval, true)}>Approve</button>
            <button className="btn sm" onClick={() => decideByTap(approval, false)}>Deny</button>
          </div>
        </div>
      )}

      {turns.length > 0 && (
        <section className="talk-log" aria-label="Conversation">
          {turns.map((t) => t.role === "user"
            ? <div key={t.id} className="msg user"><div className="bubble-user">{t.text}</div></div>
            : t.role === "note" ? <div key={t.id} className="talk-note small faint">{t.text}</div>
            : <div key={t.id} className="msg"><div className="msg-body"><Markdown text={t.text} /></div></div>)}
          {cid && <Link className="small" to={`/assistant/${cid}`}>Open this conversation in Chat</Link>}
        </section>
      )}

      <section className="card col talk-info small">
        {st && <div>Listening with {engineName}. Change it in <Link to="/voice">Voice settings</Link>.</div>}
        {st?.engine === "browser" && <div className="muted">If listening doesn't start, use your computer's own dictation: {osDictationHint()}, in any text box.</div>}
        {!pro && st && <div className="muted">Free includes dictation and push to talk. Hands-free conversation, the wake word and spoken approvals are part of Pro.</div>}
      </section>
      {toast.node}
    </div>
  );
}
