import { Smartphone, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../util";

type Lan = { available: boolean; enabled: boolean; running: boolean; port: number; addresses: string[]; error: string };
type Pairing = { code: string; expires_in: number; urls: string[]; qr: string; qr_svg: string; lan: Lan; relay_url: string };
type Device = { id: number; name: string; platform: string; created_at: string; last_seen: string | null; push: number };
type MobileSettings = { lan: Lan; relay_url: string; push_enabled: boolean };

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "not yet");

function statusLine(cfg: MobileSettings, devices: Device[]): string {
  const home = cfg.lan.running;
  const away = !!cfg.relay_url;
  if (!devices.length) {
    if (home) return "Pair a phone on this Wi-Fi. The work stays on this computer.";
    if (away) return "A phone can reach this computer from anywhere. The work stays here.";
    if (cfg.lan.available) return "Pair a phone on this Wi-Fi. The work stays on this computer.";
    return "Open Rooks on this computer to pair a phone on the same Wi-Fi.";
  }
  if (home && away) return "Reachable on this Wi-Fi, and from anywhere.";
  if (home) return "Reachable on this Wi-Fi.";
  if (away) return "Reachable from anywhere.";
  if (cfg.lan.available) return "Paired. Allow this Wi-Fi so the phone can reach this computer.";
  return "Paired. Open Rooks on this computer so the phone can reach it on your Wi-Fi.";
}

/** Settings → Phone: pair by a code, then a status line. Addresses stay under Advanced. */
export function PhoneCard() {
  const toast = useToast();
  const [pair, setPair] = useState<Pairing | null>(null);
  const [left, setLeft] = useState(0);
  const [devices, setDevices] = useState<Device[]>([]);
  const [cfg, setCfg] = useState<MobileSettings | null>(null);
  const [relay, setRelay] = useState("");
  const [blocked, setBlocked] = useState("");

  const load = useCallback(async () => {
    setDevices(await api<Device[]>("/mobile/devices"));
    const s = await api<MobileSettings>("/mobile/settings");
    setCfg(s);
    setRelay(s.relay_url);
  }, []);
  useEffect(() => { load().catch(() => undefined); }, [load]);

  useEffect(() => {
    if (!pair) return;
    const end = Date.now() + pair.expires_in * 1000;
    const t = window.setInterval(() => {
      const s = Math.max(0, Math.round((end - Date.now()) / 1000));
      setLeft(s);
      if (s === 0) setPair(null);
      if (s % 3 === 0) api<Device[]>("/mobile/devices").then((d) => {
        setDevices((prev) => {
          if (d.length > prev.length) { setPair(null); toast.ok(`${d[0].name} is paired`); }
          return d;
        });
      }).catch(() => undefined);
    }, 1000);
    setLeft(pair.expires_in);
    return () => window.clearInterval(t);
  }, [pair]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (body: Partial<{ lan_enabled: boolean; relay_url: string; push_enabled: boolean }>, quiet = false) => {
    const s = await api<MobileSettings>("/mobile/settings", { method: "PUT", body });
    setCfg(s);
    setRelay(s.relay_url);
    if (!quiet) toast.ok("Saved");
    return s;
  };

  const start = async () => {
    setBlocked("");
    try {
      let settings = cfg;
      if (settings?.lan.available && !settings.lan.enabled) settings = await save({ lan_enabled: true }, true);
      if (!settings?.lan.running && !settings?.relay_url) {
        setBlocked(settings?.lan.available
          ? "The Wi-Fi connection didn't start. Try again in a moment."
          : "This page is only on this computer. Open Rooks on this computer, then pair the phone from there.");
        return;
      }
      const next = await api<Pairing>("/mobile/pair/start", { method: "POST" });
      if (!next.urls.length) {
        setBlocked("This computer has no address a phone can use yet.");
        return;
      }
      setPair(next);
    } catch (e) { toast.err(e); }
  };

  const revoke = async (d: Device) => {
    try { await api(`/mobile/devices/${d.id}`, { method: "DELETE" }); toast.ok(`${d.name} can no longer reach this workspace`); load(); }
    catch (e) { toast.err(e); }
  };

  return (
    <div className="card col gap-lg" id="phone">
      <div className="row"><Smartphone size={16} /><h2>Phone</h2></div>
      {cfg && <p className="muted" style={{ margin: 0 }}>{statusLine(cfg, devices)}</p>}
      {blocked && <div className="alert">{blocked}</div>}
      {pair ? (
        <div className="phone-pair">
          <div className="phone-qr" dangerouslySetInnerHTML={{ __html: pair.qr_svg }} aria-label="Pairing code" />
          <div className="col gap">
            <div className="phone-code mono">{pair.code}</div>
            <p className="muted small" style={{ margin: 0 }}>
              Open Rooks on the phone and scan this. The phone should be on this Wi-Fi.
              {pair.relay_url ? " It can also reach this computer from somewhere else." : ""}
              {" "}Expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}.
            </p>
            <div><button type="button" className="btn ghost sm" onClick={() => setPair(null)}>Cancel</button></div>
          </div>
        </div>
      ) : (
        <div><button type="button" className="btn primary" onClick={start}>Pair a phone</button></div>
      )}

      {devices.length > 0 && (
        <div className="card flush">
          {devices.map((d) => (
            <div key={d.id} className="list-item">
              <div className="grow">
                <div><strong>{d.name}</strong> <span className="muted small">{d.platform || "phone"}{d.push ? " · notifications on" : ""}</span></div>
                <div className="muted small">Paired {when(d.created_at)} · last seen {when(d.last_seen)}</div>
              </div>
              <button type="button" className="btn ghost danger sm" onClick={() => revoke(d)}><Trash2 size={13} /> Unpair</button>
            </div>
          ))}
        </div>
      )}

      {cfg && devices.length > 0 && (
        <label className="row small">
          <input type="checkbox" checked={cfg.push_enabled} onChange={(e) => save({ push_enabled: e.target.checked }).catch(toast.err)} />
          Tell this phone when something needs you
        </label>
      )}

      {cfg && (
        <details className="settings-more">
          <summary>Advanced</summary>
          <div className="col gap" style={{ marginTop: 12 }}>
            {cfg.lan.available && (
              <label className="row small">
                <input type="checkbox" checked={cfg.lan.enabled} onChange={(e) => save({ lan_enabled: e.target.checked }).catch(toast.err)} />
                Allow a phone on this Wi-Fi
                <span className="muted">{cfg.lan.running ? "On" : cfg.lan.error ? "Didn't start" : "Off"}</span>
              </label>
            )}
            <label className="field small">Private address <span className="hint">Only if you already have one the phone can open.</span>
              <div className="row">
                <input className="input grow" value={relay} placeholder="https://" onChange={(e) => setRelay(e.target.value)} aria-label="Private address" />
                <button type="button" className="btn" disabled={relay === cfg.relay_url} onClick={() => save({ relay_url: relay }).catch(toast.err)}>Save</button>
              </div>
            </label>
          </div>
        </details>
      )}
      {toast.node}
    </div>
  );
}
