import { BarChart3, Code2, Download, FileSearch, FileText, FolderOpen, GitCompare, Loader2, MessageSquare, Trash2, Upload } from "lucide-react";
import { DragEvent, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, relTime, useToast } from "../util";
import "../work.css";

type WorkFile = { id: number; name: string; kind: string; size: number; summary: string; source: string; notes: string[]; created_at: string };
type Chunk = { loc: string; text: string };
type Table = { name: string; loc?: string; columns: string[]; rows: unknown[][] | Record<string, unknown>[]; n_rows?: number };
type Detail = WorkFile & { extract: { chunks: Chunk[]; tables: Table[]; meta: Record<string, unknown>; notes: string[] };
  inside: { name: string; kind: string; summary: string; notes: string[] }[] };
type Column = { column: string; type: string; filled: number; missing: number; sum?: number; mean?: number; min?: number; max?: number;
  earliest?: string; latest?: string; most_common?: Record<string, number> };
type Analysis = { file: string; sheets: { sheet: string; rows: number; columns: number; duplicate_rows: number; note?: string;
  column_summary: Column[]; formulas?: { count?: number }; chart?: { title: string; file: number } | null }[] };
type Change = { change: string; where_before: string; where_after: string; redline: string };
type Comparison = { a: string; b: string; summary: string; similarity_percent: number; text_changes: Change[];
  cell_changes: { table: string; cell?: string; column?: string; before?: unknown; after?: unknown; change?: string }[]; report?: { download: string } };
type RunResult = { status: string; isolation: string; stdout: string; error: string; outputs: { id: number; name: string; url: string }[] };
type Helpers = { missing: { key: string; label: string; why: string }[]; can_install: boolean; hint: string; installing: boolean; log: string[]; error: string };
type Caps = { readers: Record<string, boolean>; apps: Record<string, boolean>; helpers?: Helpers };

/** Plain words about missing helper apps, and one Install button (quiet brew / winget install; failures are skipped). */
function HelperApps({ initial }: { initial?: Helpers }) {
  const [h, setH] = useState<Helpers | undefined>(initial);
  useEffect(() => setH(initial), [initial]);
  useEffect(() => {
    if (!h?.installing) return;
    const t = window.setInterval(() => api<Helpers>("/files/helpers").then(setH).catch(() => undefined), 2000);
    return () => window.clearInterval(t);
  }, [h?.installing]);
  if (!h || (h.missing.length === 0 && h.log.length === 0)) return null;
  const install = () => api<Helpers>("/files/helpers/install", { method: "POST" }).then(setH).catch(() => undefined);
  return (
    <div className="card work-helpers" role="status">
      {h.missing.length > 0 ? (
        <p>
          {h.missing.length === 1 ? "One free helper app is" : "Some free helper apps are"} not installed yet, so a few files are read with less detail:{" "}
          {h.missing.map((m, i) => <span key={m.key}>{i > 0 && ", "}<b>{m.label}</b> ({m.why})</span>)}.
        </p>
      ) : <p>All helper apps are installed.</p>}
      {h.log.length > 0 && <div className="muted small">{h.log.join(" ")}</div>}
      {h.error && <div className="muted small">{h.error}</div>}
      {h.missing.length > 0 && (
        <button className="btn sm primary" disabled={h.installing} onClick={install}>
          {h.installing ? <><Loader2 size={14} className="spin" /> Installing…</> : "Install"}
        </button>
      )}
    </div>
  );
}

const KB = (n: number) => (n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const fmt = (v: unknown) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v ?? ""));

async function upload(file: File, name: string): Promise<WorkFile> {
  const res = await fetch(`/api/files/upload?name=${encodeURIComponent(name)}`, {
    method: "POST", credentials: "same-origin", headers: { "X-Workbench": "1", "Content-Type": "application/octet-stream" }, body: file,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Could not add ${name}`);
  return body;
}

/** Walks a dropped folder (Chrome, Edge, Safari, Firefox) into files with their folder path. */
async function dropped(e: DragEvent): Promise<{ file: File; name: string }[]> {
  const out: { file: File; name: string }[] = [];
  const items = Array.from(e.dataTransfer.items || []);
  const entries = items.map((i) => (i as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }).webkitGetAsEntry?.()).filter(Boolean) as FileSystemEntry[];
  if (!entries.length) return Array.from(e.dataTransfer.files).map((f) => ({ file: f, name: f.name }));
  const walk = async (en: FileSystemEntry, path: string): Promise<void> => {
    if (en.isFile) {
      const f = await new Promise<File>((ok, bad) => (en as FileSystemFileEntry).file(ok, bad));
      out.push({ file: f, name: path + f.name });
    } else if (en.isDirectory) {
      const reader = (en as FileSystemDirectoryEntry).createReader();
      let batch: FileSystemEntry[] = [];
      do {
        batch = await new Promise<FileSystemEntry[]>((ok, bad) => reader.readEntries(ok, bad));
        for (const c of batch) await walk(c, `${path}${en.name}/`);
      } while (batch.length);
    }
  };
  for (const en of entries) await walk(en, "");
  return out;
}

export default function Files() {
  const [list, setList] = useState<WorkFile[]>([]);
  const [sel, setSel] = useState<Detail | null>(null);
  const [busy, setBusy] = useState("");
  const [over, setOver] = useState(false);
  const [tab, setTab] = useState<"read" | "numbers" | "compare" | "python">("read");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [cmpWith, setCmpWith] = useState<number>(0);
  const [cmp, setCmp] = useState<Comparison | null>(null);
  const [code, setCode] = useState("df = pd.read_csv('inputs/' + INPUTS[0]) if INPUTS else None\nprint(df.describe() if df is not None else 'Add a file first')");
  const [run, setRun] = useState<RunResult | null>(null);
  const [caps, setCaps] = useState<Caps | null>(null);
  const toast = useToast();
  const pick = useRef<HTMLInputElement>(null);
  const pickDir = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const load = useCallback(() => api<WorkFile[]>("/files").then(setList).catch(toast.err), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); api<Caps>("/files/capabilities").then(setCaps).catch(() => undefined); }, [load]);

  const open = async (id: number) => {
    setBusy("open"); setAnalysis(null); setCmp(null); setRun(null); setTab("read");
    try { setSel(await api<Detail>(`/files/${id}`)); } catch (e) { toast.err(e); } finally { setBusy(""); }
  };

  const addAll = async (items: { file: File; name: string }[]) => {
    if (!items.length) return;
    let last: WorkFile | null = null;
    for (let i = 0; i < items.length; i++) {
      setBusy(`Reading ${items[i].name} (${i + 1} of ${items.length})`);
      try { last = await upload(items[i].file, items[i].name); } catch (e) { toast.err(e); }
    }
    setBusy("");
    await load();
    if (last) { toast.ok(items.length > 1 ? `${items.length} files added` : `${last.name} added`); open(last.id); }
  };

  const onDrop = async (e: DragEvent) => { e.preventDefault(); setOver(false); addAll(await dropped(e)); };

  const doAnalyse = async () => {
    if (!sel) return;
    setTab("numbers"); setBusy("Working out the numbers…");
    try { setAnalysis(await api<Analysis>(`/files/${sel.id}/analyse`)); } catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const doCompare = async (report = false) => {
    if (!sel || !cmpWith) return;
    setBusy("Comparing…");
    try { setCmp(await api<Comparison>("/files/compare", { body: { a: cmpWith, b: sel.id, report } })); } catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const doRun = async () => {
    setBusy("Running…");
    try { setRun(await api<RunResult>("/files/run", { body: { code: `INPUTS = ${JSON.stringify(sel ? [sel.name.split("/").pop()] : [])}\n${code}`, file_ids: sel ? [sel.id] : [] } })); }
    catch (e) { toast.err(e); } finally { setBusy(""); }
  };
  const doReport = async (format: "docx" | "pdf") => {
    if (!sel) return;
    const body = `**${sel.name}**\n\n${sel.summary}\n\n${sel.extract.chunks.slice(0, 40).map((c) => `*${c.loc}*: ${c.text}`).join("\n\n")}`;
    try { const r = await api<{ download: string }>("/files/report", { body: { title: `Summary of ${sel.name}`, body, format } }); window.open(r.download, "_blank"); }
    catch (e) { toast.err(e); }
  };
  const remove = async (id: number) => {
    await api(`/files/${id}`, { method: "DELETE" }).catch(toast.err);
    if (sel?.id === id) setSel(null);
    load();
  };
  const ask = () => sel && navigate("/assistant", { state: { draft: `About my file “${sel.name}” (file ${sel.id}): ` } });
  const missing = caps ? [...Object.entries(caps.readers).filter(([, v]) => !v).map(([k]) => k), ...Object.entries(caps.apps).filter(([k, v]) => !v && !/^(LibreOffice|Tesseract|FFmpeg)/.test(k)).map(([k]) => k)] : [];

  return (
    <div className="page work">
      {toast.node}
      <div className="page-head">
        <div><h1>Files</h1><p className="muted">Drop in anything: spreadsheets, Word, PDFs, slides, emails, zips, drawings, photos, data or code. It is read on this computer; only the text you ask about goes to your model.</p></div>
      </div>

      <HelperApps initial={caps?.helpers} />
      <div className={`work-drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}
        onClick={() => pick.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && pick.current?.click()}>
        <Upload size={22} />
        <div><strong>Drop files or a folder here</strong><div className="muted small">or click to choose files</div></div>
        <button type="button" className="btn sm" onClick={(e) => { e.stopPropagation(); pickDir.current?.click(); }}><FolderOpen size={14} /> Choose a folder</button>
        <input ref={pick} type="file" multiple hidden onChange={(e) => addAll(Array.from(e.target.files || []).map((f) => ({ file: f, name: f.name })))} />
        <input ref={pickDir} type="file" multiple hidden {...{ webkitdirectory: "" }} onChange={(e) => addAll(Array.from(e.target.files || []).map((f) => ({ file: f, name: f.webkitRelativePath || f.name })))} />
      </div>
      {busy && busy !== "open" && <div className="row muted work-busy"><Loader2 size={15} className="spin" /> {busy}</div>}

      <div className="work-layout">
        <div className="card flush work-list">
          {!list.length && <div className="pad"><Empty icon={<FileText size={22} />} title="No files yet" hint="Anything you drop in shows here." /></div>}
          {list.map((f) => (
            <div key={f.id} className={`work-item ${sel?.id === f.id ? "on" : ""}`} onClick={() => open(f.id)}>
              <div className="grow">
                <div className="truncate">{f.name}</div>
                <div className="small faint truncate">{f.kind} · {KB(f.size)} · {f.source.startsWith("site:") ? "from a site" : relTime(f.created_at)}</div>
              </div>
              <button className="btn ghost sm icon" aria-label={`Remove ${f.name}`} onClick={(e) => { e.stopPropagation(); remove(f.id); }}><Trash2 size={13} /></button>
            </div>
          ))}
        </div>

        <div className="work-main">
          {!sel && <div className="card"><Empty icon={<FileSearch size={24} />} title="Pick a file to see what is inside" hint="Then ask about it in Chat, check the numbers, compare versions or run a calculation." /></div>}
          {sel && (
            <div className="card col gap-lg">
              <div className="row wrap" style={{ alignItems: "flex-start" }}>
                <div className="grow"><h2>{sel.name}</h2><div className="muted">{sel.summary}</div></div>
                <button className="btn primary sm" onClick={ask}><MessageSquare size={14} /> Ask about it</button>
                <a className="btn sm" href={`/api/files/${sel.id}/download`}><Download size={14} /> Original</a>
              </div>
              {[...sel.notes, ...sel.extract.notes].filter((n, i, a) => a.indexOf(n) === i).map((n) => <div key={n} className="alert warn small">{n}</div>)}
              <div className="tabs">
                <button className={`tab ${tab === "read" ? "active" : ""}`} onClick={() => setTab("read")}><FileText size={14} /> What it says</button>
                <button className={`tab ${tab === "numbers" ? "active" : ""}`} onClick={doAnalyse} disabled={!sel.extract.tables.length}><BarChart3 size={14} /> Numbers</button>
                <button className={`tab ${tab === "compare" ? "active" : ""}`} onClick={() => setTab("compare")}><GitCompare size={14} /> Compare</button>
                <button className={`tab ${tab === "python" ? "active" : ""}`} onClick={() => setTab("python")}><Code2 size={14} /> Calculate</button>
              </div>

              {tab === "read" && (
                <div className="col gap">
                  <div className="row wrap"><button className="btn sm" onClick={() => doReport("docx")}>Word summary</button><button className="btn sm" onClick={() => doReport("pdf")}>PDF summary</button></div>
                  {sel.inside.length > 0 && <div className="col"><h3>Inside</h3>{sel.inside.map((x, i) => <div key={i} className="small"><strong>{x.name}</strong> <span className="muted">{x.summary}</span></div>)}</div>}
                  {sel.extract.tables.slice(0, 3).map((t, i) => <TableView key={i} t={t} />)}
                  <div className="col work-passages">
                    {sel.extract.chunks.slice(0, 120).map((c, i) => <div key={i} className="work-passage"><span className="badge">{c.loc}</span><Markdown text={c.text.slice(0, 3000)} /></div>)}
                  </div>
                </div>
              )}

              {tab === "numbers" && analysis && analysis.sheets.map((s) => (
                <div key={s.sheet} className="col gap">
                  <h3>{s.sheet} <span className="muted small">{s.rows.toLocaleString()} rows · {s.columns} columns{s.duplicate_rows ? ` · ${s.duplicate_rows} duplicate rows` : ""}{s.formulas?.count ? ` · ${s.formulas.count} formulas` : ""}</span></h3>
                  {s.note && <div className="muted small">{s.note}</div>}
                  {s.chart && <img className="work-chart" src={`/api/files/${s.chart.file}/download`} alt={s.chart.title} />}
                  <div className="work-table-wrap"><table className="work-table"><thead><tr><th>Column</th><th>Type</th><th>Filled</th><th>Total</th><th>Average</th><th>Lowest</th><th>Highest</th><th>Most common</th></tr></thead>
                    <tbody>{s.column_summary.map((c) => <tr key={c.column}><td>{c.column}</td><td>{c.type}</td><td>{c.filled}{c.missing ? ` (${c.missing} empty)` : ""}</td><td>{fmt(c.sum)}</td><td>{fmt(c.mean)}</td>
                      <td>{fmt(c.min ?? c.earliest)}</td><td>{fmt(c.max ?? c.latest)}</td><td className="small">{c.most_common ? Object.keys(c.most_common).slice(0, 3).join(", ") : ""}</td></tr>)}</tbody></table></div>
                </div>
              ))}

              {tab === "compare" && (
                <div className="col gap">
                  <div className="row wrap">
                    <span className="muted">Compare an older version</span>
                    <select className="input" style={{ width: 260 }} value={cmpWith} onChange={(e) => setCmpWith(Number(e.target.value))}>
                      <option value={0}>Choose a file…</option>
                      {list.filter((f) => f.id !== sel.id).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                    <span className="muted">with this one</span>
                    <button className="btn primary sm" disabled={!cmpWith} onClick={() => doCompare(false)}>Show changes</button>
                    <button className="btn sm" disabled={!cmpWith} onClick={() => doCompare(true)}>Word redline</button>
                  </div>
                  {cmp && <>
                    <div className="muted">{cmp.summary}</div>
                    {cmp.report && <a className="btn sm" href={cmp.report.download}><Download size={14} /> Download the redline</a>}
                    {cmp.text_changes.map((c, i) => <div key={i} className="work-passage"><span className="badge">{c.where_after || c.where_before}</span> <span className="small muted">{c.change}</span>
                      <div className="redline" dangerouslySetInnerHTML={{ __html: c.redline }} /></div>)}
                    {cmp.cell_changes.slice(0, 100).map((c, i) => <div key={i} className="small">{c.table} {c.cell || c.change}: <del>{fmt(c.before)}</del> → <ins>{fmt(c.after)}</ins></div>)}
                  </>}
                </div>
              )}

              {tab === "python" && (
                <div className="col gap">
                  <div className="muted small">Runs on this computer with pandas, numpy, matplotlib and sympy, without internet. This file is in <code>inputs/</code>; charts appear below.</div>
                  <textarea className="input mono" rows={8} value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
                  <div className="row"><button className="btn primary sm" onClick={doRun}><Code2 size={14} /> Run</button>{run && <span className="small faint">{run.isolation}</span>}</div>
                  {run && <>
                    {run.stdout && <pre className="work-out">{run.stdout}</pre>}
                    {run.error && <div className="alert err small"><pre className="work-out">{run.error.split("\n").slice(-6).join("\n")}</pre></div>}
                    {run.outputs.map((o) => /\.(png|jpe?g|svg)$/i.test(o.name) ? <img key={o.id} className="work-chart" src={o.url} alt={o.name} /> : <a key={o.id} className="btn sm" href={o.url}><Download size={14} /> {o.name}</a>)}
                  </>}
                </div>
              )}
            </div>
          )}
          {missing.length > 0 && <div className="muted small work-caps">Not installed on this computer, so some files are read with less detail: {missing.join(", ")}.</div>}
        </div>
      </div>
    </div>
  );
}

function TableView({ t }: { t: Table }) {
  const rows = (t.rows as unknown[]).slice(0, 25).map((r) => (Array.isArray(r) ? r : t.columns.map((c) => (r as Record<string, unknown>)[c])));
  return (
    <div className="col">
      <h3>{t.name} {t.n_rows ? <span className="muted small">{t.n_rows.toLocaleString()} rows</span> : null}</h3>
      <div className="work-table-wrap"><table className="work-table"><thead><tr>{t.columns.slice(0, 20).map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{(r as unknown[]).slice(0, 20).map((v, j) => <td key={j}>{fmt(v)}</td>)}</tr>)}</tbody></table></div>
    </div>
  );
}
