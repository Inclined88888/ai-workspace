// feat/v02-core: the chat's project switcher, the "Check this" toggle, and the checked-answer panel under a reply.
import { BadgeCheck, ChevronDown, FolderKanban, Loader2, ShieldCheck, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { isUpgrade, ProjectList, UpgradeCard } from "./shared";
import "./v02.css";

/** Which project this chat is in (or, for a new chat, which project new chats use). Pick another or none. */
export function ProjectSwitcher({ cid }: { cid: number | null }) {
  const navigate = useNavigate();
  const [list, setList] = useState<ProjectList | null>(null);
  const [current, setCurrent] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const load = async () => {
    const l = await api<ProjectList>("/projects").catch(() => null);
    setList(l);
    if (cid) setCurrent((await api<{ project: { id: number } | null }>(`/projects/for-conversation/${cid}`).catch(() => ({ project: null }))).project?.id ?? null);
    else setCurrent(l?.active_id ?? null);
  };
  useEffect(() => { load(); }, [cid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const pick = async (pid: number | null) => {
    setOpen(false);
    if (cid) await api(`/projects/for-conversation/${cid}`, { method: "PUT", body: { project_id: pid } });
    else await api("/projects/active", { method: "PUT", body: { project_id: pid } });
    if (cid && pid === null && current) await api(`/projects/${current}/dismiss`, { body: { kind: "chat", ref: String(cid) } }).catch(() => undefined);
    load();
  };
  if (!list) return null;
  const name = list.projects.find((p) => p.id === current)?.name;
  return (
    <div className="v02-switch" ref={ref}>
      <button className={`btn ghost sm ${name ? "v02-switch-on" : ""}`} onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open}
        title={cid ? "Project this chat belongs to" : "Project new chats use"}>
        <FolderKanban size={14} /> <span className="truncate">{name ?? "No project"}</span> <ChevronDown size={12} />
      </button>
      {open && (
        <div className="v02-menu card" role="listbox">
          {list.projects.map((p) => <button key={p.id} role="option" aria-selected={p.id === current} className={`v02-menu-item ${p.id === current ? "on" : ""}`} onClick={() => pick(p.id)}>{p.name}</button>)}
          <button role="option" aria-selected={current === null} className="v02-menu-item" onClick={() => pick(null)}>No project</button>
          <button className="v02-menu-item faint" onClick={() => navigate("/projects")}>Manage projects…</button>
        </div>
      )}
    </div>
  );
}

const MODE_EVENT = "v02:check-mode";

/** "Check this" mode for the chat: answers are cited and every claim is re-checked when it arrives. */
export function CheckToggle({ cid }: { cid: number | null }) {
  const [on, setOn] = useState(false);
  const [upgrade, setUpgrade] = useState(false);
  const pending = useRef(false);
  useEffect(() => {
    if (cid && pending.current) {  // switched on before the chat existed: carry it over
      pending.current = false;
      api("/checked/mode", { method: "PUT", body: { conversation_id: cid, on: true } })
        .then(() => window.dispatchEvent(new CustomEvent(MODE_EVENT, { detail: true }))).catch(() => undefined);
      return;
    }
    api<{ on: boolean }>(`/checked/mode${cid ? `?conversation_id=${cid}` : ""}`).then((m) => { setOn(m.on); window.dispatchEvent(new CustomEvent(MODE_EVENT, { detail: m.on })); }).catch(() => undefined);
  }, [cid]);
  const flip = async () => {
    const next = !on;
    try {
      if (cid) await api("/checked/mode", { method: "PUT", body: { conversation_id: cid, on: next } });
      else if (next) { await api("/checked/mode"); pending.current = true; }
      setOn(next);
      window.dispatchEvent(new CustomEvent(MODE_EVENT, { detail: next }));
    } catch (e) { if (isUpgrade(e)) setUpgrade(true); }
  };
  return (
    <>
      <button className={`btn sm v02-check-toggle ${on ? "on" : "ghost"}`} aria-pressed={on} onClick={flip} title="Cite a source for every claim and check it again">
        <ShieldCheck size={14} /> Check answers{on ? ": on" : ""}
      </button>
      {upgrade && <div className="v02-float"><UpgradeCard feature="checked_answers" onClose={() => setUpgrade(false)} /></div>}
    </>
  );
}

type Claim = { text: string; verdict: "supported" | "partly" | "unsupported" | "contradicted"; label: string; source: string | null; quote: string; note: string; downgraded: boolean };
type Source = { id: string; kind: string; label: string; url: string; excerpt: string };
type Result = { summary: string; counts: Record<string, number>; claims: Claim[]; sources: Source[]; segments: { text: string; claim?: number }[] };

/** Under one reply: a "Check this" button, or the check itself when check mode is on for a fresh reply. */
export function CheckThis({ cid, content, fresh }: { cid: number | null; content: string; fresh: boolean }) {
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [upgrade, setUpgrade] = useState(false);
  const [focus, setFocus] = useState<number | null>(null);
  const started = useRef(false);
  const run = async () => {
    if (!cid || busy) return;
    setBusy(true); setErr("");
    try { setRes(await api<Result>("/checked/check", { body: { conversation_id: cid, content } })); }
    catch (e) { if (isUpgrade(e)) setUpgrade(true); else setErr(String((e as Error).message || e)); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    if (!fresh) return;
    const go = (on: boolean) => { if (on && !started.current && cid) { started.current = true; run(); } };
    const h = (e: Event) => go(Boolean((e as CustomEvent).detail));
    window.addEventListener(MODE_EVENT, h);
    if (cid) api<{ on: boolean }>(`/checked/mode?conversation_id=${cid}`).then((m) => go(m.on)).catch(() => undefined);
    return () => window.removeEventListener(MODE_EVENT, h);
  }, [cid, fresh]); // eslint-disable-line react-hooks/exhaustive-deps

  if (upgrade) return <UpgradeCard feature="checked_answers" onClose={() => setUpgrade(false)} />;
  if (!res) return (
    <div className="v02-checkline">
      <button className="btn ghost sm" disabled={!cid || busy} onClick={run}>{busy ? <Loader2 size={13} className="spin" /> : <BadgeCheck size={13} />} {busy ? "Checking every claim…" : "Check this"}</button>
      {err && <span className="small v02-late">{err}</span>}
    </div>
  );
  const src = (id: string | null) => res.sources.find((s) => s.id === id);
  const num = (id: string | null) => (id ? Number(id.slice(1)) : 0);
  return (
    <div className="v02-checked card" aria-label="Checked answer">
      <div className="row">
        <ShieldCheck size={15} aria-hidden="true" />
        <b className="grow">{res.summary}</b>
        <button className="btn ghost sm icon" aria-label="Close check" onClick={() => setRes(null)}><X size={13} /></button>
      </div>
      <div className="v02-checked-text">
        {res.segments.map((s, i) => {
          if (s.claim === undefined) return <span key={i}>{s.text}</span>;
          const c = res.claims[s.claim];
          return (
            <span key={i} className={`v02-claim ${c.verdict} ${focus === s.claim ? "focus" : ""}`} tabIndex={0} onClick={() => setFocus(s.claim!)} onFocus={() => setFocus(s.claim!)}
              title={c.label}>
              {c.verdict === "unsupported" || c.verdict === "contradicted" ? <mark>{s.text}</mark> : s.text}
              {c.source && c.verdict !== "unsupported" ? <sup className="v02-cite">{num(c.source)}</sup> : <sup className="v02-flag">{c.verdict === "contradicted" ? "✕" : "?"}</sup>}
            </span>
          );
        })}
      </div>
      {focus !== null && res.claims[focus] && (() => {
        const c = res.claims[focus], s = src(c.source);
        return (
          <div className={`v02-claim-detail ${c.verdict}`}>
            <div className="small"><b>{c.label}.</b> {c.note}</div>
            {c.quote && <blockquote className="small">“{c.quote}”</blockquote>}
            {s && <div className="small muted">Source {num(s.id)}: {s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a> : s.label}</div>}
          </div>
        );
      })()}
      {res.sources.length > 0 && (
        <details className="small">
          <summary>{res.sources.length} source{res.sources.length > 1 ? "s" : ""}</summary>
          <ol className="v02-sources">
            {res.sources.map((s) => <li key={s.id}><span className="badge">{s.kind}</span> {s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a> : s.label}</li>)}
          </ol>
        </details>
      )}
    </div>
  );
}
