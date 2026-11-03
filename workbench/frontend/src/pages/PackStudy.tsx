// Study & research pack: flashcards with spaced repetition, worked maths checked by sympy, references, lab notebook (feat/v02-packs).
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Check, X } from "lucide-react";
import { ApiError, api } from "../api";
import { GRADES, authorsText, deckProgress, gradeForKey, mathBlock, parseAuthors, workingLines } from "../packs/logic";
import { FilePick, PackGate, download, useFiles } from "../packs/shared";
import { Markdown, useToast } from "../util";
import "../packs/packs.css";

type Card = { id: number; deck: string; front: string; back: string; source: string; reps: number };
type Deck = { deck: string; cards: number; due: number };
type Tab = "cards" | "maths" | "refs" | "lab";
type Toast = ReturnType<typeof useToast>;

export default function PackStudy() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "cards";
  const [gate, setGate] = useState<unknown>(null);
  const toast = useToast();
  useEffect(() => { api("/packs/study/decks").catch(setGate); }, []);
  if (gate) return <div className="page"><PackGate error={gate} packId="study" name="Study & research pack" /></div>;
  const tabs: [Tab, string][] = [["cards", "Flashcards"], ["maths", "Maths"], ["refs", "References"], ["lab", "Lab notebook"]];
  return (
    <div className="page" style={{ maxWidth: 1120 }}>
      <div className="page-head">
        <div><p className="kicker">Study &amp; research pack</p><h1>Study</h1>
          <p className="muted">Learn it, work it out, cite it and write it up.</p></div>
        <div className="tabs" role="tablist" style={{ marginLeft: "auto" }}>
          {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setParams({ tab: k })}>{l}</button>)}
        </div>
      </div>
      {tab === "cards" && <Flashcards toast={toast} />}
      {tab === "maths" && <Maths toast={toast} />}
      {tab === "refs" && <Refs toast={toast} />}
      {tab === "lab" && <Lab toast={toast} />}
      {toast.node}
    </div>
  );
}

function Flashcards({ toast }: { toast: Toast }) {
  const [decks, setDecks] = useState<Deck[]>([]);
  const [queue, setQueue] = useState<Card[] | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [file, setFile] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [deck, setDeck] = useState("");
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState("");
  const files = useFiles();
  const load = useCallback(() => api<Deck[]>("/packs/study/decks").then(setDecks).catch(toast.err), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const card = queue?.[0];
  const grade = useCallback(async (g: string) => {
    if (!card) return;
    try {
      const r = await api<{ next_in: string }>(`/packs/study/cards/${card.id}/review`, { body: { grade: g } });
      setLast(`Back in ${r.next_in}`);
      setQueue((q) => (q ? [...q.slice(1), ...(g === "again" ? [card] : [])] : q));
      setFlipped(false);
    } catch (e) { toast.err(e); }
  }, [card]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!card) return;
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select")) return;
      if (e.key === " ") { e.preventDefault(); setFlipped((f) => !f); return; }
      const g = gradeForKey(e.key);
      if (g && flipped) grade(g);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [card, flipped, grade]);

  if (queue) {
    return (
      <div className="pk-stack">
        <div className="row"><button className="btn ghost" onClick={() => { setQueue(null); load(); }}>← Decks</button>
          <span className="muted" style={{ fontSize: 12 }}>{queue.length} to go · {last}</span></div>
        {card ? <>
          <div className={`pk-card ${flipped ? "flipped" : ""}`}>
            <button className="pk-card-inner" style={{ width: "100%", border: 0, background: "none", padding: 0, cursor: "pointer", display: "block" }}
              onClick={() => setFlipped(!flipped)} aria-label={flipped ? "Show question" : "Show answer"}>
              <div className="pk-face front"><div><div className="q">{card.front}</div><p className="muted" style={{ fontSize: 12 }}>Tap or press space to see the answer</p></div><span className="src">{card.deck}</span></div>
              <div className="pk-face back"><div><Markdown text={card.back} /></div><span className="src">{card.source}</span></div>
            </button>
          </div>
          {flipped && <div className="pk-grades">{GRADES.map((g) => <button key={g.key} className="btn" onClick={() => grade(g.key)}>{g.label}<small>{g.hint} · {g.shortcut}</small></button>)}</div>}
        </> : <div className="card"><h2 className="pk-serif">All done for now</h2><p className="muted">Cards come back when they're due, sooner if you found them hard.</p></div>}
      </div>
    );
  }
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="card-head"><h2>Your decks</h2></div>
        {!decks.length ? <p className="muted">No decks yet. Make cards from a file or your notes.</p> : (
          <div className="pk-list">{decks.map((d) => (
            <div key={d.deck} className="pk-item" style={{ cursor: "default" }}>
              <span className="grow"><strong>{d.deck}</strong> <span className="muted" style={{ fontSize: 12 }}>{deckProgress(d.cards, d.due)}</span></span>
              <button className="btn sm primary" disabled={!d.due} onClick={async () => { setQueue(await api<Card[]>(`/packs/study/due?deck=${encodeURIComponent(d.deck)}`)); setFlipped(false); }}>Review</button>
            </div>))}</div>
        )}
      </div>
      <div className="card pk-stack">
        <div className="card-head"><h2>Make flashcards</h2></div>
        <FilePick label="From a file (notes, slides, PDF…)" value={file} onChange={setFile} files={files.files} onAdded={files.reload} />
        <textarea className="input" rows={5} placeholder="…or paste notes" value={text} onChange={(e) => setText(e.target.value)} disabled={!!file} />
        <input className="input" placeholder="Deck name (optional)" value={deck} onChange={(e) => setDeck(e.target.value)} />
        <div className="row"><button className="btn primary" disabled={busy || (!file && !text.trim())} onClick={async () => {
          setBusy(true);
          try { const r = await api<{ made: number; deck: string; note: string }>("/packs/study/cards", { body: { file_id: file, text, deck } }); toast.ok(r.made ? `${r.made} cards in ${r.deck}` : r.note); setText(""); load(); }
          catch (e) { toast.err(e); }
          setBusy(false);
        }}>{busy ? "Making…" : "Make cards"}</button></div>
      </div>
    </div>
  );
}

type MathRes = { task: string; steps: { say: string; latex: string }[]; answer_latex: string; check: { ok: boolean; how: string } | null };
type StepRes = { lines: { line: number; latex: string; ok: boolean; why: string }[]; first_wrong: number | null; all_ok: boolean };
const TASKS = ["auto", "solve", "simplify", "factor", "expand", "differentiate", "integrate", "limit", "evaluate"];

function Maths({ toast }: { toast: Toast }) {
  const [expr, setExpr] = useState("");
  const [task, setTask] = useState("auto");
  const [v, setV] = useState("");
  const [to, setTo] = useState("");
  const [r, setR] = useState<MathRes | null>(null);
  const [work, setWork] = useState("");
  const [chk, setChk] = useState<StepRes | null>(null);
  const [busy, setBusy] = useState("");
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="card-head"><h2>Work it out</h2></div>
        <input className="input mono" placeholder="e.g. x^2 - 5x + 6 = 0, or \frac{1}{x} + x^3" value={expr} onChange={(e) => setExpr(e.target.value)} aria-label="Maths" />
        <div className="row wrap">
          <select className="input" value={task} onChange={(e) => setTask(e.target.value)} aria-label="What to do">{TASKS.map((t) => <option key={t} value={t}>{t === "auto" ? "Work out what to do" : t[0].toUpperCase() + t.slice(1)}</option>)}</select>
          <input className="input" style={{ width: 90 }} placeholder="variable" value={v} onChange={(e) => setV(e.target.value)} aria-label="Variable" />
          {task === "limit" && <input className="input" style={{ width: 90 }} placeholder="towards" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Limit point" />}
          <button className="btn primary" disabled={!expr.trim() || !!busy} onClick={async () => { setBusy("m"); try { setR(await api<MathRes>("/packs/study/maths", { body: { expression: expr, task, variable: v, to } })); } catch (e) { toast.err(e); } setBusy(""); }}>{busy === "m" ? "Working…" : "Show steps"}</button>
        </div>
        {r && <div>
          {r.steps.map((s, i) => <div key={i} className="pk-step"><span className="n">{i + 1}</span><div><div className="label" style={{ margin: 0 }}>{s.say}</div><Markdown text={mathBlock(s.latex)} /></div></div>)}
          {r.check && <p className={`pk-check ${r.check.ok ? "ok" : "bad"}`}>{r.check.ok ? <Check size={13} /> : <X size={13} />} {r.check.ok ? "Checked" : "Check failed"}: {r.check.how}</p>}
        </div>}
      </div>
      <div className="card pk-stack">
        <div className="card-head"><h2>Check my working</h2></div>
        <textarea className="input mono" rows={6} placeholder={"One step per line:\n2x + 4 = 10\n2x = 6\nx = 3"} value={work} onChange={(e) => setWork(e.target.value)} />
        <div className="row"><button className="btn" disabled={workingLines(work).length < 2 || !!busy} onClick={async () => { setBusy("c"); try { setChk(await api<StepRes>("/packs/study/maths/check", { body: { lines: workingLines(work) } })); } catch (e) { toast.err(e); } setBusy(""); }}>{busy === "c" ? "Checking…" : "Check each step"}</button></div>
        {chk && <div>
          <p className={`pk-check ${chk.all_ok ? "ok" : "bad"}`}>{chk.all_ok ? "Every step follows from the one before." : `Step ${chk.first_wrong} is where it goes wrong.`}</p>
          {chk.lines.map((l) => <div key={l.line} className="pk-step"><span className="n">{l.ok ? <Check size={14} /> : <X size={14} />}</span><div><Markdown text={mathBlock(l.latex)} />{l.why && <span className={l.ok ? "muted" : "pk-err"} style={{ fontSize: 12 }}>{l.why}</span>}</div></div>)}
        </div>}
      </div>
    </div>
  );
}

type Ref = { id: number; key: string; type: string; title: string; authors: { family: string; given: string }[]; year: string; container: string; doi: string };

function Refs({ toast }: { toast: Toast }) {
  const [list, setList] = useState<Ref[]>([]);
  const [doi, setDoi] = useState("");
  const [bib, setBib] = useState("");
  const [form, setForm] = useState({ type: "article", title: "", authors: "", year: "", container: "", volume: "", issue: "", pages: "", url: "", publisher: "" });
  const [zot, setZot] = useState<{ running: boolean; local_api: boolean; how: string } | null>(null);
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api<Ref[]>("/packs/study/refs").then(setList).catch(toast.err), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); api<typeof zot>("/packs/study/zotero").then(setZot).catch(() => setZot(null)); }, [load]);
  const run = async (k: string, f: () => Promise<void>) => { setBusy(k); try { await f(); } catch (e) { toast.err(e); } setBusy(""); };
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="row" style={{ justifyContent: "space-between" }}><div className="card-head" style={{ margin: 0 }}><h2>References · {list.length}</h2></div>
          <div className="row"><a className="btn sm" href="/api/packs/study/refs/export?format=bibtex" download="references.bib">BibTeX</a>
            <a className="btn sm" href="/api/packs/study/refs/export?format=ris" download="references.ris">RIS</a></div></div>
        <div className="pk-list">{list.map((r) => (
          <div key={r.id} className="pk-find">
            <span>{authorsText(r.authors)} ({r.year || "n.d."}) <em>{r.title}</em>{r.container ? `, ${r.container}` : ""}</span>
            <span className="where">{r.key}{r.doi ? ` · doi ${r.doi}` : ""} · <button className="btn ghost sm" style={{ height: 18 }} onClick={() => run("d", async () => { await api(`/packs/study/refs/${r.id}`, { method: "DELETE" }); load(); })}>remove</button></span>
          </div>))}
          {!list.length && <p className="muted">Add a DOI, type one in, import BibTeX, or bring your Zotero library over.</p>}</div>
        <div className="pk-draft">
          <div className="row"><strong>Zotero</strong><span className={`badge ${zot?.running ? "ok" : ""}`}>{zot?.running ? (zot.local_api ? "Open" : "Open · library sharing off") : "Not open"}</span></div>
          {(!zot?.running || !zot?.local_api) && <p className="muted" style={{ margin: 0, fontSize: 12 }}>{zot?.how ?? "Open Zotero on this computer."}</p>}
          <div className="row">
            <button className="btn sm" disabled={!zot?.local_api || !!busy} onClick={() => run("zi", async () => { const r = await api<{ added: number }>("/packs/study/zotero/import", { method: "POST" }); toast.ok(`${r.added} added from Zotero`); load(); })}>Bring in from Zotero</button>
            <button className="btn sm" disabled={!zot?.running || !list.length || !!busy} onClick={() => run("zs", async () => { const r = await api<{ sent: number }>("/packs/study/zotero/send", { body: { ids: [] } }); toast.ok(`${r.sent} sent to Zotero`); })}>Send all to Zotero</button>
            <button className="btn sm ghost" onClick={() => api<typeof zot>("/packs/study/zotero").then(setZot)}>Check again</button>
          </div>
        </div>
      </div>
      <div className="card pk-stack">
        <div className="card-head"><h2>Add</h2></div>
        <div className="row"><input className="input grow mono" placeholder="DOI, e.g. 10.1038/nature14539" value={doi} onChange={(e) => setDoi(e.target.value)} />
          <button className="btn primary" disabled={!doi.trim() || !!busy} onClick={() => run("doi", async () => { await api("/packs/study/refs/doi", { body: { doi } }); setDoi(""); load(); toast.ok("Added"); })}>{busy === "doi" ? "Looking up…" : "Look up"}</button></div>
        <details><summary className="muted" style={{ fontSize: 12 }}>Type one in</summary>
          <div className="pk-stack" style={{ marginTop: 8 }}>
            <select className="input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>{["article", "book", "chapter", "conference", "report", "thesis", "web", "dataset", "other"].map((t) => <option key={t}>{t}</option>)}</select>
            {(["title", "authors", "year", "container", "volume", "issue", "pages", "publisher", "url"] as const).map((k) => (
              <input key={k} className="input" placeholder={k === "authors" ? "Authors (Family, Given; …)" : k === "container" ? "Journal or book" : k[0].toUpperCase() + k.slice(1)} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />))}
            <div className="row"><button className="btn" disabled={!form.title.trim()} onClick={() => run("add", async () => { await api("/packs/study/refs", { body: { ...form, authors: parseAuthors(form.authors) } }); setForm({ ...form, title: "", authors: "", year: "", pages: "" }); load(); })}>Save</button></div>
          </div></details>
        <details><summary className="muted" style={{ fontSize: 12 }}>Import BibTeX</summary>
          <textarea className="input mono" rows={6} value={bib} onChange={(e) => setBib(e.target.value)} placeholder="@article{…}" style={{ marginTop: 8 }} />
          <button className="btn" style={{ marginTop: 8 }} disabled={!bib.trim()} onClick={() => run("bib", async () => { const r = await api<{ added: number }>("/packs/study/refs/import", { body: { bibtex: bib } }); toast.ok(`${r.added} imported`); setBib(""); load(); })}>Import</button></details>
      </div>
    </div>
  );
}

type Entry = { id: number; title: string; body: string; tags: string; entry_date: string; created_at: string; updated_at: string;
  attachments: { id: number; name: string; missing?: boolean }[]; amendments: { at: string; title: string; body: string }[] };

function Lab({ toast }: { toast: Toast }) {
  const [list, setList] = useState<Entry[] | null>(null);
  const [pro, setPro] = useState("");
  const [edit, setEdit] = useState<Partial<Entry> & { attach?: number[] } | null>(null);
  const [q, setQ] = useState("");
  const files = useFiles();
  const load = useCallback(() => api<Entry[]>(`/packs/study/lab?q=${encodeURIComponent(q)}`).then((x) => { setList(x); setPro(""); })
    .catch((e) => { if (e instanceof ApiError && e.status === 402) setPro(e.message); else toast.err(e); }), [q]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  if (pro) return <div className="pk-gate card"><p className="kicker">Pro</p><h2 className="pk-serif">The lab notebook is part of Pro</h2><p className="muted">{pro}</p><p className="muted">Flashcards, maths and references stay free.</p></div>;
  const exp = async (format: string) => { try { download((await api<{ download: string }>("/packs/study/lab/export", { body: { format } })).download); } catch (e) { toast.err(e); } };
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="row"><input className="input grow" placeholder="Search entries" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search entries" />
          <button className="btn primary" onClick={() => setEdit({ title: "", body: "", tags: "", attach: [] })}>New entry</button></div>
        <div className="pk-list">{(list ?? []).map((e) => (
          <button key={e.id} className="pk-item" onClick={() => setEdit({ ...e, attach: e.attachments.map((a) => a.id) })}>
            <span className="mono" style={{ fontSize: 12 }}>{e.entry_date}</span><span className="grow">{e.title}</span>
            {e.amendments.length > 0 && <span className="badge">amended {e.amendments.length}×</span>}
            {e.attachments.length > 0 && <span className="muted" style={{ fontSize: 11 }}>{e.attachments.length} file{e.attachments.length > 1 ? "s" : ""}</span>}
          </button>))}
          {list && !list.length && <p className="muted">Dated entries for experiments, observations and results.</p>}</div>
        <div className="row"><span className="label" style={{ margin: 0 }}>Export</span>
          {["pdf", "md", "json"].map((f) => <button key={f} className="btn sm" disabled={!list?.length} onClick={() => exp(f)}>{f === "md" ? "Markdown" : f.toUpperCase()}</button>)}</div>
      </div>
      {edit && <div className="card pk-stack">
        {edit.id && <p className="muted" style={{ margin: 0, fontSize: 12 }}>Entry for {edit.entry_date}. Changes are kept as amendments, so the original stays on record.</p>}
        <input className="input" placeholder="Title" value={edit.title ?? ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
        {!edit.id && <input className="input" type="date" value={edit.entry_date ?? ""} onChange={(e) => setEdit({ ...edit, entry_date: e.target.value })} aria-label="Date (today if blank)" />}
        <textarea className="input" rows={10} placeholder="Method, observations, results… ($maths$ works)" value={edit.body ?? ""} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
        <input className="input" placeholder="Tags" value={edit.tags ?? ""} onChange={(e) => setEdit({ ...edit, tags: e.target.value })} />
        <div className="pk-stack" style={{ gap: 6 }}>
          {(edit.attach ?? []).map((id) => <div key={id} className="row" style={{ fontSize: 12 }}>{files.files.find((f) => f.id === id)?.name ?? `File ${id}`}
            <button className="btn ghost sm" onClick={() => setEdit({ ...edit, attach: (edit.attach ?? []).filter((x) => x !== id) })}>remove</button></div>)}
          <FilePick label="Attach a file" value={null} onChange={(id) => id && setEdit({ ...edit, attach: [...new Set([...(edit.attach ?? []), id])] })} files={files.files} onAdded={files.reload} />
        </div>
        <div className="row"><button className="btn primary" disabled={!edit.title?.trim()} onClick={async () => {
          try { await api(edit.id ? `/packs/study/lab/${edit.id}` : "/packs/study/lab", { method: edit.id ? "PUT" : "POST", body: { ...edit, attachments: edit.attach } }); setEdit(null); load(); toast.ok("Saved"); } catch (e) { toast.err(e); }
        }}>Save</button><button className="btn ghost" onClick={() => setEdit(null)}>Close</button></div>
        {!!edit.amendments?.length && <details><summary className="muted" style={{ fontSize: 12 }}>Earlier versions</summary>
          {edit.amendments.map((a, i) => <div key={i} className="pk-find"><span className="where">{a.at} UTC</span><Markdown text={a.body} /></div>)}</details>}
      </div>}
    </div>
  );
}
