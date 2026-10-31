import { ArrowLeft, Play, ShieldAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { pickTheme, THEMES, useAppearance } from "../appearance";
import { CursorOverlay, DoneTick, EdgeGlow, LiveShot, Pointer, StreamText, Thinking, transitionClick, useDictation, useExit, VoiceOrb, dictationSupported } from "../motion";
import { Tracer } from "../Tracer";
import { SkeletonList } from "../util";

/**
 * Every signature moment in one place, live in the current theme and motion level, each with a Replay. It is
 * how people preview "Calm motion" before choosing it, and how we record and test the motion system.
 */
const REPLY = "Here's your week. **Monday** is clear until 2 pm, so I've put the site walk-through for Tower B at 9:30 and blocked an hour after it for the punch list. " +
  "Two invoices are overdue (J-1001 and J-1007); I've drafted reminders for you to check. Thursday's lab samples arrive at 11, and I've moved the review to Friday so you have the results first.";
const STEPS = [
  { kind: "call", text: "calendar_free_time · this week" },
  { kind: "ok", text: "found 6 free slots" },
  { kind: "call", text: "site_items · TradeTrack jobs" },
  { kind: "ok", text: "2 overdue: J-1001, J-1007" },
  { kind: "call", text: "draft_email · 2 reminders" },
  { kind: "ok", text: "verified: 2 drafts saved" },
];
// A plain mock desktop for the computer-use preview (an inline SVG, 1280×800, so it needs no server)
const SCREEN = "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800">
<rect width="1280" height="800" fill="#e9e4da"/><rect x="0" y="0" width="1280" height="28" fill="#f7f4ee"/>
<rect x="160" y="90" width="960" height="600" rx="12" fill="#fffdf8" stroke="#d8d0c0"/><rect x="160" y="90" width="960" height="40" rx="12" fill="#f3eee6"/>
<circle cx="186" cy="110" r="6" fill="#e0604f"/><circle cx="206" cy="110" r="6" fill="#e9b949"/><circle cx="226" cy="110" r="6" fill="#71b85a"/>
<text x="640" y="116" font-family="Helvetica" font-size="15" fill="#57524a" text-anchor="middle">Quote — Tower B.xlsx</text>
${Array.from({ length: 9 }, (_, r) => Array.from({ length: 6 }, (_, c) => `<rect x="${200 + c * 150}" y="${160 + r * 50}" width="146" height="46" fill="${r === 0 ? "#f3eee6" : "#ffffff"}" stroke="#e2dace"/>`).join("")).join("")}
<text x="215" y="190" font-family="Helvetica" font-size="15" font-weight="bold" fill="#2a2620">Item</text><text x="365" y="190" font-family="Helvetica" font-size="15" font-weight="bold" fill="#2a2620">Qty</text>
<text x="515" y="190" font-family="Helvetica" font-size="15" font-weight="bold" fill="#2a2620">Rate</text><text x="665" y="190" font-family="Helvetica" font-size="15" font-weight="bold" fill="#2a2620">Total</text>
<rect x="940" y="640" width="150" height="34" rx="8" fill="#2a2620"/><text x="1015" y="662" font-family="Helvetica" font-size="14" fill="#fffdf8" text-anchor="middle">Send quote</text>
</svg>`);
const PATH: Pointer[] = [
  { x: 0.2, y: 0.24, kind: "click" }, { x: 0.33, y: 0.3, kind: "click" }, { x: 0.45, y: 0.36, kind: "move" },
  { x: 0.57, y: 0.42, kind: "click" }, { x: 0.79, y: 0.82, kind: "click" },
];

function Section({ title, note, onReplay, children }: { title: string; note: string; onReplay?: () => void; children: React.ReactNode }) {
  return (
    <section className="card col motion-demo" style={{ gap: 12 }}>
      <div className="row"><h3 className="grow">{title}</h3>{onReplay && <button className="btn sm ghost" onClick={onReplay}><Play size={13} /> Replay</button>}</div>
      <p className="small muted" style={{ margin: 0 }}>{note}</p>
      <div className="motion-stage">{children}</div>
    </section>
  );
}

function useReplay() {
  const [k, setK] = useState(0);
  return [k, () => setK((x) => x + 1)] as const;
}

function TimelineDemo({ run }: { run: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    const t = setInterval(() => setN((x) => (x >= STEPS.length ? x : x + 1)), 650);
    return () => clearInterval(t);
  }, [run]);
  return (
    <div className="steps-list timeline">
      {STEPS.slice(0, n).map((s, i) => (
        <div key={`${run}-${i}`} className={`step ${s.kind === "ok" ? "ok" : i === n - 1 && n < STEPS.length ? "live" : ""}`}><span className="truncate">{s.text}</span></div>
      ))}
    </div>
  );
}

function ApprovalDemo({ run }: { run: number }) {
  const [state, setState] = useState<"ask" | "done">("ask");
  const exit = useExit<number>(200);
  useEffect(() => setState("ask"), [run]);
  if (state === "done") return <div className="done-row" key={`d${run}`}><DoneTick /> <b>Sent.</b> <span className="muted small">Both reminders went from your mail.</span></div>;
  return (
    <div key={run} className={`card col approval-card spring-in${exit.leaving(1) ? " leaving-out" : ""}`} style={{ gap: 10 }} role="alertdialog" aria-label="Approve send_email?">
      <div className="row"><ShieldAlert size={16} style={{ color: "var(--warn-text)" }} /> <b>Send 2 emails?</b></div>
      <div className="small muted">Reminders for J-1001 and J-1007, from your own mail.</div>
      <div className="row">
        <button className="btn primary sm" onClick={() => exit.run(1, () => setState("done"))}>Approve</button>
        <button className="btn sm" onClick={() => exit.run(1, () => setState("done"))}>Deny</button>
      </div>
    </div>
  );
}

function CursorDemo({ run }: { run: number }) {
  const [n, setN] = useState(1);
  useEffect(() => {
    setN(1);
    const t = setInterval(() => setN((x) => (x >= PATH.length ? x : x + 1)), 1100);
    return () => clearInterval(t);
  }, [run]);
  return (
    <LiveShot src={SCREEN} alt="A spreadsheet the assistant is filling in" scanning={n < PATH.length}>
      <CursorOverlay points={PATH.slice(0, n)} />
    </LiveShot>
  );
}

function OrbDemo() {
  const fake = useRef<HTMLButtonElement>(null);
  const [demo, setDemo] = useState(true);
  const [heard, setHeard] = useState("");
  const voice = useDictation((t) => setHeard(t));
  useEffect(() => {
    if (!demo) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const s = (t - t0) / 1000;
      const lvl = Math.max(0, 0.45 * Math.sin(s * 5.3) * Math.sin(s * 1.7) + 0.25 * Math.sin(s * 11) + 0.2);
      fake.current?.style.setProperty("--lvl", lvl.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [demo]);
  return (
    <div className="row" style={{ gap: 18 }}>
      <VoiceOrb on={demo} orbRef={fake} onClick={() => setDemo((d) => !d)} />
      <span className="small muted">{demo ? "Simulated voice. Tap to pause." : "Paused."}</span>
      {dictationSupported() && <><VoiceOrb on={voice.on} orbRef={voice.orb} onClick={voice.toggle} /><span className="small muted">{voice.on ? heard || "Listening…" : voice.err || "Try it with your own voice"}</span></>}
    </div>
  );
}

export default function MotionLab() {
  const [a, set] = useAppearance();
  const navigate = useNavigate();
  const [think, replayThink] = useReplay();
  const [stream, replayStream] = useReplay();
  const [tl, replayTl] = useReplay();
  const [ap, replayAp] = useReplay();
  const [cur, replayCur] = useReplay();
  const [tick, replayTick] = useReplay();
  const [glow, setGlow] = useState(false);
  const since = useRef(Date.now() - 7000);
  useEffect(() => { since.current = Date.now() - 7000; }, [think]);
  return (
    <div className="page motion-lab" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <div className="grow">
          <Link to="/appearance" className="small muted row" style={{ gap: 4 }} onClick={transitionClick(() => navigate("/appearance"))}><ArrowLeft size={13} /> Appearance</Link>
          <h1>Motion</h1>
          <p className="muted">Every moving moment, live in your theme. Pick Calm to keep them as gentle fades, or Off to keep them still.</p>
        </div>
        <div className="col" style={{ gap: 8, alignItems: "flex-end" }}>
          <div className="ap-seg" role="radiogroup" aria-label="Motion">
            {([["full", "Full"], ["subtle", "Calm"], ["off", "Off"]] as const).map(([v, l]) => (
              <button key={v} type="button" role="radio" aria-checked={a.motion === v} onClick={() => set({ motion: v })}>{l}</button>
            ))}
          </div>
          <div className="row wrap" style={{ gap: 6, justifyContent: "flex-end" }}>
            {THEMES.filter((t) => t.id !== "auto").map((t) => (
              <button key={t.id} className={`btn sm${a.theme === t.id ? " primary" : ""}`} onClick={() => pickTheme(t.id)}>{t.label}</button>
            ))}
          </div>
        </div>
      </div>
      <div className="grid two motion-grid">
        <Section title="Thinking" note="The assistant's name shimmers while it thinks. After a few seconds it says how long, and names the tool it's using." onReplay={replayThink}>
          <div className="msg msg-thinking" key={think}><div className="msg-body">
            <div className="msg-byline is-thinking" aria-hidden="true">Workspace</div>
            <Thinking since={since.current} phase={think % 2 ? "web search" : undefined} />
          </div></div>
        </Section>
        <Section title="Replies, word by word" note="Only the newest words fade in, so the text never jumps and you can read along. One caret marks where the next word lands." onReplay={replayStream}>
          <StreamText key={stream} text={REPLY} />
        </Section>
        <Section title="What it's doing" note="Each step draws its own piece of the line, then its dot settles. The live step breathes until it's done." onReplay={replayTl}>
          <TimelineDemo run={tl} />
        </Section>
        <Section title="Asking first" note="Approval cards rise and settle on a spring, then lift away once you answer." onReplay={replayAp}>
          <ApprovalDemo run={ap} />
        </Section>
        <Section title="Using your computer" note="Its own cursor glides to each spot, leaves a faint trail and rings where it clicks. A reading line sweeps while it looks. Your real mouse never moves." onReplay={replayCur}>
          <CursorDemo run={cur} />
        </Section>
        <Section title="Edge glow" note="While Rooks Desk controls the computer, the window's edge lights up in your theme's colours, with Stop always one click away.">
          <div className="row" style={{ gap: 10 }}>
            <button className="btn" onClick={() => setGlow((g) => !g)}>{glow ? "Turn the glow off" : "Light up the edge"}</button>
            <span className="small muted">Neon glows. High contrast is a solid keyline. Calm holds the light still.</span>
          </div>
          <EdgeGlow active={glow} label="Preview: Rooks Desk is using your computer" onStop={() => setGlow(false)} />
        </Section>
        <Section title="Done" note="A ring draws itself, the tick strokes in, and it settles. Saved for things that matter: a job finished, an email sent." onReplay={replayTick}>
          <div className="done-row" key={tick}><DoneTick size={28} /> <b>Quote sent to Dana.</b></div>
        </Section>
        <Section title="Talk instead of typing" note="The orb breathes while it listens and swells with your voice. It's in the chat box wherever your browser supports speech.">
          <OrbDemo />
        </Section>
        <Section title="Loading" note="A soft light passes over placeholders instead of the whole block blinking.">
          <div className="card flush"><SkeletonList rows={3} avatar /></div>
        </Section>
        <Section title="Working border" note="The moving line round anything that's busy (already in the chat box and running jobs).">
          <div className="has-tracer card" style={{ padding: 18 }}><Tracer active label="Working" /><span className="muted">Checking your portals…</span></div>
        </Section>
      </div>
    </div>
  );
}
