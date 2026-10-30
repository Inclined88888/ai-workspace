import { ArrowLeft, Check, Globe, Mic, Monitor, Pencil, Play, Plus, Square, ThumbsDown, ThumbsUp, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { Param, Playbook, Run, Step, eventLine, missingValues, runFinished, runLabel, stepParts } from "../learn/text";
import "../learn/learn.css";

type Surface = "browser" | "desktop" | "narration";
type Recorders = Record<Surface, boolean>;
type Recording = { id: number; surface: Surface; status: string; error: string; events: { kind: string; [k: string]: unknown }[] };
type Suggestion = { id: number; title: string; detail: string; cron: string; kind: string };

const SURFACES: { id: Surface; label: string; icon: typeof Globe; hint: string }[] = [
  { id: "browser", label: "On a website", icon: Globe, hint: "A browser window opens. Do the task once, as you normally would." },
  { id: "desktop", label: "On my computer", icon: Monitor, hint: "Do it in your usual apps while I watch the screen." },
  { id: "narration", label: "Just tell me", icon: Mic, hint: "Describe the steps in your own words." },
];

/** "Show me how": watch a task once, turn it into steps the person can check, then repeat it on request or on a schedule. */
export default function Teach() {
  const { id } = useParams();
  return id ? <PlaybookView id={Number(id)} /> : <Home />;
}

function Home() {
  const [recorders, setRecorders] = useState<Recorders | null>(null);
  const [surface, setSurface] = useState<Surface>("browser");
  const [url, setUrl] = useState("");
  const [rec, setRec] = useState<Recording | null>(null);
  const [narration, setNarration] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<Playbook[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const nav = useNavigate();
  const toast = useToast();

  const load = useCallback(() => {
    api<Playbook[]>("/learn/playbooks").then(setItems).catch(() => {});
    api<Suggestion[]>("/learn/suggestions").then(setSuggestions).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    api<Recorders>("/learn/recorders").then((r) => { setRecorders(r); if (!r.browser) setSurface(r.desktop ? "desktop" : "narration"); }).catch(() => {});
  }, [load]);

  useEffect(() => {  // live view while recording
    if (!rec || rec.status !== "recording") return;
    const t = setInterval(() => api<Recording>(`/learn/recordings/${rec.id}`).then(setRec).catch(() => {}), 1500);
    return () => clearInterval(t);
  }, [rec?.id, rec?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setBusy(true);
    try { setRec(await api<Recording>("/learn/recordings", { body: { surface, start_url: url } })); } catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  const stop = async () => {
    if (!rec) return;
    setBusy(true);
    try {
      const pb = await api<Playbook>(`/learn/recordings/${rec.id}/stop`, { body: { narration, refine: true } });
      nav(`/teach/${pb.id}`);
    } catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  const accept = async (s: Suggestion) => {
    try { await api(`/learn/suggestions/${s.id}/accept`, { body: {} }); toast.ok("Done. You'll find it under Automations."); load(); } catch (e) { toast.err(e); }
  };
  const dismiss = async (s: Suggestion) => { try { await api(`/learn/suggestions/${s.id}/dismiss`, { body: {} }); load(); } catch (e) { toast.err(e); } };

  const recording = rec && rec.status === "recording";
  return (
    <div className="page" style={{ maxWidth: 960 }}>
      {toast.node}
      <div className="page-head">
        <div className="grow">
          <p className="kicker">Your assistant</p>
          <h1>Show me how</h1>
          <p className="muted">Show me a task once. I'll write down the steps, you check them, and next time I do it for you.</p>
        </div>
      </div>

      {suggestions.map((s) => (
        <div key={s.id} className="learn-suggest">
          <p className="title">{s.title}</p>
          <p className="muted">{s.detail}</p>
          <div className="row">
            <button className="btn primary sm" onClick={() => accept(s)}><Check size={14} /> Yes, do it for me</button>
            <button className="btn ghost sm" onClick={() => dismiss(s)}>No thanks</button>
          </div>
        </div>
      ))}

      <section className="learn-hero">
        {!recording ? (
          <>
            <h2>Teach me something new</h2>
            <div className="learn-choice" role="group" aria-label="Where you'll show me">
              {SURFACES.map((s) => {
                const off = recorders ? !recorders[s.id] : false;
                return (
                  <button key={s.id} className="btn" aria-pressed={surface === s.id} disabled={off} title={off ? "Not connected yet" : s.hint} onClick={() => setSurface(s.id)}>
                    <s.icon size={14} /> {s.label}
                  </button>
                );
              })}
            </div>
            <p className="muted" style={{ marginTop: 0 }}>{SURFACES.find((s) => s.id === surface)?.hint}</p>
            {surface === "browser" && (
              <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Where does it start? e.g. xero.com (optional)" aria-label="Start page" style={{ marginBottom: 12 }} />
            )}
            {surface === "narration" && (
              <textarea className="input" rows={5} value={narration} onChange={(e) => setNarration(e.target.value)} aria-label="The steps"
                placeholder={"e.g. Every Monday I open the timesheet portal.\nI fill in my hours for each day.\nThen I submit it before 5pm."} style={{ marginBottom: 12 }} />
            )}
            <div className="row">
              {surface === "narration"
                ? <button className="btn primary" disabled={busy || narration.trim().length < 8} onClick={async () => { await start(); }}>Continue</button>
                : <button className="btn primary" disabled={busy} onClick={start}><Play size={14} /> Start showing me</button>}
            </div>
          </>
        ) : (
          <>
            <h2><span className="learn-rec-dot" aria-hidden="true" />{rec.surface === "narration" ? "Almost there" : "I'm watching"}</h2>
            {rec.surface !== "narration" && <p className="muted">Do the task in the window that opened. I never record passwords. Press Done when you're finished.</p>}
            {rec.error && <div className="alert">{rec.error}</div>}
            {rec.surface !== "narration" && (
              <div className="learn-live" aria-live="polite">
                {rec.events.length ? rec.events.slice(-30).map((e, i) => <div key={i}>{eventLine(e as Parameters<typeof eventLine>[0])}</div>) : <div>Waiting for your first step…</div>}
              </div>
            )}
            <textarea className="input" rows={3} value={narration} onChange={(e) => setNarration(e.target.value)} aria-label="Anything to add"
              placeholder="Anything I should know? e.g. Only chase invoices over 30 days, and be friendly." />
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn primary" disabled={busy} onClick={stop}><Square size={14} /> {busy ? "Writing it up…" : "Done"}</button>
              <button className="btn ghost" disabled={busy} onClick={() => setRec(null)}>Cancel</button>
            </div>
          </>
        )}
      </section>

      <section className="learn-section">
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>Things you've shown me</h2>
        {!items.length && <Empty title="Nothing yet" hint="When you show me a task it appears here, ready to repeat." />}
        {items.map((p) => (
          <button key={p.id} className="learn-pb" onClick={() => nav(`/teach/${p.id}`)}>
            <div className="grow">
              <div className="name">{p.name}</div>
              <div className="learn-meta">{p.steps.length} steps · {p.runs ? `done ${p.runs}×` : "not run yet"} · {p.status === "draft" ? "needs your check" : "ready"} · {relTime(p.updated_at)}</div>
            </div>
            <span className="badge">{p.surface === "desktop" ? "Computer" : p.surface === "browser" ? "Website" : "Described"}</span>
          </button>
        ))}
      </section>
    </div>
  );
}

function PlaybookView({ id }: { id: number }) {
  const [pb, setPb] = useState<(Playbook & { runs_list: Run[]; last_values: Record<string, string> }) | null>(null);
  const [edit, setEdit] = useState<{ name: string; steps: Step[] } | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [run, setRun] = useState<Run | null>(null);
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const timer = useRef<ReturnType<typeof setInterval>>();
  const nav = useNavigate();
  const toast = useToast();

  const load = useCallback(() => api<NonNullable<typeof pb>>(`/learn/playbooks/${id}`).then((p) => {
    setPb(p);
    setValues((v) => (Object.keys(v).length ? v : Object.fromEntries(p.params.map((x: Param) => [x.name, p.last_values[x.name] || x.example || ""]))));
  }).catch(toast.err), [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); return () => clearInterval(timer.current); }, [load]);

  const save = async (ready = false) => {
    if (!pb) return;
    try {
      const body = edit ? { name: edit.name, steps: edit.steps.filter((s) => s.text.trim()), ready } : { ready };
      const r = await api<Playbook & { note: string }>(`/learn/playbooks/${id}`, { method: "PUT", body });
      if (r.note) setNote(r.note);
      setEdit(null);
      toast.ok(ready ? "Saved. I'm ready to do this." : "Saved");
      load();
    } catch (e) { toast.err(e); }
  };
  const go = async () => {
    if (!pb) return;
    const missing = missingValues(pb.params, values);
    if (missing.length) return toast.err(`Tell me: ${missing.map((m) => m.label).join(", ")}`);
    try {
      const r = await api<Run>(`/learn/playbooks/${id}/run`, { body: { values } });
      setRun(r);
      clearInterval(timer.current);
      timer.current = setInterval(async () => {
        const s = await api<Run>(`/learn/runs/${r.id}`).catch(() => null);
        if (s) setRun(s);
        if (!s || runFinished(s.status)) { clearInterval(timer.current); load(); }
      }, 2000);
    } catch (e) { toast.err(e); }
  };
  const rate = async (rating: number) => {
    if (!run) return;
    try {
      const r = await api<{ note: string }>("/learn/feedback", { body: { target_kind: "run", target_id: String(run.id), rating, comment } });
      setNote(r.note || (rating > 0 ? "Thanks. I'll keep doing it this way." : ""));
      setComment("");
    } catch (e) { toast.err(e); }
  };
  const remove = async () => { try { await api(`/learn/playbooks/${id}`, { method: "DELETE" }); nav("/teach"); } catch (e) { toast.err(e); } };

  if (!pb) return <div className="page" />;
  const steps = edit ? edit.steps : pb.steps;
  return (
    <div className="page" style={{ maxWidth: 900 }}>
      {toast.node}
      <button className="btn ghost sm" onClick={() => nav("/teach")} style={{ marginBottom: 16 }}><ArrowLeft size={14} /> Show me how</button>
      <div className="page-head">
        <div className="grow">
          <p className="kicker">{pb.status === "draft" ? "Check these steps" : `Done ${pb.runs}× · worked ${pb.successes}×`}</p>
          {edit ? <input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-label="Name" style={{ fontSize: "var(--text-xl, 22px)" }} />
            : <h1>{pb.name}</h1>}
          <p className="muted">{pb.summary}</p>
        </div>
        {!edit && <button className="btn" onClick={() => setEdit({ name: pb.name, steps: pb.steps.map((s) => ({ ...s })) })}><Pencil size={14} /> Change steps</button>}
      </div>

      {note && <div className="learn-note" role="status" style={{ marginBottom: 16 }}>{note}</div>}

      <section className="learn-section">
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>Steps</h2>
        <ol className="learn-steps">
          {steps.map((s, i) => (
            <li key={i}>
              <div className="grow">
                {edit ? (
                  <input className="input" value={s.text} aria-label={`Step ${i + 1}`}
                    onChange={(e) => setEdit({ ...edit, steps: edit.steps.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })} />
                ) : (
                  <span>
                    {stepParts(s.text, values).map((p, k) => (p.param ? <span key={k} className="param">{p.text}</span> : <span key={k}>{p.text}</span>))}
                    {s.sensitive && <span className="learn-flag">I'll ask first</span>}
                    {s.needs_user && <span className="learn-flag">You do this</span>}
                  </span>
                )}
              </div>
              {edit && <button className="btn ghost sm" aria-label="Remove step" onClick={() => setEdit({ ...edit, steps: edit.steps.filter((_, j) => j !== i) })}><X size={14} /></button>}
            </li>
          ))}
        </ol>
        {edit && (
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn ghost sm" onClick={() => setEdit({ ...edit, steps: [...edit.steps, { action: "note", text: "" }] })}><Plus size={14} /> Add a step</button>
            <span className="grow" />
            <button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn primary" onClick={() => save(true)}><Check size={14} /> Save</button>
          </div>
        )}
        {!edit && !!pb.notes.length && (
          <div className="learn-meta" style={{ marginTop: 12 }}>What I've learned: {pb.notes.slice(-4).join(" · ")}</div>
        )}
      </section>

      {!edit && (
        <section className="learn-section">
          <h2 style={{ fontFamily: "var(--serif)", fontWeight: 500 }}>Do it now</h2>
          {!!pb.params.length && (
            <div className="learn-fields">
              {pb.params.map((p) => (
                <label key={p.name}>{p.label}
                  <input className="input" value={values[p.name] || ""} onChange={(e) => setValues({ ...values, [p.name]: e.target.value })} />
                </label>
              ))}
            </div>
          )}
          <div className="row">
            {pb.status === "draft" && <button className="btn" onClick={() => save(true)}><Check size={14} /> Looks right</button>}
            <button className="btn primary" onClick={go} disabled={!!run && !runFinished(run.status)}><Play size={14} /> Do it for me</button>
            <span className="grow" />
            <button className="btn ghost sm" onClick={remove}><Trash2 size={14} /> Delete</button>
          </div>
          {run && (
            <div className="learn-run" aria-live="polite">
              <strong>{runLabel(run.status)}</strong>
              {run.status === "needs_you" && <p className="muted">I've stopped before anything goes out. Check the browser window and approve it there.</p>}
              {run.result && <p>{run.result}</p>}
              {runFinished(run.status) && (
                <div style={{ marginTop: 12 }}>
                  <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Anything to do differently next time?" aria-label="Feedback" />
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="btn sm" onClick={() => rate(1)}><ThumbsUp size={14} /> Good</button>
                    <button className="btn sm" onClick={() => rate(-1)}><ThumbsDown size={14} /> Not quite</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
