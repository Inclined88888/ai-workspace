// Live documents in replies (feat/v02-automations): docs, sheets with formulas and charts the user can edit in place,
// ask the assistant to change, and export. Edits are kept on this device, per message and block.
import { BarChart3, Check, Download, FileText, Loader2, Pencil, Plus, RotateCcw, Sparkles, Table2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Markdown } from "../util";
import { StreamText } from "../motion"; // integration/v0.2: word-by-word reveal from the motion system
import { Chart, chartCsv, colName, evaluate, fmtCell, liveKey, LiveKind, niceScale, pieSlices, Sheet, sheetCsv, splitLive } from "./live";
import "./v02.css";

type Data = Sheet | Chart | string;

/** Drop-in for <Markdown> on assistant replies: live blocks render as documents, the rest as Markdown. */
export function LiveMarkdown({ text, messageId, streaming = false, onDone }: { text: string; messageId?: number | string; streaming?: boolean; onDone?: () => void }) {
  const pieces = useMemo(() => splitLive(text), [text]);
  const hasLive = pieces.some((p) => p.kind !== "md");
  // Live blocks render at once (no word reveal), so finish the "fresh" state straight away.
  useEffect(() => { if (streaming && hasLive) onDone?.(); }, [streaming, hasLive]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!hasLive) return streaming ? <StreamText text={text} onDone={onDone} /> : <Markdown text={text} />;
  return (
    <div className="live-reply">
      {pieces.map((p, i) => p.kind === "md"
        ? <Markdown key={i} text={p.text} />
        : <LiveBlock key={`${p.kind}-${p.index}`} kind={p.kind} initial={p.data} storeKey={messageId !== undefined ? liveKey(messageId, p.index) : ""} />)}
    </div>
  );
}

const ICON = { doc: FileText, sheet: Table2, chart: BarChart3 };
const FORMATS: Record<LiveKind, string[]> = { doc: ["docx", "pdf"], sheet: ["xlsx", "csv", "pdf"], chart: ["png", "xlsx", "csv", "pdf"] };

function titleOf(kind: LiveKind, d: Data): string {
  if (kind === "doc") return (String(d).match(/^#+\s+(.+)$/m)?.[1] ?? "Document").trim();
  return (d as Sheet | Chart).title || (kind === "sheet" ? "Sheet" : "Chart");
}

function load(key: string, fallback: Data): Data {
  if (!key) return fallback;
  try {
    const s = localStorage.getItem(key);
    return s ? (JSON.parse(s) as Data) : fallback;
  } catch { return fallback; }
}

function save(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

const safe = (t: string) => (t || "document").replace(/[^\w\- ()]+/g, "_").slice(0, 80) || "document";

export function LiveBlock({ kind, initial, storeKey = "" }: { kind: LiveKind; initial: Data; storeKey?: string }) {
  const [data, setData] = useState<Data>(() => load(storeKey, initial));
  const [editing, setEditing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [ask, setAsk] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);
  const edited = JSON.stringify(data) !== JSON.stringify(initial);
  const Icon = ICON[kind];
  const title = titleOf(kind, data);

  useEffect(() => {
    if (!storeKey) return;
    try {
      if (edited) localStorage.setItem(storeKey, JSON.stringify(data));
      else localStorage.removeItem(storeKey);
    } catch { /* storage full: edits last this session */ }
  }, [data, edited, storeKey]);

  const values = useMemo(() => (kind === "sheet" ? evaluate(data as Sheet) : []), [kind, data]);

  const chartPng = async (): Promise<string> => {
    const svg = svgRef.current;
    if (!svg) return "";
    const xml = new XMLSerializer().serializeToString(svg);
    const img = new Image();
    const url = URL.createObjectURL(new Blob([xml], { type: "image/svg+xml" }));
    try {
      await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = url; });
      const vb = svg.viewBox.baseVal;
      const c = document.createElement("canvas");
      c.width = vb.width * 2;
      c.height = vb.height * 2;
      const g = c.getContext("2d")!;
      g.fillStyle = getComputedStyle(svg).getPropertyValue("--panel").trim() || "#ffffff";
      g.fillRect(0, 0, c.width, c.height);
      g.drawImage(img, 0, 0, c.width, c.height);
      return c.toDataURL("image/png");
    } catch { return ""; } finally { URL.revokeObjectURL(url); }
  };

  const exportAs = async (fmt: string) => {
    setErr("");
    setBusy(`Making the ${fmt.toUpperCase()}…`);
    try {
      const name = `${safe(title)}.${fmt}`;
      if (fmt === "csv") {
        const csv = kind === "sheet" ? sheetCsv(data as Sheet, values) : chartCsv(data as Chart);
        return save(new Blob(["\ufeff" + csv], { type: "text/csv" }), name);
      }
      const png = kind === "chart" ? await chartPng() : "";
      if (fmt === "png" && png) return save(await (await fetch(png)).blob(), name);
      const res = await fetch("/api/live/export", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Workbench": "1" },
        body: JSON.stringify({ kind, format: fmt, content: data, title, values: kind === "sheet" ? values : undefined, png }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Export failed (${res.status})`);
      save(await res.blob(), name);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };

  const revise = async () => {
    if (!ask.trim()) return;
    setErr("");
    setBusy("Changing it…");
    try {
      const r = await api<{ content: Data }>("/live/revise", { body: { kind, content: data, instruction: ask } });
      setData(r.content);
      setAsk("");
      setAsking(false);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(""); }
  };

  return (
    <figure className={`live live-${kind}`} aria-label={`${kind}: ${title}`}>
      <figcaption className="live-head">
        <span className="live-kind"><Icon size={13} /> {kind === "doc" ? "Document" : kind === "sheet" ? "Sheet" : "Chart"}</span>
        <strong className="live-title truncate">{title}</strong>
        {edited && <span className="live-edited">Edited</span>}
        <span className="grow" />
        <button className="btn ghost sm" onClick={() => setEditing((e) => !e)} aria-pressed={editing}>{editing ? <><Check size={13} /> Done</> : <><Pencil size={13} /> Edit</>}</button>
        <button className="btn ghost sm" onClick={() => setAsking((a) => !a)} aria-expanded={asking}><Sparkles size={13} /> Ask to change</button>
        {edited && <button className="btn ghost sm icon" title="Back to the original" aria-label="Back to the original" onClick={() => setData(initial)}><RotateCcw size={13} /></button>}
      </figcaption>

      {asking && (
        <form className="live-ask" onSubmit={(e) => { e.preventDefault(); void revise(); }}>
          <input className="input" autoFocus value={ask} onChange={(e) => setAsk(e.target.value)}
            placeholder={kind === "sheet" ? "e.g. add a row for pens, 3 at $1, and a total" : kind === "chart" ? "e.g. make it a line chart and add March" : "e.g. make it shorter and friendlier"} />
          <button className="btn primary sm" disabled={!ask.trim() || !!busy}>Change it</button>
        </form>
      )}

      <div className="live-body">
        {kind === "doc" && <DocView text={data as string} editing={editing} onChange={setData} />}
        {kind === "sheet" && <SheetView sheet={data as Sheet} values={values} editing={editing} onChange={setData} />}
        {kind === "chart" && <ChartView chart={data as Chart} editing={editing} onChange={setData} svgRef={svgRef} />}
      </div>

      <div className="live-foot">
        <span className="small muted"><Download size={12} /> Save as</span>
        {FORMATS[kind].map((f) => <button key={f} className="btn sm live-fmt" disabled={!!busy} onClick={() => void exportAs(f)}>.{f}</button>)}
        {busy && <span className="small muted row"><Loader2 size={13} className="spin" /> {busy}</span>}
        {err && <span className="small live-err" role="alert">{err}</span>}
      </div>
    </figure>
  );
}

function DocView({ text, editing, onChange }: { text: string; editing: boolean; onChange: (d: Data) => void }) {
  if (!editing) return <Markdown text={text} className="live-doc" />;
  return <textarea className="input live-doc-edit" value={text} onChange={(e) => onChange(e.target.value)} rows={Math.min(24, Math.max(6, text.split("\n").length + 1))} aria-label="Edit the document (Markdown)" />;
}

function SheetView({ sheet, values, editing, onChange }: { sheet: Sheet; values: (string | number)[][]; editing: boolean; onChange: (d: Data) => void }) {
  const [focus, setFocus] = useState<[number, number] | null>(null);
  const setCell = (r: number, c: number, v: string) => {
    const num = v.trim() !== "" && !v.startsWith("=") && !Number.isNaN(Number(v)) ? Number(v) : v;
    if (r < 0) onChange({ ...sheet, columns: sheet.columns.map((x, i) => (i === c ? v : x)) });
    else onChange({ ...sheet, rows: sheet.rows.map((row, i) => (i === r ? row.map((x, j) => (j === c ? num : x)) : row)) });
  };
  const addRow = () => onChange({ ...sheet, rows: [...sheet.rows, sheet.columns.map(() => "")] });
  const addCol = () => onChange({ ...sheet, columns: [...sheet.columns, `Column ${sheet.columns.length + 1}`], rows: sheet.rows.map((r) => [...r, ""]) });
  const raw = focus ? (focus[0] < 0 ? sheet.columns[focus[1]] : sheet.rows[focus[0]]?.[focus[1]]) : null;
  return (
    <div className="live-sheet-wrap">
      {editing && (
        <div className="live-formula small">
          <span className="live-ref">{focus ? `${colName(focus[1])}${focus[0] + 2}` : "—"}</span>
          <span className="muted truncate">{raw === null ? "Click a cell. Start with = for a formula, e.g. =SUM(B2:B5)" : String(raw ?? "")}</span>
        </div>
      )}
      <div className="live-scroll">
        <table className="live-sheet">
          <thead>
            {editing && <tr className="live-letters"><th aria-hidden="true" />{sheet.columns.map((_, c) => <th key={c} aria-hidden="true">{colName(c)}</th>)}</tr>}
            <tr>
              {editing && <th className="live-rownum" aria-hidden="true">1</th>}
              {sheet.columns.map((h, c) => <th key={c}>{editing ? <input value={h} onFocus={() => setFocus([-1, c])} onChange={(e) => setCell(-1, c, e.target.value)} aria-label={`Header ${colName(c)}`} /> : h}</th>)}
            </tr>
          </thead>
          <tbody>
            {sheet.rows.map((row, r) => (
              <tr key={r}>
                {editing && <th className="live-rownum" aria-hidden="true">{r + 2}</th>}
                {row.map((cell, c) => {
                  const v = values[r]?.[c] ?? "";
                  const isNum = typeof v === "number";
                  const isFormula = typeof cell === "string" && cell.startsWith("=");
                  return (
                    <td key={c} className={`${isNum ? "num" : ""} ${isFormula ? "formula" : ""} ${String(v).startsWith("#") ? "bad" : ""}`}>
                      {editing
                        ? <input value={focus && focus[0] === r && focus[1] === c ? String(cell ?? "") : String(isFormula ? fmtCell(v) : cell ?? "")}
                            onFocus={() => setFocus([r, c])} onBlur={() => setFocus(null)} onChange={(e) => setCell(r, c, e.target.value)}
                            aria-label={`Cell ${colName(c)}${r + 2}`} />
                        : fmtCell(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && <div className="row live-add"><button className="btn ghost sm" onClick={addRow}><Plus size={13} /> Row</button><button className="btn ghost sm" onClick={addCol}><Plus size={13} /> Column</button></div>}
    </div>
  );
}

function usePalette(): string[] {
  const [p, setP] = useState<string[]>([]);
  useEffect(() => {
    const read = () => {
      const s = getComputedStyle(document.documentElement);
      setP(["--accent", "--text", "--ok", "--warn", "--danger", "--muted"].map((v) => s.getPropertyValue(v).trim()).filter(Boolean));
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
    return () => mo.disconnect();
  }, []);
  return p.length ? p : ["#141311", "#c8102e", "#1f7a3f", "#b36b00", "#3355cc", "#777777"];
}

function ChartView({ chart, editing, onChange, svgRef }: { chart: Chart; editing: boolean; onChange: (d: Data) => void; svgRef: React.RefObject<SVGSVGElement> }) {
  const pal = usePalette();
  const style = getComputedStyle(document.documentElement);
  const ink = style.getPropertyValue("--text").trim() || "#141311";
  const rule = style.getPropertyValue("--border").trim() || "#dddddd";
  const font = style.getPropertyValue("--font").trim() || "sans-serif";
  const W = 560, H = 290, L = 44, T = 16, R = 12;
  const B = chart.series.length > 1 ? 52 : 34; // room for the legend under the labels
  const max = Math.max(0, ...chart.series.flatMap((s) => s.values));
  const ticks = niceScale(max);
  const top = ticks[ticks.length - 1] || 1;
  const y = (v: number) => T + (H - T - B) * (1 - v / top);
  const n = chart.labels.length;
  const band = (W - L - R) / Math.max(1, n);
  const ns = chart.series.length;

  const setVal = (si: number, li: number, v: string) => onChange({ ...chart, series: chart.series.map((s, i) => (i === si ? { ...s, values: chart.labels.map((_, j) => (j === li ? Number(v) || 0 : s.values[j] ?? 0)) } : s)) });
  const setLabel = (li: number, v: string) => onChange({ ...chart, labels: chart.labels.map((l, j) => (j === li ? v : l)) });

  return (
    <div className="live-chart">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${chart.type === "pie" ? 220 : H}`} role="img" aria-label={`${chart.type} chart: ${chart.title}`} xmlns="http://www.w3.org/2000/svg" fontFamily={font} fontSize="11">
        {chart.type === "pie" ? (
          <g>
            <g transform="translate(20 20)">{pieSlices(chart.series[0]?.values ?? []).map((s, i) => <path key={i} d={s.d} fill={pal[i % pal.length]} stroke="#fff" strokeWidth="1.5"><title>{`${chart.labels[i]}: ${chart.series[0].values[i]}`}</title></path>)}</g>
            {chart.labels.map((l, i) => (
              <g key={i} transform={`translate(220 ${30 + i * 20})`}><rect width="10" height="10" fill={pal[i % pal.length]} /><text x="16" y="9" fill={ink}>{`${l} · ${chart.series[0]?.values[i] ?? 0}`}</text></g>
            ))}
          </g>
        ) : (
          <g>
            {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke={rule} strokeWidth="1" /><text x={L - 6} y={y(t) + 4} textAnchor="end" fill={ink} opacity="0.7">{fmtCell(t)}</text></g>)}
            {chart.labels.map((l, i) => <text key={i} x={L + band * (i + 0.5)} y={H - B + 16} textAnchor="middle" fill={ink}>{l.length > 12 ? l.slice(0, 11) + "…" : l}</text>)}
            {chart.type === "bar"
              ? chart.series.map((s, si) => s.values.slice(0, n).map((v, i) => {
                  const w = (band * 0.7) / ns;
                  return <rect key={`${si}-${i}`} x={L + band * i + band * 0.15 + w * si} y={y(Math.max(0, v))} width={Math.max(1, w - 2)} height={Math.max(0, y(0) - y(Math.max(0, v)))} fill={pal[si % pal.length]}><title>{`${s.name}, ${chart.labels[i]}: ${v}`}</title></rect>;
                }))
              : chart.series.map((s, si) => (
                  <g key={si}>
                    <polyline fill="none" stroke={pal[si % pal.length]} strokeWidth="2.5" points={s.values.slice(0, n).map((v, i) => `${L + band * (i + 0.5)},${y(v)}`).join(" ")} />
                    {s.values.slice(0, n).map((v, i) => <circle key={i} cx={L + band * (i + 0.5)} cy={y(v)} r="3.5" fill={pal[si % pal.length]}><title>{`${s.name}, ${chart.labels[i]}: ${v}`}</title></circle>)}
                  </g>
                ))}
            {ns > 1 && chart.series.map((s, si) => <g key={si} transform={`translate(${L + si * 110} ${H - 10})`}><rect width="10" height="10" y="-9" fill={pal[si % pal.length]} /><text x="14" fill={ink}>{s.name}</text></g>)}
          </g>
        )}
      </svg>
      {editing && (
        <div className="live-chart-edit">
          <label className="small row">Type
            <select className="input sm" value={chart.type} onChange={(e) => onChange({ ...chart, type: e.target.value as Chart["type"] })}>
              <option value="bar">Bars</option><option value="line">Line</option><option value="pie">Pie</option>
            </select>
          </label>
          <div className="live-scroll">
            <table className="live-sheet">
              <thead><tr><th>Label</th>{chart.series.map((s, si) => <th key={si}><input value={s.name} aria-label="Series name" onChange={(e) => onChange({ ...chart, series: chart.series.map((x, i) => (i === si ? { ...x, name: e.target.value } : x)) })} /></th>)}</tr></thead>
              <tbody>{chart.labels.map((l, li) => (
                <tr key={li}><td><input value={l} aria-label="Label" onChange={(e) => setLabel(li, e.target.value)} /></td>
                  {chart.series.map((s, si) => <td key={si} className="num"><input inputMode="decimal" value={String(s.values[li] ?? 0)} aria-label={`${s.name} for ${l}`} onChange={(e) => setVal(si, li, e.target.value)} /></td>)}</tr>
              ))}</tbody>
            </table>
          </div>
          <div className="row live-add">
            <button className="btn ghost sm" onClick={() => onChange({ ...chart, labels: [...chart.labels, `Item ${n + 1}`], series: chart.series.map((s) => ({ ...s, values: [...s.values, 0] })) })}><Plus size={13} /> Label</button>
            {chart.type !== "pie" && <button className="btn ghost sm" onClick={() => onChange({ ...chart, series: [...chart.series, { name: `Series ${ns + 1}`, values: chart.labels.map(() => 0) }] })}><Plus size={13} /> Series</button>}
          </div>
        </div>
      )}
    </div>
  );
}
