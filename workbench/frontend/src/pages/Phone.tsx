import { Smartphone, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../util";

type Lan = { available: boolean; enabled: boolean; running: boolean; port: number; addresses: string[]; error: string };
type Pairing = { code: string; expires_in: number; urls: string[]; qr: string; qr_svg: string; lan: Lan; relay_url: string };
type Device = { id: number; name: string; platform: string; created_at: string; last_seen: string | null; push: number };
type MobileSettings = { lan: Lan; relay_url: string; push_enabled: boolean };

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "never");

/** Settings → Phone: pair the iPhone / Android companion app by QR code, list and revoke paired phones. */
export function PhoneCard() {
  const toast = useToast();
  const [pair, setPair] = useState<Pairing | null>(null);
  const [left, setLeft] = useState(0);
  const [devices, setDevices] = useState<Device[]>([]);
  const [cfg, setCfg] = useState<MobileSettings | null>(null);
  const [relay, setRelay] = useState("");

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

  const start = async () => { try { setPair(await api<Pairing>("/mobile/pair/start", { method: "POST" })); } catch (e) { toast.err(e); } };
  const update = async (body: Partial<{ lan_enabled: boolean; relay_url: string; push_enabled: boolean }>) => {
    try { const s = await api<MobileSettings>("/mobile/settings", { method: "PUT", body }); setCfg(s); setRelay(s.relay_url); toast.ok("Saved"); }
    catch (e) { toast.err(e); }
  };
  const revoke = async (d: Device) => {
    try { await api(`/mobile/devices/${d.id}`, { method: "DELETE" }); toast.ok(`${d.name} can no longer reach this workspace`); load(); }
    catch (e) { toast.err(e); }
  };

  return (
    <div className="card col gap-lg" id="phone">
      <div className="row"><Smartphone size={16} /><h2>Phone</h2><span className="badge">iPhone · Android</span></div>
      <div className="muted small">
        Chat, approve actions and follow runs from your phone. The work still happens here; the phone is the remote.
        Install the companion app (Expo Go for now), then scan the code.
      </div>
      {pair ? (
        <div className="phone-pair">
          <div className="phone-qr" dangerouslySetInnerHTML={{ __html: pair.qr_svg }} aria-label="Pairing QR code" />
          <div className="col gap">
            <div className="label">Pairing code</div>
            <div className="phone-code mono">{pair.code}</div>
            <div className="muted small">Expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}. Works once.</div>
            <div className="label" style={{ marginTop: 12 }}>The phone will try</div>
            {pair.urls.length ? pair.urls.map((u) => <div key={u} className="mono small">{u}</div>) : (
              <div className="small danger-text">
                No address a phone can reach yet. {cfg?.lan.available ? "Turn on phone access on this Wi-Fi below" : "Open this page on your computer's network address, set MOBILE_URLS,"} or add a relay address.
              </div>
            )}
            <div><button className="btn ghost sm" onClick={() => setPair(null)}>Cancel</button></div>
          </div>
        </div>
      ) : (
        <div><button className="btn primary" onClick={start}>Pair a phone</button></div>
      )}

      {cfg && (
        <div className="col gap">
          {cfg.lan.available && (
            <label className="row small">
              <input type="checkbox" checked={cfg.lan.enabled} onChange={(e) => update({ lan_enabled: e.target.checked })} />
              Phone access on this Wi-Fi
              <span className="muted">
                {cfg.lan.running ? `· listening on port ${cfg.lan.port} (${cfg.lan.addresses.join(", ") || "no network"}), phone routes only` : cfg.lan.error ? `· ${cfg.lan.error}` : "· off"}
              </span>
            </label>
          )}
          <label className="field small">Away from home <span className="hint">A Tailscale, Cloudflare Tunnel or relay address for this workspace, e.g. https://desk.your-tailnet.ts.net</span>
            <div className="row">
              <input className="input grow" value={relay} placeholder="https://" onChange={(e) => setRelay(e.target.value)} />
              <button className="btn" disabled={relay === cfg.relay_url} onClick={() => update({ relay_url: relay })}>Save</button>
            </div>
          </label>
          <label className="row small">
            <input type="checkbox" checked={cfg.push_enabled} onChange={(e) => update({ push_enabled: e.target.checked })} />
            Push notifications
            <span className="muted">· approval needed, run finished. Needs push credentials in the app build (see mobile/README.md)</span>
          </label>
        </div>
      )}

      {devices.length > 0 && (
        <div className="card flush">
          {devices.map((d) => (
            <div key={d.id} className="list-item">
              <div className="grow">
                <div><strong>{d.name}</strong> <span className="muted small">{d.platform || "phone"}{d.push ? " · notifications on" : ""}</span></div>
                <div className="muted small">Paired {when(d.created_at)} · last seen {when(d.last_seen)}</div>
              </div>
              <button className="btn ghost danger sm" onClick={() => revoke(d)} title="Unpair"><Trash2 size={13} /> Unpair</button>
            </div>
          ))}
        </div>
      )}
      {toast.node}
    </div>
  );
}
