import { Check, CircleAlert, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, Model } from "../api";
import { ModelSelect, useToast } from "../util";

type Role = { role: string; label: string; about: string; ref: string; used_by: string; problems: string[] };
type Cap = { state: string; how: string; tokens?: number | null };
type Caps = { ref: string; capabilities?: Record<string, Cap>; error?: string };
const STATE: Record<string, string> = { tested: "ok", reported: "accent", no: "danger", unknown: "" };

/** Which model does which work. A role is refused for a model without evidence of what the role needs. */
export function ModelRoles({ models }: { models: Model[] }) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [look, setLook] = useState("");
  const [caps, setCaps] = useState<Caps | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const load = () => api<{ roles: Role[] }>("/models/roles").then((r) => setRoles(r.roles)).catch(toast.err);
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (look) api<Caps[]>(`/models/capabilities?ref=${encodeURIComponent(look)}`).then((r) => setCaps(r[0])).catch(toast.err); else setCaps(null); }, [look]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = async (role: string, ref: string) => {
    try { setRoles((await api<{ roles: Role[] }>("/models/roles", { method: "PUT", body: { roles: { [role]: ref } } })).roles); toast.ok("Saved"); } catch (e) { toast.err(e); }
  };
  const jsonTest = async () => {
    setBusy(true);
    try { const r = await api<{ ok: boolean; error: string }>("/models/json-check", { body: { ref: look } }); r.ok ? toast.ok("Passed the JSON test") : toast.err(new Error(r.error));
      setCaps((await api<Caps[]>(`/models/capabilities?ref=${encodeURIComponent(look)}`))[0]); }
    catch (e) { toast.err(e); } finally { setBusy(false); }
  };
  return (
    <div className="card col" id="model-roles">
      <h3>Model roles</h3>
      <p className="muted small" style={{ margin: 0 }}>Choose a model for a kind of work. ROOKS only accepts a model it has evidence for, and uses it exactly: if it fails, the work fails visibly instead of switching to another model. Empty means the default model and its backups.</p>
      <div className="col" style={{ gap: 8 }}>
        {roles.map((r) => (
          <div key={r.role} className="col" style={{ gap: 2 }}>
            <div className="row wrap" style={{ gap: 8 }}><strong style={{ minWidth: 110 }}>{r.label}</strong>
              <div className="grow" style={{ minWidth: 200, maxWidth: 360 }}><ModelSelect value={r.ref} onChange={(v) => set(r.role, v)} models={models} defaultLabel="Default" /></div></div>
            <div className="small muted">{r.about}. {r.used_by}.</div>
            {r.problems.length > 0 && <div className="small warn-text"><CircleAlert size={12} /> {r.problems.join("; ")}</div>}
          </div>
        ))}
      </div>
      <details className="small"><summary style={{ cursor: "pointer" }}>What can a model do?</summary>
        <div className="col" style={{ marginTop: 8 }}>
          <ModelSelect value={look} onChange={setLook} models={models} defaultLabel="Pick a model" />
          {caps?.capabilities && (
            <ul className="col" style={{ listStyle: "none", margin: 0, padding: 0, gap: 4 }}>
              {Object.entries(caps.capabilities).map(([k, c]) => (
                <li key={k} className="row wrap" style={{ gap: 6 }}><span style={{ minWidth: 70 }}>{k}</span>
                  <span className={`badge ${STATE[c.state] ?? ""}`}>{c.state === "tested" && <Check size={10} />} {c.state}{c.tokens ? ` (${c.tokens.toLocaleString()} tokens)` : ""}</span>
                  <span className="faint">{c.how}</span></li>
              ))}
            </ul>
          )}
          {look && <div><button className="btn sm" disabled={busy} onClick={jsonTest}>{busy && <Loader2 size={12} className="spin" />} Run the JSON test (one tiny capped request)</button></div>}
        </div>
      </details>
      {toast.node}
    </div>
  );
}
