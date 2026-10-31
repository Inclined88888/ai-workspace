// Legal pack: clause library, compare/redline, defined terms, cross-references, citations, matter timeline (feat/v02-packs).
// Every result carries the not-legal-advice notice and its sources.
import { useCallback, useEffect, useState } from "react";
import { Copy, Trash2 } from "lucide-react";
import { api } from "../api";
import { FilePick, NotAdvice, PackGate, copy, download, useFiles } from "../packs/shared";
import { Markdown, useToast } from "../util";
import "../packs/packs.css";

type Term = { term: string; defined_at?: string; first_used?: string; uses?: number };
type Check = {
  document: string; summary: string; not_legal_advice: string;
  defined_terms: { defined: Term[]; used_not_defined: Term[]; defined_not_used: Term[]; note: string };
  cross_references: { checked: number; broken: { reference: string; where: string; problem: string }[] };
  citations: { citations: { kind: string; citation: string; where: string; problems: string[]; source: string }[]; note: string };
};
type Clause = { id: number; title: string; body: string; tags: string; uses: number; source: string };
type Event = { date: string; event: string; as_written: string; source: string; ambiguous: boolean };
type Tab = "check" | "compare" | "timeline" | "clauses";

export default function PackLegal() {
  const [tab, setTab] = useState<Tab>("check");
  const [gate, setGate] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const toast = useToast();
  const files = useFiles();
  useEffect(() => { api<{ notice: string }>("/packs/legal/settings").then((s) => setNotice(s.notice)).catch(setGate); }, []);
  if (gate) return <div className="page"><PackGate error={gate} packId="legal" name="Legal pack" /></div>;
  const tabs: [Tab, string][] = [["check", "Check a document"], ["compare", "Compare versions"], ["timeline", "Matter timeline"], ["clauses", "Clause library"]];
  return (
    <div className="page" style={{ maxWidth: 1120 }}>
      <div className="page-head">
        <div>
          <p className="kicker">Legal pack</p>
          <h1>Documents &amp; matters</h1>
          <p className="muted">Checks that catch the slips a second reader would, each one with where it is in the document.</p>
        </div>
        <div className="tabs" role="tablist" style={{ marginLeft: "auto" }}>
          {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
        </div>
      </div>
      {notice && <NotAdvice text={notice} />}
      {tab === "check" && <CheckTab files={files} toast={toast} />}
      {tab === "compare" && <CompareTab files={files} toast={toast} />}
      {tab === "timeline" && <TimelineTab files={files} toast={toast} />}
      {tab === "clauses" && <Clauses toast={toast} />}
      {toast.node}
    </div>
  );
}

type Ctx = { files: ReturnType<typeof useFiles>; toast: ReturnType<typeof useToast> };

function CheckTab({ files, toast }: Ctx) {
  const [file, setFile] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [r, setR] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); try { setR(await api<Check>("/packs/legal/check", { body: { file_id: file, text } })); } catch (e) { toast.err(e); } setBusy(false); };
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="card-head"><h2>Document</h2></div>
        <FilePick label="Pick a contract or document" value={file} onChange={setFile} files={files.files} onAdded={files.reload} accept=".docx,.pdf,.txt,.md,.rtf,.doc" />
        <textarea className="input" rows={6} placeholder="…or paste the text" value={text} onChange={(e) => setText(e.target.value)} disabled={!!file} />
        <div className="row"><button className="btn primary" disabled={busy || (!file && !text.trim())} onClick={go}>{busy ? "Checking…" : "Check"}</button></div>
      </div>
      {r ? (
        <div className="card pk-stack">
          <p className="kicker" style={{ margin: 0 }}>{r.document}</p>
          <p style={{ margin: 0 }}>{r.summary}</p>
          <Section title="Used like defined terms, but not defined" note={r.defined_terms.note}
            items={r.defined_terms.used_not_defined.map((t) => ({ main: `${t.term} (${t.uses} ${t.uses === 1 ? "use" : "uses"})`, where: t.first_used ?? "" }))} />
          <Section title="Defined but never used" items={r.defined_terms.defined_not_used.map((t) => ({ main: t.term, where: t.defined_at ?? "" }))} />
          <Section title="Cross-references that point nowhere" items={r.cross_references.broken.map((b) => ({ main: `${b.reference}: ${b.problem}`, where: b.where }))} />
          <Section title="Citations" note={r.citations.note} items={r.citations.citations.map((c) => ({
            main: `${c.citation} · ${c.kind}${c.problems.length ? ` · ${c.problems.join("; ")}` : ""}`, where: c.where, link: c.source, bad: c.problems.length > 0 }))} />
          <details><summary className="muted" style={{ fontSize: 12 }}>{r.defined_terms.defined.length} defined terms found</summary>
            {r.defined_terms.defined.map((t) => <div key={t.term} className="pk-find"><span>{t.term} · used {t.uses}×</span><span className="where">{t.defined_at}</span></div>)}</details>
        </div>
      ) : <div className="card"><p className="muted">Results show here, each with its place in the document.</p></div>}
    </div>
  );
}

function Section({ title, note, items }: { title: string; note?: string; items: { main: string; where: string; link?: string; bad?: boolean }[] }) {
  return (
    <div>
      <p className="label">{title} · {items.length}</p>
      {!items.length ? <p className="muted" style={{ fontSize: 13, margin: 0 }}>None found.</p> : items.map((x, i) => (
        <div key={i} className="pk-find">
          <span className={x.bad ? "pk-err" : ""}>{x.main}</span>
          <span className="where">{x.where}{x.link && <> · <a href={x.link} target="_blank" rel="noreferrer">check the source</a></>}</span>
        </div>
      ))}
      {note && <p className="muted" style={{ fontSize: 11 }}>{note}</p>}
    </div>
  );
}

type Cmp = { a: string; b: string; summary: string; text_changes: { change: string; where_before: string; where_after: string; redline: string }[]; report?: { download: string } };

function CompareTab({ files, toast }: Ctx) {
  const [a, setA] = useState<number | null>(null);
  const [b, setB] = useState<number | null>(null);
  const [r, setR] = useState<Cmp | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="pk-stack">
      <div className="card pk-stack">
        <div className="grid two">
          <FilePick label="Older version" value={a} onChange={setA} files={files.files} onAdded={files.reload} />
          <FilePick label="Newer version" value={b} onChange={setB} files={files.files} onAdded={files.reload} />
        </div>
        <div className="row"><button className="btn primary" disabled={!a || !b || busy} onClick={async () => {
          setBusy(true); try { setR(await api<Cmp>("/packs/legal/compare", { body: { a, b } })); } catch (e) { toast.err(e); } setBusy(false);
        }}>{busy ? "Comparing…" : "Compare"}</button>
        {r?.report && <button className="btn" onClick={() => download(r.report!.download)}>Download Word redline</button>}</div>
      </div>
      {r && <div className="card pk-stack">
        <p style={{ margin: 0 }}>{r.summary}</p>
        {r.text_changes.map((c, i) => (
          <div key={i} className="pk-find">
            <span className="where">{c.change} · {r.a} {c.where_before || "—"} → {r.b} {c.where_after || "—"}</span>
            <Markdown text={c.redline} className="pk-redline" />
          </div>
        ))}
      </div>}
    </div>
  );
}

function TimelineTab({ files, toast }: Ctx) {
  const [chosen, setChosen] = useState<number[]>([]);
  const [text, setText] = useState("");
  const [dayFirst, setDayFirst] = useState(true);
  const [r, setR] = useState<{ entries: Event[]; note: string; ambiguous: number } | null>(null);
  const [busy, setBusy] = useState("");
  const body = { file_ids: chosen, text, day_first: dayFirst };
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="card-head"><h2>Documents in this matter</h2></div>
        <div className="pk-list" style={{ maxHeight: 260, overflow: "auto" }}>
          {files.files.map((f) => (
            <label key={f.id} className="pk-item"><input type="checkbox" checked={chosen.includes(f.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, f.id] : chosen.filter((x) => x !== f.id))} />
              <span className="grow">{f.name}</span></label>
          ))}
          {!files.files.length && <p className="muted">Add documents in Files first, or paste text below.</p>}
        </div>
        <textarea className="input" rows={4} placeholder="…or paste text (emails, notes)" value={text} onChange={(e) => setText(e.target.value)} disabled={chosen.length > 0} />
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={dayFirst} onChange={(e) => setDayFirst(e.target.checked)} /> Read 03/04/2025 as 3 April (day first)</label>
        <div className="row">
          <button className="btn primary" disabled={!!busy || (!chosen.length && !text.trim())} onClick={async () => { setBusy("t"); try { setR(await api("/packs/legal/timeline", { body })); } catch (e) { toast.err(e); } setBusy(""); }}>{busy === "t" ? "Reading…" : "Build timeline"}</button>
          {r && <button className="btn" disabled={!!busy} onClick={async () => { setBusy("w"); try { download((await api<{ download: string }>("/packs/legal/timeline/report", { body })).download); } catch (e) { toast.err(e); } setBusy(""); }}>Word report</button>}
        </div>
      </div>
      <div className="card">
        {!r ? <p className="muted">Every dated event, in order, with the passage it came from.</p> : <>
          <p className="muted" style={{ fontSize: 12 }}>{r.entries.length} events · {r.note}</p>
          <ol className="pk-timeline">
            {r.entries.map((e, i) => (
              <li key={i}><div className="d">{new Date(e.date + "T00:00").toLocaleDateString("en-AU", { day: "numeric", month: "long", year: "numeric" })}
                {e.ambiguous && <span className="badge warn" style={{ marginLeft: 8 }}>check date</span>}</div>
                <div style={{ fontSize: 13 }}>{e.event}</div><div className="muted" style={{ fontSize: 11 }}>{e.source} · written “{e.as_written}”</div></li>
            ))}
          </ol>
        </>}
      </div>
    </div>
  );
}

function Clauses({ toast }: { toast: ReturnType<typeof useToast> }) {
  const [q, setQ] = useState("");
  const [list, setList] = useState<Clause[]>([]);
  const [edit, setEdit] = useState<Partial<Clause> | null>(null);
  const load = useCallback(() => api<Clause[]>(`/packs/legal/clauses?q=${encodeURIComponent(q)}`).then(setList).catch(toast.err), [q]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="row"><input className="input grow" placeholder="Search your clauses" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search clauses" />
          <button className="btn primary" onClick={() => setEdit({ title: "", body: "", tags: "" })}>New clause</button></div>
        <div className="pk-list">
          {list.map((c) => (
            <div key={c.id} className="pk-item" style={{ cursor: "default" }}>
              <button className="grow" style={{ background: "none", border: 0, textAlign: "left", font: "inherit", color: "inherit", cursor: "pointer" }} onClick={() => setEdit(c)}>
                <strong>{c.title}</strong> <span className="muted" style={{ fontSize: 12 }}>{c.tags}</span></button>
              <span className="muted" style={{ fontSize: 11 }}>used {c.uses}×</span>
              <button className="btn ghost icon sm" aria-label="Copy clause" onClick={async () => { await api(`/packs/legal/clauses/${c.id}/use`, { method: "POST" }); copy(c.body); toast.ok("Copied"); load(); }}><Copy size={13} /></button>
              <button className="btn ghost icon sm" aria-label="Delete clause" onClick={async () => { await api(`/packs/legal/clauses/${c.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
            </div>
          ))}
          {!list.length && <p className="muted">Save clauses you reuse: limitation of liability, confidentiality, payment terms.</p>}
        </div>
      </div>
      {edit && <div className="card pk-stack">
        <input className="input" placeholder="Title" value={edit.title ?? ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
        <textarea className="input" rows={10} placeholder="Clause text" value={edit.body ?? ""} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
        <input className="input" placeholder="Tags, comma separated" value={edit.tags ?? ""} onChange={(e) => setEdit({ ...edit, tags: e.target.value })} />
        <div className="row"><button className="btn primary" onClick={async () => {
          try { await api(edit.id ? `/packs/legal/clauses/${edit.id}` : "/packs/legal/clauses", { method: edit.id ? "PUT" : "POST", body: edit }); setEdit(null); load(); toast.ok("Saved"); } catch (e) { toast.err(e); }
        }}>Save</button><button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button></div>
      </div>}
    </div>
  );
}
