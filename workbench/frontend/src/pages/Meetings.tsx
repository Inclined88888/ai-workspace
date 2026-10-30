/**
 * Meeting companion (feat/v02-anywhere-voice; Pro). Record a meeting (your microphone, plus the computer's sound so the
 * other people on a call are heard too) or bring in a recording or a transcript from Zoom, Teams or Meet. You get notes,
 * decisions, and action items with owners and dates; the actions can go into Tasks, and the follow-up email is saved as
 * a draft for you to check and send yourself. Nothing is ever sent, and recording starts only after the consent notice.
 */
import { ArrowLeft, Check, CheckSquare, Circle, Copy, FileUp, Loader2, Mail, Mic, NotebookPen, ShieldCheck, Square, Trash2, Users } from "lucide-react";
import { ChangeEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Empty, ModelSelect, relTime, SkeletonList, useModels, useToast } from "../util";
import { extFor, mimeType } from "../voice/engine";
import { Action, actionMeta, clock, notesText } from "../voice/logic";
import "../voice/voice.css";

type Notes = { summary: string; notes: string[]; decisions: string[]; actions: Action[]; attendees: string[]; follow_up: { subject: string; body: string } };
type Seg = { start: number; end: number; speaker: string; text: string };
type Meeting = {
  id: number; title: string; source: "recording" | "audio" | "transcript"; status: "recording" | "ready" | "summarised"; consent_at: string | null;
  project: string; seconds: number; created_at: string; updated_at: string; notes: Notes | null; segments?: Seg[]; transcript?: string; preview?: string;
};

const CHUNK_MS = 30_000;

/** Mic + (when allowed) the computer's sound, mixed into one stream. */
async function openSources(systemAudio: boolean): Promise<{ stream: MediaStream; system: boolean; close: () => void }> {
  const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  let screen: MediaStream | null = null;
  if (systemAudio && navigator.mediaDevices.getDisplayMedia) {
    try {
      screen = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
      screen.getVideoTracks().forEach((t) => t.stop()); // only the sound is used
      if (!screen.getAudioTracks().length) { screen = null; }
    } catch { screen = null; }
  }
  const ctx = new AudioContext();
  const dest = ctx.createMediaStreamDestination();
  ctx.createMediaStreamSource(mic).connect(dest);
  if (screen) ctx.createMediaStreamSource(new MediaStream(screen.getAudioTracks())).connect(dest);
  return {
    stream: dest.stream, system: !!screen,
    close: () => { mic.getTracks().forEach((t) => t.stop()); screen?.getTracks().forEach((t) => t.stop()); ctx.close().catch(() => undefined); },
  };
}

async function upload(path: string, blob: Blob) {
  const res = await fetch(`/api${path}`, { method: "POST", credentials: "same-origin", headers: { "X-Workbench": "1", "Content-Type": blob.type || "application/octet-stream" }, body: blob });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.detail || `${res.status}`);
  return body;
}

function ConsentModal({ onStart, onClose }: { onStart: (title: string, system: boolean) => void; onClose: () => void }) {
  const [told, setTold] = useState(false);
  const [title, setTitle] = useState("");
  const [system, setSystem] = useState(true);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal card col" role="dialog" aria-modal="true" aria-labelledby="consent-title" onClick={(e) => e.stopPropagation()} style={{ gap: 14, maxWidth: 520 }}>
        <div className="row"><ShieldCheck size={18} /><h2 id="consent-title">Before you record</h2></div>
        <p className="small" style={{ margin: 0 }}>Tell everyone in the meeting that you're recording and taking notes, and give them the chance to say no. In many places it's the law to ask first.</p>
        <label className="field">Meeting name <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Weekly catch-up" autoFocus /></label>
        <label className="row small"><input type="checkbox" checked={system} onChange={(e) => setSystem(e.target.checked)} />
          Also record the computer's sound, so I hear the other people on a video call <span className="faint">(you'll be asked to share a screen or tab with sound)</span></label>
        <label className="row consent-check"><input type="checkbox" checked={told} onChange={(e) => setTold(e.target.checked)} /> <b>I've told everyone this meeting is being recorded</b></label>
        <p className="small faint" style={{ margin: 0 }}>The sound is turned into text as we go and then thrown away. Only the transcript and notes are kept, on this computer.</p>
        <div className="row"><div className="spacer" /><button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!told} onClick={() => onStart(title, system)}><Mic size={14} /> Start recording</button></div>
      </div>
    </div>
  );
}

function Recorder({ meeting, system, onDone }: { meeting: Meeting; system: boolean; onDone: (m: Meeting) => void }) {
  const [elapsed, setElapsed] = useState(0);
  const [lines, setLines] = useState<Seg[]>([]);
  const [pending, setPending] = useState(0);
  const [note, setNote] = useState("");
  const [stopping, setStopping] = useState(false);
  const toast = useToast();
  const live = useRef(true);
  const uploads = useRef<Promise<unknown>[]>([]);
  const stopFn = useRef<() => void>(() => undefined);

  useEffect(() => {
    let sources: Awaited<ReturnType<typeof openSources>> | null = null;
    let rec: MediaRecorder | null = null;
    let segStart = 0;
    const began = Date.now();
    const tick = setInterval(() => setElapsed((Date.now() - began) / 1000), 500);
    const mime = mimeType();

    const send = (blob: Blob, offset: number) => {
      if (!blob.size) return;
      setPending((n) => n + 1);
      const p = upload(`/meetings/${meeting.id}/chunk?offset=${offset.toFixed(2)}&name=chunk.${extFor(blob.type || mime)}`, blob)
        .then((r: { added: Seg[] }) => setLines((l) => [...l, ...(r.added || [])]))
        .catch((e) => toast.err(e))
        .finally(() => setPending((n) => n - 1));
      uploads.current.push(p);
    };
    // A new recorder every 30 seconds, so each piece is a complete file the speech model can read on its own
    const next = () => {
      if (!live.current || !sources) return;
      const chunks: Blob[] = [];
      const offset = segStart;
      rec = new MediaRecorder(sources.stream, mime ? { mimeType: mime } : undefined);
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      rec.onstop = () => { send(new Blob(chunks, { type: rec?.mimeType || mime }), offset); if (live.current) next(); };
      rec.start();
      setTimeout(() => { segStart = (Date.now() - began) / 1000; if (rec && rec.state !== "inactive") rec.stop(); }, CHUNK_MS);
    };
    stopFn.current = () => { live.current = false; if (rec && rec.state !== "inactive") rec.stop(); };

    openSources(system).then((s) => {
      sources = s;
      setNote(system && !s.system ? "Only your microphone is being recorded (the computer's sound wasn't shared). On a call, use speakers so I can hear the others too." : "");
      next();
    }).catch(() => { toast.err("Microphone access is off. Allow it in your system settings, then try again."); live.current = false; });

    return () => { live.current = false; clearInterval(tick); if (rec && rec.state !== "inactive") rec.stop(); setTimeout(() => sources?.close(), 300); };
  }, [meeting.id]);

  const stop = async () => {
    setStopping(true);
    stopFn.current();
    await new Promise((r) => setTimeout(r, 400));
    await Promise.allSettled(uploads.current);
    try { onDone(await api<Meeting>(`/meetings/${meeting.id}/stop`, { method: "POST" })); } catch (e) { toast.err(e); setStopping(false); }
  };

  return (
    <div className="col gap-lg">
      <div className="card recorder">
        <span className="rec-dot" aria-hidden="true" />
        <div className="grow">
          <div className="recorder-time">{clock(elapsed)}</div>
          <div className="small muted">Recording “{meeting.title}”. Everyone should know.{pending > 0 ? ` Writing down the last ${pending * 30} seconds…` : ""}</div>
        </div>
        <button className="btn primary" onClick={stop} disabled={stopping}>{stopping ? <Loader2 size={14} className="spin" /> : <Square size={13} />} Stop and write notes</button>
      </div>
      {note && <div className="alert" role="status">{note}</div>}
      <div className="card col transcript-live" aria-live="polite">
        <div className="label">Live transcript</div>
        {lines.length === 0 ? <div className="faint small">The first lines appear about half a minute in.</div>
          : lines.map((l, i) => <div key={i} className="seg"><span className="seg-time mono faint">{clock(l.start)}</span> {l.text}</div>)}
      </div>
      {toast.node}
    </div>
  );
}

function Detail({ m, onChange, onDelete }: { m: Meeting; onChange: (m: Meeting) => void; onDelete: () => void }) {
  const [busy, setBusy] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [project, setProject] = useState(m.project);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState(m.notes?.follow_up.subject ?? "");
  const [body, setBody] = useState(m.notes?.follow_up.body ?? "");
  const [draftSaved, setDraftSaved] = useState(false);
  const [showT, setShowT] = useState(!m.notes);
  const [model, setModel] = useState("");
  const { models } = useModels();
  const toast = useToast();
  const n = m.notes;

  useEffect(() => {
    setSubject(m.notes?.follow_up.subject ?? ""); setBody(m.notes?.follow_up.body ?? ""); setProject(m.project);
    setPicked(new Set((m.notes?.actions ?? []).map((a, i) => (a.task_id ? -1 : i)).filter((i) => i >= 0)));
    setDraftSaved(false);
  }, [m.id, m.notes]);

  const summarise = async () => {
    setBusy("notes");
    try { onChange(await api<Meeting>(`/meetings/${m.id}/summarise`, { body: { model: model || null } })); setShowT(false); } catch (e) { toast.err(e); }
    setBusy("");
  };
  const addTasks = async () => {
    setBusy("tasks");
    try {
      const r = await api<{ created: number[]; project: string }>(`/meetings/${m.id}/tasks`, { body: { indexes: [...picked], project } });
      onChange(await api<Meeting>(`/meetings/${m.id}`));
      toast.ok(r.created.length ? `Added ${r.created.length} to Tasks${r.project ? ` under ${r.project}` : ""}` : "Those are already in Tasks");
    } catch (e) { toast.err(e); }
    setBusy("");
  };
  const saveDraft = async () => {
    setBusy("draft");
    try {
      await api(`/meetings/${m.id}/draft`, { body: { to, subject, body } });
      setDraftSaved(true);
      toast.ok("Saved to your drafts. Nothing was sent.");
    } catch (e) { toast.err(e); }
    setBusy("");
  };
  const rename = async (title: string) => {
    if (title.trim() && title !== m.title) onChange(await api<Meeting>(`/meetings/${m.id}`, { method: "PATCH", body: { title } }));
  };

  return (
    <div className="col gap-lg">
      <div className="row wrap">
        <input className="input meeting-title grow" defaultValue={m.title} key={m.id} onBlur={(e) => rename(e.target.value)} aria-label="Meeting name" />
        <span className="faint small">{m.source === "recording" ? "Recorded" : m.source === "audio" ? "From a recording" : "From a transcript"} · {relTime(m.created_at)}{m.seconds ? ` · ${clock(m.seconds)}` : ""}</span>
        <button className="btn ghost sm icon danger" onClick={onDelete} aria-label="Delete meeting"><Trash2 size={14} /></button>
      </div>

      {!n ? (
        <div className="card col">
          <div className="small muted">Ready to write the notes: a summary, what was decided, and who's doing what by when.</div>
          <div className="row wrap"><div style={{ minWidth: 220 }}><ModelSelect value={model} onChange={setModel} models={models} /></div>
            <button className="btn primary" onClick={summarise} disabled={!!busy}>{busy === "notes" ? <Loader2 size={14} className="spin" /> : <NotebookPen size={14} />} Write the notes</button></div>
        </div>
      ) : (
        <>
          <section className="card col">
            <div className="row"><h2 className="grow">Summary</h2>
              <button className="btn ghost sm" onClick={() => navigator.clipboard.writeText(notesText(m.title, n)).then(() => toast.ok("Notes copied"))}><Copy size={13} /> Copy notes</button>
              <button className="btn ghost sm" onClick={summarise} disabled={!!busy}>{busy === "notes" ? <Loader2 size={13} className="spin" /> : null} Write again</button></div>
            <p style={{ margin: 0 }}>{n.summary}</p>
            {n.attendees.length > 0 && <div className="small muted row"><Users size={13} /> {n.attendees.join(", ")}</div>}
            {n.notes.length > 0 && <><div className="label">Notes</div><ul className="meeting-list">{n.notes.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
            {n.decisions.length > 0 && <><div className="label">Decisions</div><ul className="meeting-list decisions">{n.decisions.map((x, i) => <li key={i}><Check size={13} /> {x}</li>)}</ul></>}
          </section>

          <section className="card col">
            <div className="row"><CheckSquare size={16} /><h2 className="grow">Action items</h2></div>
            {n.actions.length === 0 ? <div className="small faint">No action items came up.</div> : (
              <>
                {n.actions.map((a, i) => (
                  <label key={i} className={`action-item ${a.task_id ? "added" : ""}`}>
                    {a.task_id ? <Check size={15} className="ok" aria-label="In Tasks" /> :
                      <input type="checkbox" checked={picked.has(i)} onChange={(e) => setPicked((p) => { const q = new Set(p); if (e.target.checked) q.add(i); else q.delete(i); return q; })} />}
                    <span className="grow"><span>{a.task}</span><span className="small muted action-meta">{actionMeta(a)}</span></span>
                  </label>
                ))}
                <div className="row wrap">
                  <input className="input" style={{ width: 200 }} placeholder="Project (optional)" value={project} onChange={(e) => setProject(e.target.value)} aria-label="Project" />
                  <button className="btn primary sm" onClick={addTasks} disabled={!!busy || picked.size === 0}>{busy === "tasks" ? <Loader2 size={13} className="spin" /> : <CheckSquare size={13} />} Add {picked.size || ""} to Tasks</button>
                  <Link className="small" to="/tasks">Open Tasks</Link>
                </div>
              </>
            )}
          </section>

          <section className="card col">
            <div className="row"><Mail size={16} /><h2 className="grow">Follow-up email</h2></div>
            <div className="small muted">I'll save this as a draft. I never send it: check it, then send it yourself from your mail.</div>
            <input className="input" placeholder={n.attendees.length ? `To (e.g. the emails for ${n.attendees.slice(0, 2).join(", ")})` : "To (email addresses)"} value={to} onChange={(e) => setTo(e.target.value)} aria-label="To" />
            <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Subject" />
            <textarea className="input" rows={9} value={body} onChange={(e) => { setBody(e.target.value); setDraftSaved(false); }} aria-label="Email" />
            <div className="row">
              <button className="btn primary sm" onClick={saveDraft} disabled={!!busy || !body.trim()}>{busy === "draft" ? <Loader2 size={13} className="spin" /> : <Mail size={13} />} Save as draft</button>
              {draftSaved && <span className="small" style={{ color: "var(--ok-text)" }}><Check size={13} /> Saved in Mail → Drafts (and your Gmail or Outlook drafts if connected). Not sent.</span>}
              {draftSaved && <Link className="small" to="/mail">Open Mail</Link>}
            </div>
          </section>
        </>
      )}

      <section className="card col">
        <button className="row nav-toggle" style={{ background: "none", border: 0, padding: 0, cursor: "pointer" }} onClick={() => setShowT((s) => !s)} aria-expanded={showT}>
          <h2 className="grow" style={{ textAlign: "left" }}>Transcript</h2><span className="small muted">{showT ? "Hide" : "Show"}</span>
        </button>
        {showT && <div className="transcript">{(m.segments ?? []).map((s, i) => (
          <div key={i} className="seg"><span className="seg-time mono faint">{clock(s.start)}</span> {s.speaker && <b>{s.speaker}: </b>}{s.text}</div>
        ))}</div>}
      </section>
      {toast.node}
    </div>
  );
}

export default function Meetings() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [list, setList] = useState<Meeting[] | null>(null);
  const [locked, setLocked] = useState("");
  const [current, setCurrent] = useState<Meeting | null>(null);
  const [consent, setConsent] = useState(false);
  const [recording, setRecording] = useState<{ m: Meeting; system: boolean } | null>(null);
  const [importing, setImporting] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const mid = id ? Number(id) : null;

  const load = () => api<Meeting[]>("/meetings").then(setList).catch((e) => { if (e instanceof ApiError && e.status === 402) setLocked(e.message); else toast.err(e); setList([]); });
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!mid) return setCurrent(null);
    api<Meeting>(`/meetings/${mid}`).then(setCurrent).catch(toast.err);
  }, [mid]);

  const start = async (title: string, system: boolean) => {
    setConsent(false);
    try {
      const m = await api<Meeting>("/meetings", { body: { title, consent: true } });
      setRecording({ m, system });
      navigate(`/meetings/${m.id}`);
      load();
    } catch (e) { toast.err(e); }
  };
  const onImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setImporting(true);
    try {
      const m = await upload(`/meetings/import?name=${encodeURIComponent(f.name)}`, f) as Meeting;
      await load();
      navigate(`/meetings/${m.id}`);
      toast.ok("Brought in. Now write the notes.");
    } catch (err) { toast.err(err); }
    setImporting(false);
  };
  const remove = async () => {
    if (!current || !confirm(`Delete “${current.title}” and its notes? Tasks already added stay in Tasks.`)) return;
    await api(`/meetings/${current.id}`, { method: "DELETE" });
    navigate("/meetings");
    load();
  };

  if (locked) {
    return (
      <div className="page"><div className="page-head"><div><h1>Meetings</h1></div></div>
        <Empty icon={<Users size={22} />} title="The meeting companion is part of Pro" hint="Record or bring in a meeting and get notes, decisions, action items and a follow-up email draft." /></div>
    );
  }

  const isRecording = recording && recording.m.id === mid;
  return (
    <div className={`split ${mid !== null ? "has-selection" : ""}`}>
      <div className="split-list">
        <div className="col" style={{ padding: "14px 12px 10px", gap: 10 }}>
          <div className="row"><h2 className="grow">Meetings</h2></div>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn primary sm grow" onClick={() => setConsent(true)} disabled={!!recording}><Circle size={12} fill="currentColor" /> Record</button>
            <button className="btn sm grow" onClick={() => file.current?.click()} disabled={importing}>{importing ? <Loader2 size={13} className="spin" /> : <FileUp size={13} />} Bring in a file</button>
            <input ref={file} type="file" hidden accept=".vtt,.srt,.txt,.md,.json,.docx,audio/*,video/*,.m4a,.mp3,.wav,.webm,.mp4" onChange={onImport} />
          </div>
        </div>
        <div className="scroll">
          {list === null && <SkeletonList rows={4} />}
          {list?.length === 0 && <Empty icon={<Users size={22} />} title="No meetings yet" hint="Record one, or bring in a recording or a transcript from Zoom, Teams or Meet." />}
          {list?.map((m) => (
            <div key={m.id} className={`list-item ${m.id === mid ? "active" : ""}`} role="link" tabIndex={0} onClick={() => navigate(`/meetings/${m.id}`)} onKeyDown={(e) => { if (e.key === "Enter") navigate(`/meetings/${m.id}`); }}>
              <div className="grow">
                <div className="truncate" style={{ fontWeight: m.id === mid ? 550 : 450 }}>{m.status === "recording" && <span className="rec-dot sm" aria-label="Recording" />} {m.title}</div>
                <div className="faint small truncate">{relTime(m.created_at)} · {m.status === "summarised" ? "Notes ready" : m.status === "recording" ? "Recording" : "Needs notes"}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="split-main">
        <div className="toolbar">
          <button className="btn ghost sm mobile-only" onClick={() => navigate("/meetings")} aria-label="Back to meetings"><ArrowLeft size={14} /></button>
          <div className="toolbar-title truncate">{current?.title ?? "Meetings"}</div>
        </div>
        <div className="scroll" style={{ padding: "24px 28px 64px" }}>
          <div style={{ maxWidth: 820, margin: "0 auto" }}>
            {isRecording ? <Recorder meeting={recording.m} system={recording.system} onDone={(m) => { setRecording(null); setCurrent(m); load(); }} />
              : current ? <Detail m={current} onChange={(m) => { setCurrent(m); load(); }} onDelete={remove} />
              : <Empty hero icon={<Users size={24} />} title="Your meeting companion" hint="Record a meeting or bring in a file. I'll write the notes, list who's doing what by when, add those to Tasks, and draft the follow-up email for you to send.">
                  <div className="row" style={{ justifyContent: "center", gap: 8 }}>
                    <button className="btn primary" onClick={() => setConsent(true)}><Mic size={14} /> Record a meeting</button>
                    <button className="btn" onClick={() => file.current?.click()}><FileUp size={14} /> Bring in a file</button>
                  </div>
                </Empty>}
          </div>
        </div>
      </div>
      {consent && <ConsentModal onStart={start} onClose={() => setConsent(false)} />}
      {toast.node}
    </div>
  );
}
