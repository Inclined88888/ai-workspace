import { AlertTriangle, BellRing, CalendarPlus, Check, Download, ExternalLink, FileDown, Globe, KeyRound, ListChecks, Loader2, LogIn, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime, useToast } from "../util";
import "../work.css";

type Watch = { id: number; request: string; cron: string; in_words?: string; next_run: string | null; last_run: string | null; last_result?: string };
type Form = { name: string; submit: string; page_url: string; fields: { name: string; label: string; type: string; options: { value: string; label: string }[]; value: string }[] };
type Site = { id: number; name: string; url: string; mode: string; recipe: string; status: string; status_detail: string; last_checked: string | null;
  has_key: boolean; records?: number; last_digest?: string; recipe_info: { label?: string; help?: string; kind?: string };
  structure: { collections?: { name: string; entity: string; count: number; roles: Record<string, string> }[]; forms?: Form[]; files?: { url: string; name: string }[];
    navigation?: { text: string; url: string }[] };
  checks?: { id: number; at: string; status: string; digest: string }[]; watches?: Watch[] };
type Rec = { id: number; collection: string; title: string; status: string; start: string; due: string; finish: string; person: string; location: string;
  amount: string; url: string; overdue: boolean; done: boolean };
type Item = { id: number; title: string; start: string; end: string; status: string; overdue: boolean; done: boolean; collection: string };
type Action = { id: number; site_id: number; summary: string; status: string; result: string; created_at: string };

const day = (s: string) => new Date(s.slice(0, 10) + "T00:00:00").getTime();
const short = (s: string) => (s ? new Date(s.length > 10 ? s : s + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" }) + (s.length > 10 ? " " + s.slice(11, 16) : "") : "");

export default function Sites() {
  const [sites, setSites] = useState<Site[]>([]);
  const [browser, setBrowser] = useState(false);
  const [sel, setSel] = useState<Site | null>(null);
  const [recs, setRecs] = useState<Rec[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [actions, setActions] = useState<Action[]>([]);
  const [tab, setTab] = useState<"overview" | "records" | "schedule" | "do">("overview");
  const [busy, setBusy] = useState("");
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [watchText, setWatchText] = useState("");
  const [key, setKey] = useState("");
  const [coll, setColl] = useState("");
  const [formIdx, setFormIdx] = useState(-1);
  const [vals, setVals] = useState<Record<string, string>>({});
  const toast = useToast();
  const navigate = useNavigate();

  const load = useCallback(async () => {
    const r = await api<{ sites: Site[]; browser: boolean }>("/sites");
    setSites(r.sites); setBrowser(r.browser);
    setActions(await api<Action[]>("/site-actions"));
    return r.sites;
  }, []);
  const open = useCallback(async (id: number) => {
    const [s, r, t] = await Promise.all([api<Site>(`/sites/${id}`), api<Rec[]>(`/sites/${id}/records`), api<Item[]>(`/sites/${id}/timeline`)]);
    setSel(s); setRecs(r); setItems(t);
  }, []);
  useEffect(() => { load().then((s) => s[0] && open(s[0].id)).catch(toast.err); }, [load, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (label: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(label);
    try { const r = await fn(); if (ok) toast.ok(ok); await load(); if (sel) await open(sel.id); return r; }
    catch (e) { toast.err(e); } finally { setBusy(""); }
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setBusy("Adding…");
    try {
      const s = await api<Site>("/sites", { body: { url, name } });
      setAdding(false); setUrl(""); setName("");
      await load(); await open(s.id);
      if (!s.recipe_info?.kind || s.recipe_info.kind === "browser") {
        setBusy("Reading the site for the first time…");
        await api(`/sites/${s.id}/check`, { method: "POST" }).catch(() => undefined);
        await load(); await open(s.id);
      }
    } catch (err) { toast.err(err); } finally { setBusy(""); }
  };

  const collections = sel?.structure.collections ?? [];
  const forms = sel?.structure.forms ?? [];
  const shown = coll ? recs.filter((r) => r.collection === coll) : recs;
  const overdue = recs.filter((r) => r.overdue);
  const pending = actions.filter((a) => a.status === "pending" && a.site_id === sel?.id);
  const form = formIdx >= 0 ? forms[formIdx] : null;

  return (
    <div className="page work">
      {toast.node}
      <div className="page-head">
        <div><h1>Sites</h1><p className="muted">The websites you work in: job trackers, rosters, project portals, anything with a login. Sign in once, and the assistant reads them, plans your days and tells you what changed. It never changes anything on a site without asking you first.</p></div>
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={15} /> Add a site</button>
      </div>

      {adding && (
        <form className="card col gap" onSubmit={add} style={{ marginBottom: 16 }}>
          <h3>Add a site</h3>
          <label className="col small">Web address<input className="input" autoFocus value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste it from your browser, e.g. portal.mybuilder.com.au/jobs" /></label>
          <label className="col small">Name (optional)<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Builder portal" /></label>
          <div className="row"><button className="btn primary" disabled={url.trim().length < 4}>Add and read it</button><button type="button" className="btn ghost" onClick={() => setAdding(false)}>Cancel</button></div>
          {!browser && <div className="small muted">Sites that need a sign-in are read through Rooks Browser, which starts with the desktop app.</div>}
        </form>
      )}
      {busy && <div className="row muted work-busy"><Loader2 size={15} className="spin" /> {busy}</div>}

      <div className="work-layout">
        <div className="card flush work-list">
          {!sites.length && <div className="pad"><Empty icon={<Globe size={22} />} title="No sites yet" hint="Add the address of a site you use for work." /></div>}
          {sites.map((s) => (
            <div key={s.id} className={`work-item ${sel?.id === s.id ? "on" : ""}`} onClick={() => { setTab("overview"); setColl(""); open(s.id); }}>
              <div className="grow">
                <div className="truncate">{s.name}</div>
                <div className="small faint truncate">{s.status === "needs_sign_in" ? "Needs you to sign in" : s.status === "error" ? "Could not read" : s.last_checked ? `${s.records ?? 0} items · checked ${relTime(s.last_checked)}` : "Not read yet"}</div>
              </div>
              <span className={`dot ${s.status}`} aria-hidden="true" />
            </div>
          ))}
        </div>

        <div className="work-main">
          {!sel && <div className="card"><Empty icon={<Globe size={24} />} title="Pick or add a site" /></div>}
          {sel && (
            <div className="card col gap-lg">
              <div className="row wrap" style={{ alignItems: "flex-start" }}>
                <div className="grow">
                  <h2>{sel.name}</h2>
                  <a className="small muted row" href={sel.url} target="_blank" rel="noreferrer noopener">{sel.url} <ExternalLink size={11} /></a>
                </div>
                {(sel.mode !== "api") && <button className="btn sm" disabled={!browser} title={browser ? "" : "Rooks Browser is not running"} onClick={() => act("Opening the browser…", () => api(`/sites/${sel.id}/sign-in`, { method: "POST" }), "Sign in in the window that opened, then press Check now")}><LogIn size={14} /> Sign in once</button>}
                <button className="btn primary sm" onClick={() => act("Reading the site…", () => api(`/sites/${sel.id}/check`, { method: "POST" }))}><RefreshCw size={14} /> Check now</button>
                <button className="btn ghost sm icon" aria-label="Remove site" onClick={() => confirm(`Remove ${sel.name}? Your sign-in in the browser stays.`) && act("Removing…", () => api(`/sites/${sel.id}`, { method: "DELETE" })).then(() => setSel(null))}><Trash2 size={14} /></button>
              </div>

              {sel.status === "needs_sign_in" && <div className="alert warn">{sel.status_detail}</div>}
              {sel.status === "error" && <div className="alert err">{sel.status_detail}</div>}
              {sel.recipe_info?.help && (
                <div className="alert info small col">
                  <div><strong>{sel.recipe_info.label}.</strong> {sel.recipe_info.help}</div>
                  {sel.recipe_info.kind === "api" && !sel.has_key && <div className="row"><input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste the API key" autoComplete="off" />
                    <button className="btn sm" disabled={key.length < 8} onClick={() => act("Saving the key…", () => api(`/sites/${sel.id}/key`, { body: { api_key: key } }), "Key saved in your computer's keychain").then(() => setKey(""))}><KeyRound size={14} /> Save key</button></div>}
                  {sel.has_key && <div className="row"><Check size={14} /> API key saved securely.</div>}
                </div>
              )}

              <div className="tabs">
                <button className={`tab ${tab === "overview" ? "active" : ""}`} onClick={() => setTab("overview")}><BellRing size={14} /> What's new</button>
                <button className={`tab ${tab === "records" ? "active" : ""}`} onClick={() => setTab("records")}><ListChecks size={14} /> Items {recs.length ? `(${recs.length})` : ""}</button>
                <button className={`tab ${tab === "schedule" ? "active" : ""}`} onClick={() => setTab("schedule")}><CalendarPlus size={14} /> Schedule</button>
                <button className={`tab ${tab === "do" ? "active" : ""}`} onClick={() => setTab("do")}>Make a change {pending.length ? <span className="badge">{pending.length}</span> : null}</button>
              </div>

              {tab === "overview" && (
                <div className="col gap">
                  {overdue.length > 0 && <div className="alert warn"><AlertTriangle size={14} /> {overdue.length} overdue: {overdue.slice(0, 4).map((r) => r.title).join(", ")}{overdue.length > 4 ? "…" : ""}</div>}
                  {sel.checks?.[0] ? <div className="work-digest">{sel.checks[0].digest.split("\n").map((l, i) => <p key={i}>{l}</p>)}<div className="small faint">Checked {relTime(sel.checks[0].at)}</div></div>
                    : <Empty title="Not read yet" hint="Press Check now." />}
                  {collections.length > 0 && <div className="small muted">Found on this site: {collections.map((c) => `${c.count} ${c.name.toLowerCase()}`).join(" · ")}{sel.structure.files?.length ? ` · ${sel.structure.files.length} files` : ""}{forms.length ? ` · ${forms.length} forms` : ""}</div>}
                  <div className="col">
                    <h3>Keep an eye on it</h3>
                    <form className="row" onSubmit={(e) => { e.preventDefault(); act("Setting it up…", () => api("/sites/watches", { body: { request: watchText, site_id: sel.id } }), "Done. Results arrive in your inbox").then(() => setWatchText("")); }}>
                      <input className="input grow" value={watchText} onChange={(e) => setWatchText(e.target.value)} placeholder="e.g. check every morning at 7 and tell me which jobs are overdue" />
                      <button className="btn sm" disabled={watchText.trim().length < 5}>Set up</button>
                    </form>
                    {(sel.watches ?? []).map((w) => (
                      <div key={w.id} className="row small work-watch">
                        <BellRing size={13} /><span className="grow">“{w.request}” · <span className="muted">{w.in_words}</span></span>
                        <button className="btn ghost sm" onClick={() => act("Checking…", () => api(`/sites/watches/${w.id}/run`, { method: "POST" }), "Sent to your inbox")}>Run now</button>
                        <button className="btn ghost sm icon" aria-label="Stop watching" onClick={() => act("", () => api(`/sites/watches/${w.id}`, { method: "DELETE" }))}><X size={13} /></button>
                      </div>
                    ))}
                    <div className="small faint">Checks only read the site. Anything that would change it waits for you.</div>
                  </div>
                  {(sel.structure.files ?? []).length > 0 && <div className="col"><h3>Files on the site</h3>{sel.structure.files!.slice(0, 30).map((f) => (
                    <div key={f.url} className="row small"><span className="grow truncate">{f.name}</span>
                      <button className="btn ghost sm" onClick={() => act("Fetching…", () => api(`/sites/${sel.id}/fetch-file`, { body: { url: f.url } }), "Added to Files").then(() => navigate("/files"))}><FileDown size={13} /> Read it</button></div>))}</div>}
                </div>
              )}

              {tab === "records" && (
                <div className="col gap">
                  <div className="row wrap">
                    {collections.length > 1 && <select className="input" style={{ width: 220 }} value={coll} onChange={(e) => setColl(e.target.value)}><option value="">Everything</option>{collections.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.count})</option>)}</select>}
                    <div className="spacer" />
                    <button className="btn sm" onClick={() => act("Making the spreadsheet…", () => api<{ download: string }>(`/sites/${sel.id}/export`, { method: "POST" }).then((r) => window.open(r.download, "_blank")))}><Download size={14} /> Excel</button>
                    <button className="btn sm" onClick={() => navigate("/assistant", { state: { draft: `Looking at ${sel.name} (site ${sel.id}): ` } })}>Ask about these</button>
                  </div>
                  <div className="work-table-wrap"><table className="work-table"><thead><tr><th>Item</th><th>Status</th><th>Starts</th><th>Due</th><th>Who</th><th>Where</th></tr></thead>
                    <tbody>{shown.slice(0, 400).map((r) => <tr key={r.id} className={r.overdue ? "late" : r.done ? "done" : ""}>
                      <td><a href={r.url} target="_blank" rel="noreferrer noopener">{r.title}</a>{collections.length > 1 && !coll ? <div className="small faint">{r.collection}</div> : null}</td>
                      <td>{r.status && <span className={`chip ${r.overdue ? "late" : r.done ? "done" : ""}`}>{r.status}</span>}{r.overdue && <span className="chip late">overdue</span>}</td>
                      <td>{short(r.start)}</td><td>{short(r.due)}</td><td>{r.person}</td><td className="small">{r.location}</td></tr>)}</tbody></table></div>
                  {!shown.length && <Empty title="No items found yet" hint="Press Check now. If the site needs a sign-in, sign in once first." />}
                </div>
              )}

              {tab === "schedule" && <Schedule items={items} siteId={sel.id} onCalendar={() => act("Adding to your calendar…", () => api<{ created: number }>(`/sites/${sel.id}/to-calendar`, { method: "POST" }).then((r) => toast.ok(`${r.created} added to your calendar`)))} />}

              {tab === "do" && (
                <div className="col gap">
                  {pending.map((a) => (
                    <div key={a.id} className="alert warn col">
                      <div><strong>Waiting for your OK:</strong> {a.summary}</div>
                      <div className="row"><button className="btn primary sm" onClick={() => act("Doing it…", () => api(`/site-actions/${a.id}`, { body: { approve: true } }), "Done")}><Check size={14} /> Yes, do it</button>
                        <button className="btn sm" onClick={() => act("", () => api(`/site-actions/${a.id}`, { body: { approve: false } }), "Cancelled")}><X size={14} /> No</button></div>
                    </div>
                  ))}
                  {!forms.length && <Empty title="No forms found on this site yet" hint="After a check, the forms the site offers (update a status, add a note, submit a timesheet) appear here." />}
                  {forms.length > 0 && <select className="input" value={formIdx} onChange={(e) => { const i = Number(e.target.value); setFormIdx(i); setVals(Object.fromEntries((forms[i]?.fields ?? []).map((f) => [f.name, f.value]))); }}>
                    <option value={-1}>What do you want to do?</option>{forms.map((f, i) => <option key={i} value={i}>{f.name} ({f.submit}) · {new URL(f.page_url).pathname}</option>)}</select>}
                  {form && (
                    <form className="col gap" onSubmit={(e) => { e.preventDefault(); act("Preparing…", () => api(`/sites/${sel.id}/actions`, { body: { form: String(formIdx), values: vals } }), "Check it, then press Yes"); }}>
                      {form.fields.filter((f) => f.type !== "hidden").map((f) => (
                        <label key={f.name} className="col small">{f.label}
                          {f.options.length ? <select className="input" value={vals[f.name] ?? ""} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}>{f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
                            : f.type === "textarea" ? <textarea className="input" rows={3} value={vals[f.name] ?? ""} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
                              : <input className="input" type={f.type === "password" ? "password" : "text"} value={vals[f.name] ?? ""} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />}
                        </label>
                      ))}
                      <div className="row"><button className="btn primary sm">Prepare “{form.submit}”</button><span className="small muted">You will see exactly what will be sent before anything happens.</span></div>
                    </form>
                  )}
                  {actions.filter((a) => a.site_id === sel.id && a.status !== "pending").slice(0, 10).map((a) => <div key={a.id} className="small muted">{a.status === "done" ? "Done" : a.status === "denied" ? "Cancelled" : "Failed"} · {a.summary} · {relTime(a.created_at)}</div>)}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** A simple program view: one bar per dated item, today marked, late items highlighted. */
function Schedule({ items, siteId, onCalendar }: { items: Item[]; siteId: number; onCalendar: () => void }) {
  const range = useMemo(() => {
    if (!items.length) return null;
    const today = day(new Date().toISOString());
    const lo = Math.min(today, ...items.map((i) => day(i.start)));
    const hi = Math.max(today, ...items.map((i) => day(i.end))) + 86400000;
    return { lo, hi, today };
  }, [items]);
  if (!items.length || !range) return <Empty title="Nothing with dates yet" hint="Items with dates or times on the site show up here as a program." />;
  const pct = (t: number) => `${((t - range.lo) / (range.hi - range.lo)) * 100}%`;
  return (
    <div className="col gap">
      <div className="row wrap"><button className="btn primary sm" onClick={onCalendar}><CalendarPlus size={14} /> Add to my calendar</button>
        <a className="btn sm" href={`/api/sites/${siteId}/calendar.ics`}><Download size={14} /> Calendar file (.ics)</a>
        <span className="small muted">Done items are left out of your calendar. Adding twice does not duplicate.</span></div>
      <div className="row small muted gantt-key"><span className="gantt-swatch" /> planned <span className="gantt-swatch late" /> overdue <span className="gantt-swatch done" /> done <span className="gantt-swatch now" /> today</div>
      <div className="gantt">
        {items.slice(0, 200).map((i) => (
          <div key={i.id} className="gantt-row">
            <div className="gantt-label truncate" title={i.title}>{i.title}</div>
            <div className="gantt-track"><div className="gantt-now" style={{ left: pct(range.today) }} /><div className={`gantt-bar ${i.overdue ? "late" : i.done ? "done" : ""}`}
              style={{ left: pct(day(i.start)), width: `max(6px, calc(${pct(day(i.end) + 86400000)} - ${pct(day(i.start))}))` }}
              title={`${i.title}: ${short(i.start)}${i.end !== i.start ? " to " + short(i.end) : ""}${i.status ? " · " + i.status : ""}`} /></div>
            <div className="gantt-when small muted">{short(i.start)}{i.end !== i.start ? `–${short(i.end)}` : ""}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
