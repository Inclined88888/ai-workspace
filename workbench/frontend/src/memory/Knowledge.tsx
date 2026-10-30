// feat/memory-core: Memory - people, project knowledge, what I know (with sources, scopes and why), decisions, tidy-up.
import { Brain, Check, FolderKanban, HelpCircle, Lock, Pin, PinOff, Search, Sparkles, Trash2, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import { FiledChip } from "./FiledChip";
import { Conflict, Entity, EntityDetail, Filing, Mem, SCOPE_LABEL, TIER_LABEL, Tidy, Why } from "./types";
import "./memory.css";

type Tab = "people" | "projects" | "memories" | "decide" | "tidy";
const TABS: { id: Tab; label: string }[] = [
  { id: "memories", label: "What I know" }, { id: "people", label: "People" }, { id: "projects", label: "Projects" },
  { id: "decide", label: "Needs your call" }, { id: "tidy", label: "Tidy up" },
];

export default function Knowledge() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "memories";
  const entityId = params.get("entity");
  const [tidy, setTidy] = useState<Tidy | null>(null);
  const toast = useToast();
  const loadTidy = useCallback(() => api<Tidy>("/knowledge/tidy").then(setTidy).catch(() => {}), []);
  useEffect(() => { loadTidy(); }, [loadTidy]);
  const go = (t: Tab) => setParams({ tab: t });
  const counts: Partial<Record<Tab, number>> = { decide: tidy?.conflicts.length, tidy: tidy?.duplicates.length };

  return (
    <div className="page kn">
      {toast.node}
      <div className="page-head">
        <div>
          <div className="v02-kicker">Memory</div>
          <h1>What I know</h1>
          <p className="muted">Everything I remember, who it's about, where it came from and who can see it. Change or forget anything. Sensitive things never leave this computer.</p>
        </div>
      </div>
      {entityId ? <EntityPage id={Number(entityId)} onBack={() => setParams({ tab })} toast={toast} /> : <>
        <div className="kn-tabs" role="tablist">
          {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => go(t.id)}>{t.label}{counts[t.id] ? <span className="count">{counts[t.id]}</span> : null}</button>)}
        </div>
        {tab === "memories" && <Memories toast={toast} />}
        {tab === "people" && <Entities type="person" icon={<UserRound size={16} />} empty="No people yet. I add people as they come up in chats, mail, meetings and projects." />}
        {tab === "projects" && <Entities type="project" icon={<FolderKanban size={16} />} empty="No projects yet. Make one on the Projects page and I'll gather what belongs to it." />}
        {tab === "decide" && <Decide items={tidy?.conflicts ?? []} onDone={loadTidy} />}
        {tab === "tidy" && <TidyUp tidy={tidy} onDone={loadTidy} toast={toast} />}
      </>}
    </div>
  );
}

function Memories({ toast }: { toast: ReturnType<typeof useToast> }) {
  const [mems, setMems] = useState<Mem[] | null>(null);
  const [q, setQ] = useState("");
  const [why, setWhy] = useState<Record<number, Why>>({});
  const load = useCallback(() => api<Mem[]>("/knowledge/memories").then(setMems).catch(toast.err), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const shown = useMemo(() => (mems ?? []).filter((m) => !q.trim() || m.content.toLowerCase().includes(q.toLowerCase())), [mems, q]);
  const groups = useMemo(() => Object.keys(TIER_LABEL).map((t) => [t, shown.filter((m) => m.tier === t)] as const).filter(([, l]) => l.length), [shown]);
  const patch = async (m: Mem, body: Partial<Mem>) => { await api(`/knowledge/memories/${m.id}`, { method: "PATCH", body }); load(); };
  const forget = async (m: Mem) => { await api(`/knowledge/memories/${m.id}`, { method: "DELETE" }); toast.ok?.("Forgotten"); load(); };
  const explain = async (m: Mem) => {
    if (why[m.id]) { const n = { ...why }; delete n[m.id]; setWhy(n); return; }
    setWhy({ ...why, [m.id]: await api<Why>(`/knowledge/memories/${m.id}/why`) });
  };
  if (!mems) return <div className="muted">Loading…</div>;
  if (!mems.length) return <Empty icon={<Brain size={22} />} title="Nothing remembered yet" hint="As we work I'll keep the useful bits here, and you'll see exactly where each one came from." />;
  return (
    <div>
      <div className="row gap" style={{ marginBottom: 12 }}>
        <div className="input row" style={{ flex: 1, alignItems: "center", gap: 8 }}><Search size={15} /><input style={{ border: 0, outline: 0, flex: 1, background: "transparent", font: "inherit" }} placeholder="Find a memory" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      {groups.map(([tier, list]) => (
        <section key={tier} className="kn-section kn-enter">
          <h3>{TIER_LABEL[tier]}</h3>
          {list.map((m) => (
            <div key={m.id} className="kn-row">
              <div className="grow">
                <div>{m.content}</div>
                <div className="kn-sub" style={{ marginTop: 4 }}>
                  {m.pinned && <span className="kn-tag pinned">Pinned</span>}
                  {m.privacy === "sensitive" && <span className="kn-tag sensitive"><Lock size={10} /> Sensitive, never sent to online models</span>}
                  <span className="kn-tag">{SCOPE_LABEL(m.scope)}</span>
                  <span>{m.source_kind === "user" ? "You told me" : "I picked this up"} {relTime(m.observed_at)}</span>
                </div>
                {why[m.id] && <div className="kn-why kn-enter">{why[m.id].summary}{why[m.id].replaced.length ? ` It replaced: “${why[m.id].replaced[0].content}”.` : ""}</div>}
              </div>
              <button className="btn ghost sm" title="Why do you know this?" aria-label="Why do you know this?" onClick={() => explain(m)}><HelpCircle size={15} /></button>
              <button className="btn ghost sm" title={m.pinned ? "Unpin" : "Pin, always keep in mind"} aria-label={m.pinned ? "Unpin" : "Pin"} onClick={() => patch(m, { pinned: !m.pinned })}>{m.pinned ? <PinOff size={15} /> : <Pin size={15} />}</button>
              <button className="btn ghost sm" title={m.privacy === "sensitive" ? "Mark as normal" : "Mark sensitive"} aria-label="Toggle sensitive" onClick={() => patch(m, { privacy: m.privacy === "sensitive" ? "normal" : "sensitive" })}><Lock size={15} /></button>
              <button className="btn ghost sm" title="Forget this" aria-label="Forget this" onClick={() => forget(m)}><Trash2 size={15} /></button>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function Entities({ type, icon, empty }: { type: string; icon: React.ReactNode; empty: string }) {
  const [list, setList] = useState<Entity[] | null>(null);
  const [, setParams] = useSearchParams();
  useEffect(() => { api<Entity[]>(`/knowledge/entities?type=${type}`).then(setList).catch(() => setList([])); }, [type]);
  if (!list) return <div className="muted">Loading…</div>;
  if (!list.length) return <Empty icon={icon} title="Nothing here yet" hint={empty} />;
  return (
    <div className="kn-grid">
      {list.map((e) => (
        <button key={e.id} className="kn-card kn-enter" onClick={() => setParams({ entity: String(e.id), tab: type === "person" ? "people" : "projects" })}>
          <h4>{e.name}</h4>
          <div className="kn-sub">{[e.attrs.role, e.attrs.company, e.attrs.email, e.attrs.kind, e.attrs.status].filter(Boolean).join(" · ") || (type === "person" ? "Person" : "Project")}</div>
        </button>
      ))}
    </div>
  );
}

function EntityPage({ id, onBack, toast }: { id: number; onBack: () => void; toast: ReturnType<typeof useToast> }) {
  const [e, setE] = useState<EntityDetail | null>(null);
  const navigate = useNavigate();
  useEffect(() => { api<EntityDetail>(`/knowledge/entities/${id}`).then(setE).catch(toast.err); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!e) return <div className="muted">Loading…</div>;
  const byType = e.links.reduce<Record<string, Entity[]>>((a, l) => ((a[l.type] ??= []).push(l), a), {});
  const LABEL: Record<string, string> = { person: "People", project: "Projects", document: "Files", task: "Tasks", event: "Meetings and events", site: "Sites", thread: "Chats", organisation: "Organisations", place: "Places" };
  return (
    <div className="kn-enter">
      <button className="btn ghost sm" onClick={onBack}>← Back</button>
      <div className="kn-detail" style={{ marginTop: 10 }}>
        <div className="kn-sub">{e.type === "person" ? "Person" : e.type === "project" ? "Project knowledge" : e.type}</div>
        <h2 style={{ margin: "2px 0 6px" }}>{e.name}</h2>
        <div className="kn-sub">{Object.entries(e.attrs).filter(([k, v]) => v && !["keywords"].includes(k)).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
        {e.ref?.startsWith("projects:") && <button className="btn sm" style={{ marginTop: 10 }} onClick={() => navigate(`/projects/${e.ref!.split(":")[1]}`)}>Open project</button>}
      </div>
      <section className="kn-section">
        <h3>What I know</h3>
        {e.memories.length ? e.memories.map((m) => <div key={m.id} className="kn-row"><div className="grow">{m.content}<div className="kn-sub">{m.source_kind === "user" ? "You told me" : "Picked up"} {relTime(m.observed_at)}</div></div></div>)
          : <div className="muted">Nothing yet.</div>}
      </section>
      {Object.entries(byType).map(([t, list]) => (
        <section key={t} className="kn-section">
          <h3>{LABEL[t] ?? t}</h3>
          <div className="kn-grid">{list.map((l) => <div key={l.id} className="kn-card" style={{ cursor: "default" }}><h4>{l.name}</h4><div className="kn-sub">{l.rel?.replace("_", " ")}</div></div>)}</div>
        </section>
      ))}
    </div>
  );
}

function Decide({ items, onDone }: { items: Conflict[]; onDone: () => void }) {
  if (!items.length) return <Empty icon={<Check size={22} />} title="Nothing to decide" hint="When two things I've learned disagree, I'll ask you here instead of guessing." />;
  const pick = async (c: Conflict, keep: "old" | "new" | "both") => { await api(`/knowledge/conflicts/${c.id}/${keep}`, { body: {} }); onDone(); };
  return <div>{items.map((c) => (
    <div key={c.id} className="kn-conflict kn-enter">
      <b>{c.question}</b>
      <div className="pair">
        <div><div className="kn-sub">Earlier, {relTime(c.old.observed_at)}</div>{c.old.content}</div>
        <div><div className="kn-sub">Newer, {relTime(c.new.observed_at)}</div>{c.new.content}</div>
      </div>
      <div className="row gap">
        <button className="btn" onClick={() => pick(c, "old")}>Keep earlier</button>
        <button className="btn primary" onClick={() => pick(c, "new")}>Keep newer</button>
        <button className="btn ghost" onClick={() => pick(c, "both")}>Both are true</button>
      </div>
    </div>))}</div>;
}

function TidyUp({ tidy, onDone, toast }: { tidy: Tidy | null; onDone: () => void; toast: ReturnType<typeof useToast> }) {
  const [filings, setFilings] = useState<Filing[]>([]);
  useEffect(() => { api<Filing[]>("/knowledge/filings").then(setFilings).catch(() => {}); }, []);
  const merge = async (a: Entity, b: Entity) => { await api(`/knowledge/merge/${a.id}/${b.id}`, { body: {} }); toast.ok?.(`Merged into ${a.name}`); onDone(); };
  const dups = tidy?.duplicates ?? [];
  return (
    <div>
      <section className="kn-section">
        <h3>Looks like the same thing</h3>
        {dups.length ? dups.map((d) => (
          <div key={`${d.a.id}-${d.b.id}`} className="kn-row kn-enter">
            <Sparkles size={16} style={{ color: "var(--accent-text)", marginTop: 3 }} />
            <div className="grow">{d.why}</div>
            <button className="btn sm primary" onClick={() => merge(d.a, d.b)}>Merge</button>
            <button className="btn sm ghost" aria-label="Not the same" onClick={() => onDone()}><X size={14} /></button>
          </div>)) : <div className="muted">Nothing to merge.</div>}
      </section>
      <section className="kn-section">
        <h3>Recently filed for you</h3>
        {filings.length ? filings.map((f) => (
          <div key={f.id} className="kn-row kn-enter"><div className="grow"><span className="kn-tag">{f.item_kind}</span>{f.reason && <span className="kn-sub">because it {f.reason}</span>}</div>
            <FiledChip kind={f.item_kind} itemRef={f.item_ref} initial={f} /></div>)) : <div className="muted">Nothing filed yet.</div>}
      </section>
    </div>
  );
}
