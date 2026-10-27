import { Phone, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../util";
import { PasswordInput } from "./ui";
import type { ModelsStatus, ProviderStatus } from "./Models";

type Dest = { local?: boolean; label?: string };
type VoiceSettings = { stt: string; stt_model: string; tts: string; tts_model: string; tts_voice: string; local_only: boolean;
  consent: Record<string, { ref: string; destination: string; at: string }>; stt_destination: Dest; tts_destination: Dest };

function splitRef(ref: string): [string, string] {
  const i = ref.indexOf("/");
  return i < 0 ? ["", ""] : [ref.slice(0, i), ref.slice(i + 1)];
}

/** Where speech is recognised and spoken. Browser by default; a provider (local, or cloud with consent) if connected. */
export function VoiceSettingsPanel() {
  const [s, setS] = useState<VoiceSettings | null>(null);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [form, setForm] = useState({ stt: "browser", sttP: "", sttM: "", tts: "browser", ttsP: "", ttsM: "", voice: "" });
  const toast = useToast();
  const fill = (v: VoiceSettings) => {
    setS(v);
    const [sp, sm] = splitRef(v.stt_model);
    const [tp, tm] = splitRef(v.tts_model);
    setForm({ stt: v.stt, sttP: sp, sttM: sm, tts: v.tts, ttsP: tp, ttsM: tm, voice: v.tts_voice });
  };
  useEffect(() => {
    api<VoiceSettings>("/voice").then(fill).catch(toast.err);
    api<ModelsStatus>("/models/status").then((r) => setProviders(r.providers.filter((p) => p.enabled))).catch(() => setProviders([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    const body: Record<string, unknown> = { stt: form.stt, tts: form.tts, tts_voice: form.voice };
    if (form.stt === "provider") {
      if (!form.sttP || !form.sttM.trim()) { toast.err(new Error("Pick a provider and type the speech recognition model, for example whisper-1.")); return; }
      body.stt_model = `${form.sttP}/${form.sttM.trim()}`;
    }
    if (form.tts === "provider") {
      if (!form.ttsP || !form.ttsM.trim()) { toast.err(new Error("Pick a provider and type the speech model, for example tts-1.")); return; }
      body.tts_model = `${form.ttsP}/${form.ttsM.trim()}`;
    }
    try { fill(await api<VoiceSettings>("/voice", { method: "PUT", body })); toast.ok("Voice settings saved"); } catch (e) { toast.err(e); }
  };
  const consent = async (k: "stt_model" | "tts_model") => {
    try { fill(await api<VoiceSettings>("/voice", { method: "PUT", body: { consent_for: k } })); toast.ok("Recorded"); } catch (e) { toast.err(e); }
  };
  const localOnly = async (v: boolean) => {
    try { fill(await api<VoiceSettings>("/voice", { method: "PUT", body: { local_only: v } })); } catch (e) { toast.err(e); }
  };
  if (!s) return null;

  const needsConsent = (k: "stt" | "tts") => {
    const dest = k === "stt" ? s.stt_destination : s.tts_destination;
    return s[k] === "provider" && dest?.local === false && s.consent[`${k}_model`]?.ref !== s[`${k}_model`] ? dest : null;
  };
  const row = (k: "stt" | "tts", label: string, example: string) => {
    const p = k === "stt" ? "sttP" : "ttsP";
    const m = k === "stt" ? "sttM" : "ttsM";
    return (
      <fieldset className="col" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="field">{label}</legend>
        <div className="row wrap">
          <label className="row small" style={{ gap: 6 }}><input type="radio" name={k} checked={form[k] === "browser"} onChange={() => setForm({ ...form, [k]: "browser" })} /> This browser</label>
          <label className="row small" style={{ gap: 6 }}><input type="radio" name={k} checked={form[k] === "provider"} onChange={() => setForm({ ...form, [k]: "provider" })} disabled={!providers.length} /> A connected provider</label>
        </div>
        {form[k] === "provider" && (
          <div className="row wrap">
            <select className="input" style={{ width: 220 }} aria-label={`${label} provider`} value={form[p]} onChange={(e) => setForm({ ...form, [p]: e.target.value })}>
              <option value="">Choose a provider</option>
              {providers.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}
            </select>
            <input className="input" style={{ width: 200 }} aria-label={`${label} model`} placeholder={example} value={form[m]} onChange={(e) => setForm({ ...form, [m]: e.target.value })} />
          </div>
        )}
      </fieldset>
    );
  };

  return (
    <div className="card col gap-lg" id="voice" style={{ scrollMarginTop: 16 }}>
      <div className="row"><Volume2 size={16} aria-hidden="true" /><h2>Voice</h2></div>
      <div className="muted small">Talk with ROOKS from the Assistant's Voice button. By default your browser listens and speaks: nothing to install,
        but Chrome and Edge recognise speech on Google's or Microsoft's servers. For speech that stays on your network, connect a local
        OpenAI compatible speech server (for example a Whisper or Kokoro server) as a provider and pick it here. ROOKS never stores audio.</div>
      {row("stt", "Listening (speech to text)", "whisper-1")}
      {row("tts", "Speaking (text to speech)", "tts-1")}
      {form.tts === "provider" && <label className="field">Voice name <span className="hint">Optional, as the provider names it (for example alloy).</span>
        <input className="input" style={{ maxWidth: 240 }} value={form.voice} onChange={(e) => setForm({ ...form, voice: e.target.value })} /></label>}
      <div className="row wrap"><button type="button" className="btn primary" onClick={save}>Save</button></div>
      {(["stt", "tts"] as const).map((k) => {
        const dest = needsConsent(k);
        return dest ? (
          <div key={k} className="alert" role="status">
            {k === "stt" ? "Your recorded speech" : "The text ROOKS speaks"} would go to {dest.label}. Nothing is sent until you agree.
            <div style={{ marginTop: 6 }}><button type="button" className="btn sm" onClick={() => consent(`${k}_model`)}>I agree to send it there</button></div>
          </div>
        ) : null;
      })}
      <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={s.local_only} onChange={(e) => localOnly(e.target.checked)} />
        Local speech providers only (refuse cloud speech providers; projects set to local only always refuse them)</label>
      {toast.node}
    </div>
  );
}

type Policy = { window_start: string; window_end: string; weekend_start: string; weekend_end: string; invites_per_day: number; invite_minutes: number;
  phone_per_day: number; phone_per_week: number; phone_month_cents: number };
type CallsStatus = { policy: Policy; in_window: boolean;
  phone: { provider: string; enabled: boolean; number: string; verified: boolean; twilio_configured: boolean; month_cost_estimate_cents: number };
  invites: { id: number; title: string; status: string; created_at: string }[];
  phone_calls: { id: number; purpose: string; provider: string; to_masked: string; status: string; cost_estimate_cents: number; error: string; created_at: string }[] };

/** When ROOKS may ask to talk, and the optional phone line (off unless you set it up and verify your number). */
export function CallsPanel() {
  const [st, setSt] = useState<CallsStatus | null>(null);
  const [pol, setPol] = useState<Policy | null>(null);
  const [ph, setPh] = useState({ password: "", provider: "", number: "", sid: "", token: "", from: "" });
  const [code, setCode] = useState("");
  const [cost, setCost] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const load = () => api<CallsStatus>("/calls").then((r) => { setSt(r); setPol(r.policy); setPh((p) => ({ ...p, provider: r.phone.provider })); }).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!st || !pol) return null;

  const savePolicy = async () => {
    try { await api("/calls/policy", { method: "PUT", body: pol }); toast.ok("Saved"); load(); } catch (e) { toast.err(e); }
  };
  const savePhone = async (extra: Record<string, unknown> = {}) => {
    const body: Record<string, unknown> = { password: ph.password, provider: ph.provider, ...extra };
    if (ph.number) body.number = ph.number;
    if (ph.sid) body.twilio_sid = ph.sid;
    if (ph.token) body.twilio_token = ph.token;
    if (ph.from) body.twilio_from = ph.from;
    try { await api("/calls/phone", { method: "PUT", body }); toast.ok("Phone settings saved"); setPh({ ...ph, number: "", sid: "", token: "", from: "" }); load(); }
    catch (e) { toast.err(e); }
  };
  const verify = async () => {
    setBusy(true);
    try { await api("/calls/phone/verify", { body: { password: ph.password, confirm_cost: cost } }); toast.ok("Calling you now with a code"); load(); }
    catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  const confirmCode = async () => {
    try { await api("/calls/phone/confirm", { body: { code } }); toast.ok("Number verified"); setCode(""); load(); } catch (e) { toast.err(e); }
  };
  const num = (k: keyof Policy, label: string, min: number, max: number) => (
    <label className="field">{label}<input className="input" type="number" min={min} max={max} value={pol[k] as number}
      onChange={(e) => setPol({ ...pol, [k]: Number(e.target.value) })} /></label>
  );
  const time = (k: keyof Policy, label: string) => (
    <label className="field">{label}<input className="input" type="time" value={pol[k] as string} onChange={(e) => setPol({ ...pol, [k]: e.target.value })} /></label>
  );
  const p = st.phone;
  return (
    <div className="card col gap-lg" id="calls" style={{ scrollMarginTop: 16 }}>
      <div className="row"><Phone size={16} aria-hidden="true" /><h2 className="grow">Calls from ROOKS</h2>
        <span className={`badge ${st.in_window ? "ok" : ""}`}>{st.in_window ? "Inside contact hours" : "Outside contact hours"}</span></div>
      <div className="muted small">A check-in set to "call" asks to talk inside these hours. In the app it shows a banner: accept to hear it and reply by voice,
        or decline or ignore it and the briefing arrives in writing, once. ROOKS never calls anyone but you, and never outside these hours.</div>
      <div className="grid two">
        {time("window_start", "Weekdays from")}{time("window_end", "Weekdays until")}
        {time("weekend_start", "Weekends from")}{time("weekend_end", "Weekends until")}
        {num("invites_per_day", "In-app call requests per day", 0, 50)}{num("invite_minutes", "Minutes a request waits", 5, 240)}
      </div>
      <div><button type="button" className="btn primary" onClick={savePolicy}>Save contact rules</button></div>

      <h3>Phone line (optional, may cost money)</h3>
      <div className="muted small">ROOKS can phone your own verified number and read a briefing aloud, introducing itself as an AI. It is one way: you
        reply in the app. Calls go through Twilio on your account (Twilio listed about US$0.075 a minute to Australian mobiles when this was written;
        check its current prices). Calls are never recorded. Every change here needs your ROOKS password.</div>
      <div className="row wrap small">
        <span className="badge">{p.provider ? (p.provider === "test" ? "Test line (no real calls)" : "Twilio") : "Off"}</span>
        {p.number && <span className="badge">{p.number}</span>}
        {p.number && <span className={`badge ${p.verified ? "ok" : "warn"}`}>{p.verified ? "Verified" : "Not verified"}</span>}
        {p.provider && <span className={`badge ${p.enabled ? "ok" : ""}`}>{p.enabled ? "Calls on" : "Calls off"}</span>}
        <span className="badge">About ${(p.month_cost_estimate_cents / 100).toFixed(2)} in the last 30 days</span>
      </div>
      <div className="grid two">
        <div className="col" style={{ gap: 5 }}><label htmlFor="phone-pw" className="field">Your ROOKS password</label>
          <PasswordInput id="phone-pw" value={ph.password} onChange={(v) => setPh({ ...ph, password: v })} autoComplete="current-password" /></div>
        <label className="field">Line<select className="input" value={ph.provider} onChange={(e) => setPh({ ...ph, provider: e.target.value })}>
          <option value="">Off</option><option value="test">Test line (no real calls)</option><option value="twilio">Twilio</option></select></label>
        <label className="field">Your number <span className="hint">International format, e.g. +61412345678</span>
          <input className="input" value={ph.number} onChange={(e) => setPh({ ...ph, number: e.target.value })} placeholder={p.number || "+61…"} /></label>
        {ph.provider === "twilio" && <>
          <label className="field">Twilio Account SID<input className="input" value={ph.sid} onChange={(e) => setPh({ ...ph, sid: e.target.value })} placeholder={p.twilio_configured ? "saved" : "AC…"} /></label>
          <label className="field">Twilio Auth Token<input className="input" type="password" value={ph.token} onChange={(e) => setPh({ ...ph, token: e.target.value })} placeholder={p.twilio_configured ? "saved" : ""} /></label>
          <label className="field">Twilio number to call from<input className="input" value={ph.from} onChange={(e) => setPh({ ...ph, from: e.target.value })} /></label>
        </>}
      </div>
      <div className="grid two">
        {num("phone_per_day", "Phone calls per day", 0, 10)}{num("phone_per_week", "Phone calls per week", 0, 50)}
        <label className="field">Monthly phone cost limit (US cents)<input className="input" type="number" min={0} max={100000} value={pol.phone_month_cents}
          onChange={(e) => setPol({ ...pol, phone_month_cents: Number(e.target.value) })} /></label>
      </div>
      <div className="row wrap">
        <button type="button" className="btn" disabled={!ph.password} onClick={() => savePhone()}>Save phone line</button>
        <button type="button" className="btn" onClick={savePolicy}>Save call limits</button>
        {p.provider && p.verified && <button type="button" className="btn" disabled={!ph.password} onClick={() => savePhone({ enabled: !p.enabled })}>{p.enabled ? "Turn calls off" : "Turn calls on"}</button>}
      </div>
      {p.provider && p.number && !p.verified && (
        <div className="col">
          <div className="small">Verify the number: ROOKS calls it once and reads a 6 digit code.</div>
          {p.provider === "twilio" && <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={cost} onChange={(e) => setCost(e.target.checked)} />
            I understand this places one real call billed to my Twilio account</label>}
          <div className="row wrap">
            <button type="button" className="btn" disabled={!ph.password || busy || (p.provider === "twilio" && !cost)} onClick={verify}>Call me with a code</button>
            <input className="input" style={{ width: 120 }} aria-label="Verification code" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Code" />
            <button type="button" className="btn primary" disabled={code.length < 6} onClick={confirmCode}>Confirm</button>
          </div>
        </div>
      )}
      {(st.phone_calls.length > 0 || st.invites.length > 0) && (
        <details>
          <summary className="small" style={{ cursor: "pointer" }}>Call history</summary>
          <div className="col small" style={{ marginTop: 6 }}>
            {st.invites.slice(0, 10).map((i) => <div key={`i${i.id}`}>In app · {i.title} · {i.status} · {i.created_at.slice(0, 16).replace("T", " ")}</div>)}
            {st.phone_calls.slice(0, 10).map((c) => <div key={`p${c.id}`}>Phone ({c.provider}) · {c.purpose} · {c.to_masked} · {c.status}{c.error ? ` · ${c.error}` : ""} · {c.created_at.slice(0, 16).replace("T", " ")}</div>)}
          </div>
        </details>
      )}
      {toast.node}
    </div>
  );
}
