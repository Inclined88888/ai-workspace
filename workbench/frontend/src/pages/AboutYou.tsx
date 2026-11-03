import { Brain, Check, Pencil, Pin, PinOff, Plus, Search, Trash2, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { Fact, groupFacts, sourceLabel } from "../learn/text";
import "../learn/learn.css";

type Settings = { memory: boolean; memory_in_chat: boolean; notice_patterns: boolean; suggest: boolean };
type Work = { kind: string; label: string; times: number };
type Legacy = { id: number; content: string; kind: string; source: string; updated_at: string };
type Data = { facts: Fact[]; categories: Record<string, string>; legacy: Legacy[]; work: Work[] };
type Lesson = { id: number; lesson: string; created_at: string };

const SWITCHES: { key: keyof Settings; label: string; hint: string }[] = [
  { key: "memory", label: "Learn about me as we work", hint: "From chats, files, tasks and things you show me. Kept on this computer, encrypted." },
  { key: "memory_in_chat", label: "Use what I know in answers", hint: "Off means I answer without your notes, even though they stay saved." },
  { key: "notice_patterns", label: "Notice my routines", hint: "Spot work you repeat, like a Monday report." },
  { key: "suggest", label: "Offer to take things over", hint: "Suggest one-click routines. Nothing goes out without your OK." },
];

/** "What I know about you": everything the assistant has learned, in plain words, editable and deletable. */
export default function AboutYou() {
  const [data, setData] = useState<Data | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ content: "", category: "fact" });
  const [editing, setEditing] = useState<{ id: number; content: string } | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const toast = useToast();

  const load = useCallback((query = q) => {
    api<Data>(`/learn/facts${query.trim() ? `?q=${encodeURIComponent(query)}` : ""}`).then(setData).catch(toast.err);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    load("");
    api<Settings>("/learn/settings").then(setSettings).catch(() => {});
    api<Lesson[]>("/learn/lessons").then(setLessons).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => (data ? groupFacts(data.facts, data.categories) : []), [data]);

  const flip = async (key: keyof Settings) => {
    if (!settings) return;
    try { setSettings(await api<Settings>("/learn/settings", { method: "PUT", body: { [key]: !settings[key] } })); } catch (e) { toast.err(e); }
  };
  const add = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/learn/facts", { body: draft }); setDraft({ content: "", category: draft.category }); setAdding(false); toast.ok("Saved"); load(); } catch (e2) { toast.err(e2); }
  };
  const save = async () => {
    if (!editing) return;
    try { await api(`/learn/facts/${editing.id}`, { method: "PUT", body: { content: editing.content } }); setEditing(null); load(); } catch (e) { toast.err(e); }
  };
  const pin = async (f: Fact) => { try { await api(`/learn/facts/${f.id}`, { method: "PUT", body: { pinned: !f.pinned } }); load(); } catch (e) { toast.err(e); } };
  const del = async (f: Fact) => { try { await api(`/learn/facts/${f.id}`, { method: "DELETE" }); toast.ok("Forgotten"); load(); } catch (e) { toast.err(e); } };
  const delLegacy = async (m: Legacy) => { try { await api(`/learn/legacy-memories/${m.id}`, { method: "DELETE" }); load(); } catch (e) { toast.err(e); } };
  const forgetAll = async () => {
    try { await api("/learn/facts/forget-all", { body: {} }); setConfirmAll(false); toast.ok("Everything I learned is gone"); load(); } catch (e) { toast.err(e); }
  };

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      {toast.node}
      <div className="page-head">
        <div className="grow">
          <p className="kicker">Your assistant</p>
          <h1>What I know about you</h1>
          <p className="muted">I pick these up as we work so I don't ask twice. Change anything that's wrong, or remove it.</p>
        </div>
        <button className="btn" onClick={() => setAdding((v) => !v)}><Plus size={14} /> Tell me something</button>
      </div>

      <div className="learn-grid">
        <div>
          {adding && (
            <form className="learn-hero" onSubmit={add}>
              <h2>Something I should know</h2>
              <textarea className="input" rows={2} autoFocus value={draft.content} placeholder="e.g. I'm a solicitor in Brisbane and I bill in 6-minute units"
                onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
              <div className="row wrap" style={{ marginTop: 12 }}>
                <select className="input" style={{ width: "auto" }} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} aria-label="Kind">
                  {Object.entries(data?.categories || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <button className="btn primary" disabled={draft.content.trim().length < 4}>Save</button>
                <button type="button" className="btn ghost" onClick={() => setAdding(false)}>Cancel</button>
              </div>
            </form>
          )}

          <form className="row" style={{ marginBottom: 24 }} onSubmit={(e) => { e.preventDefault(); load(); }}>
            <Search size={16} className="muted" aria-hidden="true" />
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search what I know" aria-label="Search what I know" />
            {q && <button type="button" className="btn ghost sm" onClick={() => { setQ(""); load(""); }}>Clear</button>}
          </form>

          {data && !groups.length && !data.legacy.length && (
            <Empty icon={<Brain size={22} />} title={q ? "Nothing matches that" : "Nothing yet"}
              hint={q ? "Try another word." : "Chat, share a file or show me a task, and what I learn will appear here."} />
          )}

          {groups.map((g) => (
            <section key={g.key} className="learn-section">
              <h2>{g.label}</h2>
              <ul className="learn-list">
                {g.facts.map((f) => (
                  <li key={f.id} className="learn-item">
                    <div className="grow">
                      {editing?.id === f.id ? (
                        <div className="row">
                          <input className="input" autoFocus value={editing.content} aria-label="Edit"
                            onChange={(e) => setEditing({ id: f.id, content: e.target.value })} onKeyDown={(e) => e.key === "Enter" && save()} />
                          <button className="btn sm" onClick={save} aria-label="Save"><Check size={14} /></button>
                          <button className="btn ghost sm" onClick={() => setEditing(null)} aria-label="Cancel"><X size={14} /></button>
                        </div>
                      ) : <p>{f.pinned && <Pin size={12} className="learn-pin" aria-label="Always used" />} {f.content}</p>}
                      <div className="learn-meta">
                        {sourceLabel(f.source)}{f.evidence ? ` · “${f.evidence.slice(0, 80)}”` : ""} · {relTime(f.updated_at)}{f.uses ? ` · used ${f.uses}×` : ""}
                      </div>
                    </div>
                    <div className="learn-actions">
                      <button className="btn ghost sm" title={f.pinned ? "Only use when relevant" : "Always keep in mind"} aria-label={f.pinned ? "Unpin" : "Pin"} onClick={() => pin(f)}>
                        {f.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                      </button>
                      <button className="btn ghost sm" aria-label="Edit" onClick={() => setEditing({ id: f.id, content: f.content })}><Pencil size={14} /></button>
                      <button className="btn ghost sm" aria-label="Forget" onClick={() => del(f)}><Trash2 size={14} /></button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          ))}

          {!!data?.legacy.length && (
            <section className="learn-section">
              <h2>Things you asked me to remember</h2>
              <ul className="learn-list">
                {data.legacy.map((m) => (
                  <li key={m.id} className="learn-item">
                    <div className="grow"><p>{m.content}</p><div className="learn-meta">{relTime(m.updated_at)}</div></div>
                    <div className="learn-actions"><button className="btn ghost sm" aria-label="Forget" onClick={() => delLegacy(m)}><Trash2 size={14} /></button></div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {!!data?.facts.length && (
            <div className="row" style={{ marginTop: 8 }}>
              {confirmAll ? (
                <>
                  <span className="muted">Forget everything I've learned about you? This can't be undone.</span>
                  <button className="btn danger-solid sm" onClick={forgetAll}>Yes, forget it all</button>
                  <button className="btn ghost sm" onClick={() => setConfirmAll(false)}>Keep</button>
                </>
              ) : <button className="btn ghost sm" onClick={() => setConfirmAll(true)}>Forget everything</button>}
            </div>
          )}
        </div>

        <aside className="learn-aside">
          <h3>Learning</h3>
          <div className="learn-switches" style={{ marginBottom: 28 }}>
            {SWITCHES.map((s) => (
              <label key={s.key} className="learn-switch">
                <input type="checkbox" checked={!!settings?.[s.key]} disabled={!settings} onChange={() => flip(s.key)} />
                <span>{s.label}<small>{s.hint}</small></span>
              </label>
            ))}
          </div>
          {!!data?.work.length && (
            <div style={{ marginBottom: 28 }}>
              <h3>The work I've seen</h3>
              {data.work.slice(0, 8).map((w) => (
                <div key={w.kind} className="learn-work"><span>{w.label.charAt(0).toUpperCase() + w.label.slice(1)}</span><span>{w.times}×</span></div>
              ))}
            </div>
          )}
          {!!lessons.length && (
            <div>
              <h3>Learned from your changes</h3>
              {lessons.slice(0, 6).map((l) => <p key={l.id} className="learn-meta" style={{ fontSize: "var(--text-sm, 14px)", color: "var(--text)" }}>{l.lesson}</p>)}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
