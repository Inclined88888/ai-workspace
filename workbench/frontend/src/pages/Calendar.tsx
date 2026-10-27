import { ChevronLeft, ChevronRight, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, Calendar, CalEvent } from "../api";
import { fmtTime, localDateISO, useToast } from "../util";
import { Modal } from "../components/ui";

function eventDays(ev: CalEvent): [string, string] {
  if (ev.all_day) {
    const end = new Date(`${ev.end}T00:00:00`);
    end.setDate(end.getDate() - 1);
    return [ev.start, localDateISO(end) < ev.start ? ev.start : localDateISO(end)];
  }
  const end = new Date(new Date(ev.end).getTime() - 1);
  return [localDateISO(new Date(ev.start)), localDateISO(end < new Date(ev.start) ? new Date(ev.start) : end)];
}

function AddCalendar({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [form, setForm] = useState({ name: "", kind: "ics" as "ics" | "caldav" | "local", url: "", username: "", password: "", color: "#0ea5e9" });
  const [found, setFound] = useState<{ name: string; url: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const discover = async () => {
    setBusy(true);
    setError("");
    try {
      setFound(await api("/calendars/discover", { body: { url: form.url, username: form.username, password: form.password } }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api<{ error?: string }>("/calendars", { body: form });
      if (r.error) setError(`Added, but the first sync failed: ${r.error}`);
      else onClose();
      onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Add a calendar" onClose={onClose}>
      <form className="col gap-lg" onSubmit={submit}>
        {error && <div className="alert err">{error}</div>}
        <div className="tabs">
          {(["ics", "caldav", "local"] as const).map((k) => (
            <button type="button" key={k} className={`tab ${form.kind === k ? "active" : ""}`} onClick={() => setForm({ ...form, kind: k })}>
              {k === "ics" ? "ICS link (read only)" : k === "caldav" ? "CalDAV (two way)" : "Local"}
            </button>
          ))}
        </div>
        {form.kind === "ics" && <div className="muted small">Paste a secret iCal address: Google Calendar → Settings → your calendar → "Secret address in iCal format"; Outlook → Settings → Shared calendars → Publish → ICS. Refreshes every 15 minutes.</div>}
        {form.kind === "caldav" && <div className="muted small">Works with Nextcloud, Fastmail, iCloud (app specific password, server https://caldav.icloud.com), Radicale and Baïkal. Use Find calendars, then pick one.</div>}
        <label className="field">Name<input className="input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        {form.kind !== "local" && <label className="field">URL<input className="input" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder={form.kind === "ics" ? "https://… .ics or webcal://…" : "https://cloud.example.com/remote.php/dav"} /></label>}
        {form.kind === "caldav" && (
          <>
            <div className="row">
              <label className="field grow">Username<input className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
              <label className="field grow">Password<input className="input" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
            </div>
            <button type="button" className="btn" disabled={!form.url || busy} onClick={discover}>Find calendars</button>
            {found.map((c) => (
              <label key={c.url} className="check"><input type="radio" name="cal" checked={form.url === c.url} onChange={() => setForm({ ...form, url: c.url, name: form.name || c.name })} /> {c.name} <span className="faint small truncate">{c.url}</span></label>
            ))}
          </>
        )}
        <label className="field">Color<input type="color" value={form.color} onChange={(e) => setForm({ ...form, color: e.target.value })} /></label>
        <button className="btn primary" disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : "Add calendar"}</button>
      </form>
    </Modal>
  );
}

function AddEvent({ date, calendars, onClose, onAdded }: { date: string; calendars: Calendar[]; onClose: () => void; onAdded: () => void }) {
  const writable = calendars.filter((c) => c.kind !== "ics" && c.enabled);
  const [form, setForm] = useState({ title: "", date, startTime: "09:00", endTime: "10:00", all_day: false, location: "", description: "", calendar_id: writable[0]?.id ?? 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const iso = (t: string) => new Date(`${form.date}T${t}:00`).toISOString();
      const next = new Date(`${form.date}T00:00:00`);
      next.setDate(next.getDate() + 1);
      await api("/events", {
        body: {
          title: form.title, all_day: form.all_day, location: form.location, description: form.description, calendar_id: form.calendar_id || null,
          start: form.all_day ? form.date : iso(form.startTime), end: form.all_day ? localDateISO(next) : iso(form.endTime),
        },
      });
      onAdded();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="New event" onClose={onClose}>
      <form className="col gap-lg" onSubmit={submit}>
        {error && <div className="alert err">{error}</div>}
        <input className="input" required autoFocus placeholder="Title" aria-label="Event title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <div className="row wrap">
          <input className="input" type="date" aria-label="Date" style={{ width: 160 }} value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} />
          {!form.all_day && (
            <>
              <input className="input" type="time" aria-label="Start time" style={{ width: 120 }} value={form.startTime} onChange={(e) => setForm({ ...form, startTime: e.target.value })} />
              <span className="muted">to</span>
              <input className="input" type="time" aria-label="End time" style={{ width: 120 }} value={form.endTime} onChange={(e) => setForm({ ...form, endTime: e.target.value })} />
            </>
          )}
          <label className="check"><input type="checkbox" checked={form.all_day} onChange={(e) => setForm({ ...form, all_day: e.target.checked })} /> All day</label>
        </div>
        <input className="input" placeholder="Location" aria-label="Location" value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} />
        <textarea className="input" rows={3} placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        <select className="input" value={form.calendar_id} onChange={(e) => setForm({ ...form, calendar_id: Number(e.target.value) })}>
          {writable.map((c) => <option key={c.id} value={c.id}>{c.name}{c.kind === "caldav" ? " (CalDAV)" : ""}</option>)}
        </select>
        <button className="btn primary" disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : "Create event"}</button>
      </form>
    </Modal>
  );
}

export default function CalendarPage() {
  const [cursor, setCursor] = useState(() => { const d = new Date(); d.setDate(1); return d; });
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [selected, setSelected] = useState(localDateISO(new Date()));
  const [modal, setModal] = useState<"" | "calendar" | "event">("");
  const [syncing, setSyncing] = useState(0);
  const toast = useToast();

  const days = useMemo(() => {
    const first = new Date(cursor);
    const offset = (first.getDay() + 6) % 7; // Monday first
    const start = new Date(first);
    start.setDate(1 - offset);
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  }, [cursor]);

  const loadCalendars = () => api<Calendar[]>("/calendars").then(setCalendars).catch(toast.err);
  const loadEvents = () => {
    const start = new Date(days[0]); const end = new Date(days[41]); end.setDate(end.getDate() + 1);
    api<CalEvent[]>(`/events?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`).then(setEvents).catch(toast.err);
  };
  useEffect(() => { loadCalendars(); }, []);
  useEffect(loadEvents, [days]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalEvent[]>();
    for (const ev of events) {
      const [s, e] = eventDays(ev);
      for (const d of days) { const k = localDateISO(d); if (k >= s && k <= e) map.set(k, [...(map.get(k) ?? []), ev]); }
    }
    return map;
  }, [events, days]);

  const sync = async (c: Calendar) => {
    setSyncing(c.id);
    try { const r = await api<{ synced: number }>(`/calendars/${c.id}/sync`, { method: "POST" }); toast.ok(`${c.name}: ${r.synced} events`); loadEvents(); }
    catch (e) { toast.err(e); }
    finally { setSyncing(0); loadCalendars(); }
  };
  const toggle = async (c: Calendar) => {
    await api(`/calendars/${c.id}`, { method: "PUT", body: { name: c.name, kind: c.kind, url: c.url, username: c.username, color: c.color, enabled: !c.enabled } });
    loadCalendars(); loadEvents();
  };
  const removeCal = async (c: Calendar) => {
    if (!confirm(`Remove calendar "${c.name}" and its synced events from ROOKS?`)) return;
    await api(`/calendars/${c.id}`, { method: "DELETE" }); loadCalendars(); loadEvents();
  };
  const removeEvent = async (ev: CalEvent) => {
    if (!confirm(`Delete "${ev.title}"?`)) return;
    try { await api(`/events/${ev.id}`, { method: "DELETE" }); loadEvents(); } catch (e) { toast.err(e); }
  };

  const today = localDateISO(new Date());
  const month = cursor.getMonth();
  const dayEvents = byDay.get(selected) ?? [];

  return (
    <div className="page wide" style={{ maxWidth: 1400 }}>
      <div className="page-head">
        <h1>{cursor.toLocaleDateString([], { month: "long", year: "numeric" })}</h1>
        <div className="row">
          <button className="btn icon sm" aria-label="Previous month" title="Previous month" onClick={() => setCursor(new Date(cursor.getFullYear(), month - 1, 1))}><ChevronLeft size={16} /></button>
          <button className="btn sm" onClick={() => { const d = new Date(); d.setDate(1); setCursor(d); setSelected(today); }}>Today</button>
          <button className="btn icon sm" aria-label="Next month" title="Next month" onClick={() => setCursor(new Date(cursor.getFullYear(), month + 1, 1))}><ChevronRight size={16} /></button>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => setModal("calendar")}><Plus size={15} /> Calendar</button>
        <button className="btn primary" onClick={() => setModal("event")}><Plus size={15} /> Event</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 300px", gap: 16 }} className="cal-layout">
        <div className="card flush">
          <div className="month">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="dow">{d}</div>)}
            {days.map((d) => {
              const k = localDateISO(d);
              const list = byDay.get(k) ?? [];
              return (
                <div key={k} className={`day ${d.getMonth() !== month ? "other" : ""} ${k === today ? "today" : ""} ${list.length ? "has-events" : ""}`}
                  onClick={() => setSelected(k)} onDoubleClick={() => { setSelected(k); setModal("event"); }}
                  style={k === selected ? { outline: "2px solid var(--accent)", outlineOffset: -2 } : undefined}>
                  <span className="n">{d.getDate()}</span>
                  {list.slice(0, 3).map((ev) => <div key={`${ev.id}-${k}`} className="ev" style={{ borderLeftColor: ev.color }}>{!ev.all_day && <span className="faint">{fmtTime(ev.start)} </span>}{ev.title}</div>)}
                  {list.length > 3 && <div className="faint small">+{list.length - 3} more</div>}
                </div>
              );
            })}
          </div>
        </div>
        <div className="col gap-lg">
          <div className="card col">
            <h3>{new Date(`${selected}T00:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</h3>
            {dayEvents.length === 0 && <div className="muted small">No events. Double click a day to add one.</div>}
            {dayEvents.map((ev) => (
              <div key={ev.id} className="row" style={{ alignItems: "flex-start" }}>
                <span className="dot" style={{ background: ev.color, marginTop: 6 }} />
                <div className="grow">
                  <div>{ev.title || "(untitled)"}</div>
                  <div className="muted small">{ev.all_day ? "All day" : `${fmtTime(ev.start)} to ${fmtTime(ev.end)}`}{ev.location ? ` · ${ev.location}` : ""} · {ev.calendar_name}</div>
                </div>
                {ev.calendar_kind === "local" && <button className="btn ghost sm icon" aria-label={`Delete event ${ev.title}`} title="Delete event" onClick={() => removeEvent(ev)}><Trash2 size={13} /></button>}
              </div>
            ))}
          </div>
          <div className="card col">
            <h3>Calendars</h3>
            {calendars.map((c) => (
              <div key={c.id} className="row">
                <input type="checkbox" aria-label={`Show ${c.name}`} checked={!!c.enabled} onChange={() => toggle(c)} style={{ accentColor: c.color }} />
                <div className="grow">
                  <div className="truncate">{c.name}</div>
                  <div className="faint small truncate">{c.kind === "local" ? "Local" : c.kind === "ics" ? "ICS subscription" : "CalDAV"}{c.last_synced ? ` · synced ${new Date(c.last_synced).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : ""}</div>
                  {c.last_error && <div className="small" style={{ color: "var(--danger)" }}>{c.last_error}</div>}
                </div>
                {c.kind !== "local" && <button className="btn ghost sm icon" onClick={() => sync(c)} title="Sync now" aria-label={`Sync ${c.name} now`}>{syncing === c.id ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}</button>}
                <button className="btn ghost sm icon" onClick={() => removeCal(c)} title="Remove calendar" aria-label={`Remove calendar ${c.name}`}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        </div>
      </div>
      {modal === "calendar" && <AddCalendar onClose={() => setModal("")} onAdded={() => { loadCalendars(); loadEvents(); }} />}
      {modal === "event" && <AddEvent date={selected} calendars={calendars} onClose={() => setModal("")} onAdded={loadEvents} />}
      {toast.node}
    </div>
  );
}
