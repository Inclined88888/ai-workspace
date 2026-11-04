import { CalendarClock, Inbox as InboxIcon, Loader2, Play, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { Empty, Markdown, relTime, useToast } from "../util";
import TriggerAutomations from "../v02/TriggerAutomations"; // feat/v02-automations

type Schedule = { id: number; name: string; cron: string; prompt: string; deliver: string; enabled: number; next_run: string | null; last_run: string | null; last_status: string };
type Item = { id: number; title: string; body: string; source: string; read: number; created_at: string };

const PRESETS = [
  { label: "Plan my day, 7:30", name: "Plan my day", cron: "30 7 * * 1-5",
    prompt: "Plan my day. Check my calendar and what's due. Open each place I've shown you (list_playbooks, then run_playbook for each) and see what's new. Write today in order: what to do, and when. Add anything with a time as a task due today. Do not send mail or pay anyone." },
  { label: "Daily report, 8:00", name: "Daily report", cron: "0 8 * * *",
    prompt: "Write my daily briefing: today's events and due or overdue tasks (use get_agenda), anything you remember I'm working on or waiting for, and 3 suggested priorities. Under 200 words." },
  { label: "Weekly review, Fri 17:00", name: "Weekly review", cron: "0 17 * * 5",
    prompt: "Review my week: completed and still open tasks, what moved forward in our conversations, and what to focus on next week. Under 250 words." },
  { label: "Weekday news, 7:30", name: "Morning news", cron: "30 7 * * 1-5",
    prompt: "Search the web for the most important news in my areas of interest (from memory) and give me 5 bullet points with links." },
];

function OfferWhen() {
  const [when, setWhen] = useState("2");
  const toast = useToast();
  useEffect(() => { api<{ when: string }>("/automations/offer").then((r) => setWhen(r.when)).catch(() => undefined); }, []);
  const pick = async (v: string) => {
    setWhen(v);
    try { await api("/automations/offer", { method: "PUT", body: { when: v } }); }
    catch (e) { toast.err(e); }
  };
  return (
    <label className="row small" style={{ marginBottom: 16, gap: 10 }}>
      <span>Ask to save a task</span>
      <select className="input sm" aria-label="When to offer an automation" value={when} onChange={(e) => pick(e.target.value)}>
        <option value="2">After I've done it twice</option>
        <option value="3">After I've done it three times</option>
        <option value="always">Every time</option>
        <option value="off">Don't ask</option>
      </select>
      {toast.node}
    </label>
  );
}

function describe(cron: string): string {
  const [m, h, dom, mon, dow] = cron.split(" ");
  const time = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : "";
  if (time && dom === "*" && mon === "*") {
    if (dow === "*") return `Every day at ${time}`;
    if (dow === "1-5") return `Weekdays at ${time}`;
    const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    if (/^\d$/.test(dow)) return `Every ${days[Number(dow) % 7]} at ${time}`;
  }
  return cron;
}

export default function Automations() {
  const [data, setData] = useState<{ timezone: string; schedules: Schedule[] }>({ timezone: "", schedules: [] });
  const [inbox, setInbox] = useState<{ unread: number; items: Item[] }>({ unread: 0, items: [] });
  const [form, setForm] = useState({ name: "", cron: "0 8 * * *", prompt: "", deliver: "inbox" });
  const [running, setRunning] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const toast = useToast();

  const load = () => {
    api<typeof data>("/schedules").then(setData).catch(toast.err);
    api<typeof inbox>("/inbox").then(setInbox).catch(toast.err);
  };
  useEffect(() => { load(); }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    try { await api("/schedules", { body: form }); setForm({ name: "", cron: "0 8 * * *", prompt: "", deliver: "inbox" }); toast.ok("Scheduled"); load(); }
    catch (err) { toast.err(err); }
  };
  const runNow = async (s: Schedule) => {
    setRunning(s.id);
    try { await api(`/schedules/${s.id}/run`, { method: "POST" }); toast.ok(`${s.name} delivered to the inbox`); }
    catch (e) { toast.err(e); } finally { setRunning(0); load(); }
  };

  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <div className="page-head"><div><h1>Automations</h1><p className="muted">Show me once where to look, and I can check it each morning. Plan my day opens those places, sees what's new, and writes the plan.</p></div></div>
      <OfferWhen />
      <TriggerAutomations />{/* feat/v02-automations: "when X happens, do Y" */}
      <h2 className="trig-sub">Scheduled reports</h2>

      <form className="card col" onSubmit={add} style={{ marginBottom: 16 }}>
        <div className="row wrap">
          <span className="small muted">Quick start:</span>
          {PRESETS.map((p) => <button type="button" key={p.label} className="btn sm" onClick={() => setForm({ ...form, name: p.name, cron: p.cron, prompt: p.prompt })}>{p.label}</button>)}
        </div>
        <div className="row wrap">
          <input className="input grow" style={{ minWidth: 180 }} required placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input mono" style={{ width: 150 }} required value={form.cron} onChange={(e) => setForm({ ...form, cron: e.target.value })} title="minute hour day month weekday" />
          <span className="muted small" style={{ width: 150 }}>{describe(form.cron)}</span>
          <select className="input" style={{ width: 130 }} value={form.deliver} onChange={(e) => setForm({ ...form, deliver: e.target.value })}>
            <option value="inbox">Inbox</option><option value="telegram">Inbox + Telegram</option>
          </select>
        </div>
        <textarea className="input" rows={3} required placeholder="What should the assistant do each time?" value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
        <div className="row"><span className="muted small grow">Each run is a model call (and tool calls). Keep frequent jobs short.</span><button className="btn primary"><Plus size={15} /> Schedule</button></div>
      </form>

      <div className="grid two">
        <div className="col">
          <div className="row"><CalendarClock size={16} /><h2>Schedules</h2></div>
          {data.schedules.length === 0 && <div className="card"><Empty icon={<CalendarClock size={22} />} title="Nothing scheduled" hint="Try the daily report preset." /></div>}
          <div className="card flush">
            {data.schedules.map((s) => (
              <div key={s.id} className="col" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", gap: 4 }}>
                <div className="row">
                  <input type="checkbox" checked={!!s.enabled} onChange={async () => { await api(`/schedules/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } }); load(); }} />
                  <strong className="grow truncate">{s.name}</strong>
                  <button className="btn sm" disabled={running === s.id} onClick={() => runNow(s)}>{running === s.id ? <Loader2 size={13} className="spin" /> : <Play size={13} />} Run now</button>
                  <button className="btn ghost sm icon" onClick={async () => { if (confirm(`Delete ${s.name}?`)) { await api(`/schedules/${s.id}`, { method: "DELETE" }); load(); } }}><Trash2 size={13} /></button>
                </div>
                <div className="muted small">{describe(s.cron)} · {s.deliver === "telegram" ? "Inbox + Telegram" : "Inbox"}{s.enabled && s.next_run ? ` · next ${new Date(s.next_run).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : ""}</div>
                {s.last_status && s.last_status !== "ok" && <div className="small" style={{ color: "var(--danger)" }}>{s.last_status}</div>}
              </div>
            ))}
          </div>
        </div>
        <div className="col">
          <div className="row"><InboxIcon size={16} /><h2 className="grow">Inbox</h2>{inbox.unread > 0 && <button className="btn sm" onClick={async () => { await api("/inbox/read", { method: "POST" }); load(); }}>Mark {inbox.unread} read</button>}</div>
          {inbox.items.length === 0 && <div className="card"><Empty icon={<InboxIcon size={22} />} title="Empty" hint="Scheduled results and agent reports land here." /></div>}
          <div className="card flush">
            {inbox.items.map((i) => (
              <div key={i.id} className="col" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", gap: 4 }}>
                <div className="row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === i.id ? null : i.id)}>
                  {!i.read && <span className="dot" style={{ background: "var(--accent)" }} />}
                  <strong className="grow truncate">{i.title}</strong>
                  <span className="faint small">{relTime(i.created_at)}</span>
                  <button className="btn ghost sm icon" onClick={async (e) => { e.stopPropagation(); await api(`/inbox/${i.id}`, { method: "DELETE" }); load(); }}><Trash2 size={13} /></button>
                </div>
                {open === i.id ? <Markdown text={i.body} /> : <div className="muted small truncate">{i.body}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>
      {toast.node}
    </div>
  );
}
