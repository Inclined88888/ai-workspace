// Trigger automations (feat/v02-automations): "When X happens, do Y, ask me first", from plain English or a simple builder.
import { ArrowRight, Loader2, Play, Sparkles, Trash2, Zap } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { relTime, useToast } from "../util";
import "./v02.css";

type Trig = Record<string, unknown>;
export type Automation = {
  id: number; name: string; request: string; trigger_kind: string; trigger: Trig; action_kind: string; action: Trig;
  ask_first: number | boolean; enabled: number | boolean; fired: number; last_fired: string | null; last_error: string | null; last_result: string | null;
  when: string; then: string; sentence: string;
};
type Listing = { automations: Automation[]; limit: number | null; pro: boolean };
type Draft = { name: string; request: string; trigger_kind: string; trigger: Trig; action_kind: string; action: Trig; ask_first: boolean;
  when?: string; then?: string; questions?: string[]; confidence?: number };

const EXAMPLES = [
  "When an invoice email arrives, save the PDF to Invoices and add it to my spreadsheet",
  "15 minutes before each meeting, tell me who is coming and what we last talked about",
  "When a PDF lands in my Downloads folder, summarise it",
  "When something new appears on my builder portal, add it to my tasks",
  "Every weekday at 8am, summarise my unread email",
  "When I share a link from my phone, save it to my reading list note",
];
const TRIGGERS: { v: string; label: string }[] = [
  { v: "email", label: "An email arrives" }, { v: "calendar", label: "A calendar event is coming up" }, { v: "site", label: "A work site changes" },
  { v: "folder", label: "A file appears in a folder" }, { v: "schedule", label: "On a schedule" }, { v: "share", label: "I share something from my phone" },
];
const ACTIONS: { v: string; label: string }[] = [
  { v: "task", label: "Ask the assistant to…" }, { v: "playbook", label: "Run my “Show me how” steps" }, { v: "analyse_file", label: "Read and analyse the file" },
  { v: "draft_email", label: "Draft an email (never sent on its own)" }, { v: "add_event", label: "Add it to my calendar" },
];
const SCHEDULES = [
  { v: "0 8 * * *", label: "Every day at 8:00" }, { v: "0 8 * * 1-5", label: "Weekdays at 8:00" }, { v: "0 17 * * 5", label: "Fridays at 17:00" },
  { v: "0 9 * * 1", label: "Mondays at 9:00" }, { v: "0 * * * *", label: "Every hour" },
];
const BLANK: Draft = { name: "", request: "", trigger_kind: "email", trigger: {}, action_kind: "task", action: {}, ask_first: true };

export default function TriggerAutomations() {
  const [data, setData] = useState<Listing | null>(null);
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [builder, setBuilder] = useState(false);
  const [busy, setBusy] = useState("");
  const [sites, setSites] = useState<{ id: number; name: string }[]>([]);
  const [playbooks, setPlaybooks] = useState<{ id: number; name: string }[]>([]);
  const toast = useToast();

  const load = () => api<Listing>("/automations").then(setData).catch(toast.err);
  useEffect(() => {
    void load();
    api<{ sites: { id: number; name: string }[] }>("/sites").then((r) => setSites(r.sites || [])).catch(() => {});
    api<{ id: number; name: string }[]>("/learn/playbooks").then((r) => setPlaybooks(Array.isArray(r) ? r : [])).catch(() => {});
  }, []);

  const parse = async (e?: FormEvent, t = text) => {
    e?.preventDefault();
    if (!t.trim()) return;
    setBusy("parse");
    try { const d = await api<Draft>("/automations/parse", { body: { text: t } }); setDraft(d); setBuilder(false); }
    catch (err) { toast.err(err); } finally { setBusy(""); }
  };
  const save = async () => {
    if (!draft) return;
    setBusy("save");
    try {
      await api("/automations", { body: { name: draft.name, request: draft.request, trigger_kind: draft.trigger_kind, trigger: draft.trigger,
        action_kind: draft.action_kind, action: draft.action, ask_first: draft.ask_first } });
      toast.ok("Automation is on");
      setDraft(null); setText(""); setBuilder(false);
      void load();
    } catch (err) { toast.err(err); } finally { setBusy(""); }
  };
  const patch = async (a: Automation, body: Record<string, unknown>) => {
    try { await api(`/automations/${a.id}`, { method: "PATCH", body }); void load(); } catch (e) { toast.err(e); }
  };
  const remove = async (a: Automation) => {
    if (!confirm(`Delete “${a.name}”?`)) return;
    try { await api(`/automations/${a.id}`, { method: "DELETE" }); void load(); } catch (e) { toast.err(e); }
  };
  const tryNow = async (a: Automation) => {
    setBusy(`try${a.id}`);
    try { await api(`/automations/${a.id}/test`, { method: "POST" }); toast.ok("Test run is on the Jobs board"); void load(); }
    catch (e) { toast.err(e); } finally { setBusy(""); }
  };

  const count = data?.automations.length ?? 0;
  const atLimit = data?.limit != null && count >= data.limit;
  const set = (k: "trigger" | "action", key: string, v: unknown) => setDraft((d) => (d ? { ...d, [k]: { ...d[k], [key]: v } } : d));

  return (
    <section className="trig" aria-labelledby="trig-h">
      {toast.node}
      <div className="trig-head">
        <h2 id="trig-h"><Zap size={18} /> When this happens, do that</h2>
        {data && data.limit != null && <span className="small muted">{count} of {data.limit} on the Free plan · Pro is unlimited</span>}
      </div>

      {!draft && (
        <form className="card trig-ask" onSubmit={(e) => void parse(e)}>
          <label className="trig-label" htmlFor="trig-text">Say it in your own words</label>
          <div className="row">
            <input id="trig-text" className="input grow" value={text} onChange={(e) => setText(e.target.value)} disabled={atLimit}
              placeholder="When an invoice email arrives, save the PDF to Invoices and add it to my spreadsheet" />
            <button className="btn primary" disabled={!text.trim() || busy === "parse" || atLimit}>{busy === "parse" ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />} Set it up</button>
          </div>
          <div className="trig-examples">
            {EXAMPLES.map((x) => <button type="button" key={x} className="trig-example" disabled={atLimit} onClick={() => { setText(x); void parse(undefined, x); }}>{x}</button>)}
          </div>
          <div className="small muted row">
            <button type="button" className="linkish" disabled={atLimit} onClick={() => { setDraft({ ...BLANK }); setBuilder(true); }}>Or build it step by step</button>
            {atLimit && <span>You've used the {data?.limit} Free automations. Delete one, or go Pro for unlimited.</span>}
          </div>
        </form>
      )}

      {draft && (
        <div className="card trig-confirm">
          {!builder ? (
            <>
              <div className="trig-sentence">
                <div className="trig-part"><span className="trig-tag">When</span>{draft.when}</div>
                <ArrowRight size={16} className="trig-arrow" aria-hidden="true" />
                <div className="trig-part"><span className="trig-tag">Then</span>{draft.then}</div>
              </div>
              {draft.questions && draft.questions.length > 0 && <ul className="small trig-questions">{draft.questions.map((q) => <li key={q}>{q}</li>)}</ul>}
            </>
          ) : (
            <Builder draft={draft} setDraft={setDraft} set={set} sites={sites} playbooks={playbooks} />
          )}
          <label className="switch-label trig-askfirst">
            <input type="checkbox" checked={draft.ask_first} onChange={(e) => setDraft({ ...draft, ask_first: e.target.checked })} />
            Ask me before each run
          </label>
          <div className="small muted">Either way, anything that sends, posts, pays, deletes or changes something outside the app waits for your OK every time.</div>
          <div className="row wrap">
            <button className="btn primary" onClick={() => void save()} disabled={busy === "save"}><Zap size={14} /> Turn it on</button>
            {!builder && <button className="btn ghost" onClick={() => setBuilder(true)}>Change the details</button>}
            <button className="btn ghost" onClick={() => { setDraft(null); setBuilder(false); }}>Cancel</button>
          </div>
        </div>
      )}

      <div className="trig-list">
        {data?.automations.map((a) => (
          <article key={a.id} className={`card trig-item ${a.enabled ? "" : "off"}`}>
            <div className="grow col">
              <strong>{a.name}</strong>
              <div className="trig-sentence small">
                <div className="trig-part"><span className="trig-tag">When</span>{a.when}</div>
                <ArrowRight size={14} className="trig-arrow" aria-hidden="true" />
                <div className="trig-part"><span className="trig-tag">Then</span>{a.then}</div>
              </div>
              <div className="small muted">
                {a.fired ? `Ran ${a.fired} time${a.fired === 1 ? "" : "s"}, last ${relTime(a.last_fired || "")}` : "Hasn't run yet"}
                {a.last_error && <span className="trig-err"> · {a.last_error}</span>}
              </div>
            </div>
            <div className="col trig-controls">
              <label className="switch-label small"><input type="checkbox" checked={!!a.enabled} onChange={(e) => void patch(a, { enabled: e.target.checked })} /> On</label>
              <label className="switch-label small"><input type="checkbox" checked={!!a.ask_first} onChange={(e) => void patch(a, { ask_first: e.target.checked })} /> Ask first</label>
              <div className="row">
                <button className="btn ghost sm" disabled={busy === `try${a.id}`} onClick={() => void tryNow(a)}><Play size={13} /> Try now</button>
                <button className="btn ghost sm icon" aria-label={`Delete ${a.name}`} onClick={() => void remove(a)}><Trash2 size={13} /></button>
              </div>
            </div>
          </article>
        ))}
        {data && data.automations.length > 0 && <div className="small muted">Runs show up on the <Link to="/jobs">Jobs board</Link>.</div>}
      </div>
    </section>
  );
}

function Builder({ draft, setDraft, set, sites, playbooks }: {
  draft: Draft; setDraft: (d: Draft) => void; set: (k: "trigger" | "action", key: string, v: unknown) => void;
  sites: { id: number; name: string }[]; playbooks: { id: number; name: string }[];
}) {
  const t = draft.trigger;
  const a = draft.action;
  const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));
  return (
    <div className="trig-builder">
      <div className="col gap">
        <label className="small col">When
          <select className="input" value={draft.trigger_kind} onChange={(e) => setDraft({ ...draft, trigger_kind: e.target.value, trigger: e.target.value === "schedule" ? { cron: "0 8 * * *" } : e.target.value === "folder" ? { path: "~/Downloads", pattern: "*" } : {} })}>
            {TRIGGERS.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
        </label>
        {draft.trigger_kind === "email" && <>
          <label className="small col">From (optional)<input className="input" value={str(t.from)} onChange={(e) => set("trigger", "from", e.target.value)} placeholder="e.g. accounts@supplier.com" /></label>
          <label className="small col">Mentions (optional)<input className="input" value={str(t.words)} onChange={(e) => set("trigger", "words", e.target.value)} placeholder="e.g. invoice" /></label>
          <label className="switch-label small"><input type="checkbox" checked={!!t.attachment} onChange={(e) => set("trigger", "attachment", e.target.checked)} /> Only with an attachment</label>
        </>}
        {draft.trigger_kind === "calendar" && <>
          <label className="small col">How long before<select className="input" value={str(t.minutes_before || 15)} onChange={(e) => set("trigger", "minutes_before", Number(e.target.value))}>
            {[5, 10, 15, 30, 60, 120, 1440].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : m === 1440 ? "1 day" : `${m / 60} hour${m > 60 ? "s" : ""}`}</option>)}
          </select></label>
          <label className="small col">Only events mentioning (optional)<input className="input" value={str(t.words)} onChange={(e) => set("trigger", "words", e.target.value)} placeholder="e.g. client" /></label>
        </>}
        {draft.trigger_kind === "site" && (sites.length
          ? <>
              <label className="small col">Which site<select className="input" value={str(t.site_id)} onChange={(e) => set("trigger", "site_id", Number(e.target.value))}>
                <option value="">Pick a site</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select></label>
              <label className="small col">What counts<select className="input" value={str(t.only || "any")} onChange={(e) => set("trigger", "only", e.target.value)}>
                <option value="any">Anything changes</option><option value="added">Something new appears</option><option value="changed">Something changes</option>
              </select></label>
            </>
          : <div className="small muted">Add a site on the <Link to="/sites">Sites</Link> page first.</div>)}
        {draft.trigger_kind === "folder" && <>
          <label className="small col">Folder<input className="input" value={str(t.path)} onChange={(e) => set("trigger", "path", e.target.value)} placeholder="~/Downloads" /></label>
          <label className="small col">Which files<select className="input" value={str(t.pattern || "*")} onChange={(e) => set("trigger", "pattern", e.target.value)}>
            <option value="*">Any file</option><option value="*.pdf">PDFs</option><option value="*.xlsx,*.xls,*.csv">Spreadsheets</option><option value="*.docx,*.doc">Word documents</option><option value="*.jpg,*.jpeg,*.png,*.heic">Photos</option>
          </select></label>
        </>}
        {draft.trigger_kind === "schedule" && <label className="small col">When<select className="input" value={str(t.cron)} onChange={(e) => set("trigger", "cron", e.target.value)}>
          {SCHEDULES.map((s) => <option key={s.v} value={s.v}>{s.label}</option>)}
          {!SCHEDULES.some((s) => s.v === t.cron) && t.cron ? <option value={str(t.cron)}>{str(t.cron)}</option> : null}
        </select></label>}
        {draft.trigger_kind === "share" && <label className="small col">Only when it mentions (optional)<input className="input" value={str(t.words)} onChange={(e) => set("trigger", "words", e.target.value)} placeholder="e.g. recipe" /></label>}
      </div>
      <div className="col gap">
        <label className="small col">Then
          <select className="input" value={draft.action_kind} onChange={(e) => setDraft({ ...draft, action_kind: e.target.value, action: { prompt: a.prompt } })}>
            {ACTIONS.map((x) => <option key={x.v} value={x.v}>{x.label}</option>)}
          </select>
        </label>
        {draft.action_kind === "playbook"
          ? (playbooks.length
              ? <label className="small col">Which steps<select className="input" value={str(a.playbook_id)} onChange={(e) => set("action", "playbook_id", Number(e.target.value))}>
                  <option value="">Pick one</option>{playbooks.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select></label>
              : <div className="small muted">Teach it something on <Link to="/teach">Show me how</Link> first.</div>)
          : <label className="small col">{draft.action_kind === "task" ? "What to do" : "Anything to add (optional)"}
              <textarea className="input" rows={3} value={str(a.prompt)} onChange={(e) => set("action", "prompt", e.target.value)}
                placeholder={draft.action_kind === "draft_email" ? "e.g. thank them and say I'll reply properly tomorrow" : "e.g. save the PDF to Invoices and add a row to my Invoices sheet"} />
            </label>}
        {draft.action_kind === "draft_email" && <label className="small col">To (optional)<input className="input" value={str(a.to)} onChange={(e) => set("action", "to", e.target.value)} placeholder="Leave empty to reply to the sender" /></label>}
        <label className="small col">Name<input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="e.g. Save invoices" /></label>
      </div>
    </div>
  );
}
