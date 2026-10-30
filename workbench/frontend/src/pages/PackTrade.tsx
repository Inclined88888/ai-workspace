// Trade pack: site photo or voice note → itemised quote → invoice → reminder drafts (feat/v02-packs).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Mic, Plus, Square, Trash2 } from "lucide-react";
import { api } from "../api";
import { type Line, type TradeSettings, abnValid, money, previewTotals, statusLabel } from "../packs/logic";
import { FilePick, PackGate, copy, download, useFiles } from "../packs/shared";
import { useToast } from "../util";
import "../packs/packs.css";

type Settings = TradeSettings & Record<string, string | number | boolean | null>;
type Doc = {
  id: number; kind: "quote" | "invoice"; number: string; title: string; status: string; customer: Record<string, string>;
  items: Line[]; totals: { total: number; tax: number; subtotal_ex_tax: number; labour: number; materials: number; other: number; tax_name: string };
  due_on: string; issued_on: string; overdue_days: number; questions?: string[]; photo_note?: string; pushed: Record<string, { ref: string; at: string }>;
};
type Draft = { id: number; tone: string; to: string; subject: string; body: string; send_on?: string };
type PushStatus = Record<string, { name: string; connected: boolean; fields: string[]; how: string }>;

type SpeechCtor = new () => { lang: string; continuous: boolean; interimResults: boolean; start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null; onend: (() => void) | null };

function useDictation(onText: (t: string) => void) {
  const Ctor = (window as unknown as { SpeechRecognition?: SpeechCtor; webkitSpeechRecognition?: SpeechCtor }).SpeechRecognition
    ?? (window as unknown as { webkitSpeechRecognition?: SpeechCtor }).webkitSpeechRecognition;
  const rec = useRef<InstanceType<SpeechCtor> | null>(null);
  const [on, setOn] = useState(false);
  const start = () => {
    if (!Ctor) return;
    const r = new Ctor();
    r.lang = navigator.language || "en-AU"; r.continuous = true; r.interimResults = false;
    r.onresult = (e) => {
      const parts: string[] = [];
      for (let i = 0; i < e.results.length; i++) if (e.results[i].isFinal) parts.push(e.results[i][0].transcript);
      onText(parts.join(" "));
    };
    r.onend = () => setOn(false);
    rec.current = r; r.start(); setOn(true);
  };
  return { supported: !!Ctor, on, start, stop: () => rec.current?.stop() };
}

export default function PackTrade() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"work" | "settings">(params.get("tab") === "settings" ? "settings" : "work");
  const [gate, setGate] = useState<unknown>(null);
  const [s, setS] = useState<Settings | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const toast = useToast();
  const sel = Number(params.get("doc")) || null;

  const load = useCallback(async () => {
    try {
      const [st, ds] = await Promise.all([api<Settings>("/packs/trade/settings"), api<Doc[]>("/packs/trade/docs")]);
      setS(st); setDocs(ds); setGate(null);
    } catch (e) { setGate(e); }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (gate) return <div className="page"><PackGate error={gate} packId="trade" name="Trade pack" /></div>;
  if (!s) return <div className="page"><p className="muted">Loading…</p></div>;
  const pick = (id: number | null) => setParams(id ? { doc: String(id) } : {});

  return (
    <div className="page wide" style={{ maxWidth: 1240 }}>
      <div className="page-head">
        <div>
          <p className="kicker">Trade pack</p>
          <h1>Quotes &amp; invoices</h1>
          <p className="muted">From a site photo, a voice note or a few lines to a priced quote, then an invoice. Nothing is sent for you.</p>
        </div>
        <div className="tabs" role="tablist" style={{ marginLeft: "auto" }}>
          <button role="tab" aria-selected={tab === "work"} className={`tab ${tab === "work" ? "active" : ""}`} onClick={() => setTab("work")}>Quotes &amp; invoices</button>
          <button role="tab" aria-selected={tab === "settings"} className={`tab ${tab === "settings" ? "active" : ""}`} onClick={() => setTab("settings")}>Your business</button>
        </div>
      </div>
      {tab === "settings" ? <SettingsForm s={s} onSaved={(x) => { setS(x); toast.ok("Saved"); }} onErr={toast.err} /> : (
        <div className={`pk-two ${sel ? "open" : ""}`}>
          <div className="pk-stack">
            <NewQuote s={s} onMade={(d) => { load(); pick(d.id); toast.ok(`Quote ${d.number} drafted`); }} onErr={toast.err} />
            <div className="card">
              <div className="card-head"><h2>Your quotes and invoices</h2></div>
              {!docs.length ? <p className="muted">Nothing yet. Draft your first quote above.</p> : (
                <div className="pk-list">
                  {docs.map((d) => (
                    <button key={d.id} className={`pk-item ${sel === d.id ? "active" : ""}`} onClick={() => pick(d.id)}>
                      <span className="badge">{d.kind === "quote" ? "Quote" : "Invoice"}</span>
                      <span className="mono" style={{ fontSize: 12 }}>{d.number}</span>
                      <span className="grow">{d.customer.name || "No customer"} · {d.title}</span>
                      <span className={`badge ${d.overdue_days > 0 && d.status !== "paid" ? "warn" : d.status === "paid" ? "ok" : ""}`}>{statusLabel(d.kind, d.status, d.overdue_days)}</span>
                      <span className="num">{money(d.totals.total, s.currency_symbol)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          {sel ? <DocEditor key={sel} id={sel} s={s} onChanged={load} onOpen={pick} toast={toast} /> : (
            <div className="card"><p className="muted">Pick a quote or invoice to edit it, make the PDF or write reminders.</p></div>
          )}
        </div>
      )}
      {toast.node}
    </div>
  );
}

function NewQuote({ s, onMade, onErr }: { s: Settings; onMade: (d: Doc) => void; onErr: (e: unknown) => void }) {
  const [desc, setDesc] = useState("");
  const [transcript, setTranscript] = useState("");
  const [photo, setPhoto] = useState<number | null>(null);
  const [cust, setCust] = useState({ name: "", email: "", phone: "", address: "" });
  const [busy, setBusy] = useState(false);
  const { files, reload } = useFiles();
  const dict = useDictation((t) => setTranscript(t));
  const go = async () => {
    setBusy(true);
    try { onMade(await api<Doc>("/packs/trade/quotes/draft", { body: { description: desc, transcript, photo_file_id: photo, customer: cust } })); setDesc(""); setTranscript(""); setPhoto(null); }
    catch (e) { onErr(e); }
    setBusy(false);
  };
  return (
    <div className="card pk-stack">
      <div className="card-head"><h2>New quote</h2></div>
      <textarea className="input" rows={4} value={desc} onChange={(e) => setDesc(e.target.value)} aria-label="Describe the job"
        placeholder={"Describe the job, or list lines like:\nReplace switchboard\n3 x downlights @ 45\n2 hours labour"} />
      <div className="row wrap">
        {dict.supported ? (
          <button className={`btn ${dict.on ? "pk-rec" : ""}`} onClick={dict.on ? dict.stop : dict.start}>
            {dict.on ? <><Square size={14} /> Stop voice note</> : <><Mic size={14} /> Record a voice note</>}
          </button>
        ) : <span className="muted" style={{ fontSize: 12 }}>Voice notes need Chrome, Edge or Safari. You can paste a transcript instead.</span>}
      </div>
      {(transcript || !dict.supported) && <textarea className="input" rows={2} value={transcript} onChange={(e) => setTranscript(e.target.value)} placeholder="Voice note transcript" aria-label="Voice note transcript" />}
      <FilePick label="Site photo (optional)" accept="image/*" value={photo} onChange={setPhoto} files={files.filter((f) => /\.(jpe?g|png|heic|webp)$/i.test(f.name))} onAdded={reload} />
      <div className="grid two">
        <input className="input" placeholder="Customer name" value={cust.name} onChange={(e) => setCust({ ...cust, name: e.target.value })} />
        <input className="input" placeholder="Customer email" value={cust.email} onChange={(e) => setCust({ ...cust, email: e.target.value })} />
        <input className="input" placeholder="Phone" value={cust.phone} onChange={(e) => setCust({ ...cust, phone: e.target.value })} />
        <input className="input" placeholder="Site address" value={cust.address} onChange={(e) => setCust({ ...cust, address: e.target.value })} />
      </div>
      <div className="row">
        <button className="btn primary" disabled={busy || (!desc.trim() && !transcript.trim() && !photo)} onClick={go}>{busy ? "Pricing…" : "Draft quote"}</button>
        <span className="muted" style={{ fontSize: 12 }}>Labour {money(Number(s.labour_rate), s.currency_symbol)}/{String(s.labour_unit)} · materials +{s.materials_markup}% · {s.tax_registered ? `${s.tax_name} ${s.tax_rate}%` : `no ${s.tax_name}`}</span>
      </div>
    </div>
  );
}

function DocEditor({ id, s, onChanged, onOpen, toast }: { id: number; s: Settings; onChanged: () => void; onOpen: (id: number) => void; toast: ReturnType<typeof useToast> }) {
  const [d, setD] = useState<Doc | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [drafts, setDrafts] = useState<{ drafts: Draft[]; suggested: string; note: string } | null>(null);
  const [push, setPush] = useState<{ target: string; connected: boolean; would_send: unknown; approval_code?: string; how?: string; note: string } | null>(null);
  const [busy, setBusy] = useState("");
  useEffect(() => { api<Doc>(`/packs/trade/docs/${id}`).then((x) => { setD(x); setLines(x.items); }).catch(toast.err); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const live = useMemo(() => previewTotals(lines, s), [lines, s]);
  if (!d) return <div className="card"><p className="muted">Loading…</p></div>;
  const dirty = JSON.stringify(lines) !== JSON.stringify(d.items);
  const run = async <T,>(name: string, f: () => Promise<T>) => { setBusy(name); try { return await f(); } catch (e) { toast.err(e); } finally { setBusy(""); } };
  const set = (i: number, k: keyof Line, v: string | number) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v, estimate: k === "unit_cost" || k === "qty" ? false : l.estimate } : l)));
  const save = (extra: Record<string, unknown> = {}) => run("save", async () => {
    const x = await api<Doc>(`/packs/trade/docs/${id}`, { method: "PUT", body: { items: lines, ...extra } });
    setD(x); setLines(x.items); onChanged(); toast.ok("Saved");
  });
  return (
    <div className="card pk-stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <p className="kicker" style={{ margin: 0 }}>{d.kind === "quote" ? "Quote" : "Invoice"} {d.number}</p>
          <h2 className="pk-serif">{d.title}</h2>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>{d.customer.name || "No customer"} · issued {d.issued_on} · {d.kind === "quote" ? "valid until" : "due"} {d.due_on}</p>
        </div>
        <span className="badge">{statusLabel(d.kind, d.status, d.overdue_days)}</span>
      </div>
      {!!d.questions?.length && <div className="pk-notice"><strong>Check before sending:</strong> {d.questions.join(" ")}</div>}
      {d.photo_note && <p className="pk-err">{d.photo_note}</p>}
      <table className="pk-lines">
        <thead><tr><th>Type</th><th>Description</th><th className="num">Qty</th><th>Unit</th><th className="num">Cost</th><th /></tr></thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td><select className="input" style={{ width: 112 }} value={l.kind} onChange={(e) => set(i, "kind", e.target.value)} aria-label="Type">
                <option value="labour">Labour</option><option value="materials">Materials</option><option value="other">Other</option></select></td>
              <td style={{ width: "100%" }}><input className="input" style={{ width: "100%", minWidth: 160 }} value={l.description} onChange={(e) => set(i, "description", e.target.value)} aria-label="Description" />
                {l.estimate && <span className="badge warn" style={{ marginTop: 2 }}>estimate</span>}</td>
              <td><input className="input num" style={{ width: 64 }} type="number" step="any" value={l.qty} onChange={(e) => set(i, "qty", Number(e.target.value))} aria-label="Quantity" /></td>
              <td><input className="input" style={{ width: 70 }} value={l.unit} onChange={(e) => set(i, "unit", e.target.value)} aria-label="Unit" /></td>
              <td><input className="input num" style={{ width: 90 }} type="number" step="any" value={l.unit_cost} onChange={(e) => set(i, "unit_cost", Number(e.target.value))} aria-label="Unit cost" /></td>
              <td><button className="btn ghost icon sm" aria-label="Remove line" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={13} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button className="btn sm" onClick={() => setLines([...lines, { kind: "materials", description: "", qty: 1, unit: "each", unit_cost: 0 }])}><Plus size={12} /> Add line</button></div>
      <div className="pk-totals">
        <span className="muted">Labour</span><span>{money(live.labour, s.currency_symbol)}</span>
        <span className="muted">Materials (incl. markup)</span><span>{money(live.materials, s.currency_symbol)}</span>
        {live.other > 0 && <><span className="muted">Other</span><span>{money(live.other, s.currency_symbol)}</span></>}
        <span className="muted">Subtotal{s.tax_registered ? ` ex ${s.tax_name}` : ""}</span><span>{money(live.subtotal_ex_tax, s.currency_symbol)}</span>
        {s.tax_registered && <><span className="muted">{s.tax_name}{s.prices_include_tax ? " (included)" : ""}</span><span>{money(live.tax, s.currency_symbol)}</span></>}
        <span className="grand">Total</span><span className="grand">{money(live.total, s.currency_symbol)}</span>
      </div>
      <div className="row wrap">
        <button className="btn primary" disabled={!dirty || busy === "save"} onClick={() => save()}>Save</button>
        <button className="btn" disabled={dirty || !!busy} onClick={() => run("pdf", async () => { const f = await api<{ download: string }>(`/packs/trade/docs/${id}/pdf`, { method: "POST" }); download(f.download); })}>{busy === "pdf" ? "Making…" : "PDF"}</button>
        {d.kind === "quote" && <button className="btn" disabled={dirty || !!busy} onClick={() => run("inv", async () => { const x = await api<Doc>(`/packs/trade/docs/${id}/invoice`, { method: "POST" }); onChanged(); onOpen(x.id); toast.ok(`Invoice ${x.number} made`); })}>Turn into invoice</button>}
        {d.kind === "invoice" && <button className="btn" disabled={!!busy} onClick={() => run("rem", async () => setDrafts(await api(`/packs/trade/docs/${id}/reminders`, { method: "POST" })))}>Payment reminders</button>}
        {d.kind === "invoice" && d.status !== "paid" && <button className="btn ghost" onClick={() => save({ status: "paid" })}>Mark paid</button>}
        {d.status === "draft" && <button className="btn ghost" onClick={() => save({ status: "sent" })}>I've sent it</button>}
        <span style={{ flex: 1 }} />
        {["servicem8", "xero"].map((t) => <button key={t} className="btn ghost sm" disabled={dirty || !!busy}
          onClick={() => run("push", async () => setPush(await api(`/packs/trade/push/${t}/preview`, { body: { document_id: id } })))}>{t === "xero" ? "Xero…" : "ServiceM8…"}</button>)}
      </div>
      {drafts && (
        <div className="pk-stack">
          <p className="pk-notice">{drafts.note}</p>
          {drafts.drafts.map((r) => (
            <div key={r.id} className="pk-draft">
              <div className="row"><span className={`badge ${r.tone === drafts.suggested ? "accent" : ""}`}>{r.tone}{r.tone === drafts.suggested ? " · suggested" : ""}</span><strong style={{ fontSize: 13 }}>{r.subject}</strong>{r.send_on && <span className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>send from {r.send_on}</span>}</div>
              <pre>{r.body}</pre>
              <div className="row">
                <button className="btn sm" onClick={() => { copy(`Subject: ${r.subject}\n\n${r.body}`); toast.ok("Copied"); }}>Copy</button>
                {r.to && <a className="btn sm ghost" href={`mailto:${encodeURIComponent(r.to)}?subject=${encodeURIComponent(r.subject)}&body=${encodeURIComponent(r.body)}`}>Open in my email</a>}
              </div>
            </div>
          ))}
        </div>
      )}
      {push && (
        <div className="pk-draft">
          <strong>{push.connected ? `Send to ${push.target}?` : `${push.target} is not connected`}</strong>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>{push.note}</p>
          {!push.connected && push.how && <p style={{ margin: 0, fontSize: 12 }}>{push.how}</p>}
          <details><summary className="muted" style={{ fontSize: 12 }}>Exactly what would be sent</summary><pre className="pk-code">{JSON.stringify(push.would_send, null, 2)}</pre></details>
          <div className="row">
            {push.connected && push.approval_code && <button className="btn primary" disabled={busy === "send"} onClick={() => run("send", async () => {
              const t = push.target.toLowerCase().includes("xero") ? "xero" : "servicem8";
              await api(`/packs/trade/push/${t}/send`, { body: { document_id: id, approval_code: push.approval_code } });
              setPush(null); toast.ok(`Sent to ${push.target} as a draft`);
            })}>Send to {push.target}</button>}
            <button className="btn ghost" onClick={() => setPush(null)}>{push.connected ? "Cancel" : "Close"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

const FIELDS: [string, string, "text" | "number" | "bool"][] = [
  ["business_name", "Business name", "text"], ["abn", "ABN", "text"], ["address", "Address", "text"], ["phone", "Phone", "text"], ["email", "Email", "text"],
  ["labour_rate", "Labour rate", "number"], ["labour_unit", "Labour unit (hour, day…)", "text"], ["callout_fee", "Call-out fee (0 for none)", "number"],
  ["materials_markup", "Materials markup %", "number"], ["labour_markup", "Labour markup %", "number"],
  ["tax_registered", "Registered for GST", "bool"], ["tax_name", "Tax name", "text"], ["tax_rate", "Tax rate %", "number"], ["prices_include_tax", "My prices include GST", "bool"],
  ["currency", "Currency", "text"], ["currency_symbol", "Currency symbol", "text"], ["quote_valid_days", "Quotes valid for (days)", "number"],
  ["payment_terms_days", "Invoices due in (days)", "number"], ["quote_prefix", "Quote number prefix", "text"], ["invoice_prefix", "Invoice number prefix", "text"],
];

function SettingsForm({ s, onSaved, onErr }: { s: Settings; onSaved: (s: Settings) => void; onErr: (e: unknown) => void }) {
  const [v, setV] = useState<Settings>(s);
  const [push, setPush] = useState<PushStatus | null>(null);
  const [keys, setKeys] = useState<Record<string, Record<string, string>>>({});
  const { files, reload } = useFiles();
  useEffect(() => { api<PushStatus>("/packs/trade/push").then(setPush).catch(onErr); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const abn = String(v.abn || "");
  return (
    <div className="pk-two">
      <div className="card pk-stack">
        <div className="card-head"><h2>Your business</h2></div>
        <div className="grid two">
          {FIELDS.map(([k, label, kind]) => kind === "bool" ? (
            <label key={k} className="row" style={{ gap: 8 }}><input type="checkbox" checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} /> {label}</label>
          ) : (
            <label key={k} className="pk-stack" style={{ gap: 4 }}><span className="label" style={{ margin: 0 }}>{label}</span>
              <input className="input" type={kind} step="any" value={String(v[k] ?? "")} onChange={(e) => setV({ ...v, [k]: kind === "number" ? Number(e.target.value) : e.target.value })} />
              {k === "abn" && abn && !abnValid(abn) && <span className="pk-err">That ABN doesn't pass the check. Look it up at abr.business.gov.au.</span>}
            </label>
          ))}
        </div>
        <label className="pk-stack" style={{ gap: 4 }}><span className="label" style={{ margin: 0 }}>How to pay (on invoices and reminders)</span>
          <textarea className="input" rows={3} value={String(v.payment_details ?? "")} onChange={(e) => setV({ ...v, payment_details: e.target.value })} placeholder="BSB 000-000, Account 1234 5678, or a payment link" /></label>
        <div className="pk-stack" style={{ gap: 4 }}><span className="label" style={{ margin: 0 }}>Logo</span>
          <FilePick label="No logo" accept="image/png,image/jpeg" value={(v.logo_file_id as number) || null} onChange={(id) => setV({ ...v, logo_file_id: id })} files={files.filter((f) => /\.(png|jpe?g)$/i.test(f.name))} onAdded={reload} /></div>
        <div className="row"><button className="btn primary" onClick={async () => { try { onSaved(await api<Settings>("/packs/trade/settings", { method: "PUT", body: v })); } catch (e) { onErr(e); } }}>Save</button></div>
      </div>
      <div className="card pk-stack">
        <div className="card-head"><h2>ServiceM8 and Xero (optional)</h2></div>
        <p className="muted" style={{ margin: 0 }}>Only if you use them. Your keys stay encrypted on this computer, and nothing is sent until you check the preview and press Send each time.</p>
        {push && Object.entries(push).map(([t, p]) => (
          <div key={t} className="pk-draft">
            <div className="row"><strong>{p.name}</strong><span className={`badge ${p.connected ? "ok" : ""}`}>{p.connected ? "Connected" : "Not connected"}</span></div>
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>{p.how}</p>
            {p.fields.map((f) => <input key={f} className="input mono" type="password" autoComplete="off" placeholder={p.connected ? "•••••••• saved" : f.replace("_", " ")}
              value={keys[t]?.[f] ?? ""} onChange={(e) => setKeys({ ...keys, [t]: { ...keys[t], [f]: e.target.value } })} aria-label={`${p.name} ${f}`} />)}
            <div className="row">
              <button className="btn sm" onClick={async () => { try { setPush(await api<PushStatus>(`/packs/trade/push/${t}`, { method: "PUT", body: keys[t] ?? {} })); setKeys({ ...keys, [t]: {} }); } catch (e) { onErr(e); } }}>Save</button>
              {p.connected && <button className="btn sm ghost danger" onClick={async () => setPush(await api<PushStatus>(`/packs/trade/push/${t}`, { method: "PUT", body: {} }))}>Disconnect</button>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
