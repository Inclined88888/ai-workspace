import { Activity, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, relTime } from "../util";

// Health of every moving part (backend health.py). Checked only when this page asks: no background probes (R8).
type Check = { name: string; kind: string; status: "ok" | "degraded" | "down" | "off"; detail: string; latency_ms: number };
type Report = { status: "ok" | "degraded" | "down"; checked_at: string; checks: Check[] };
type Host = { host: string; effect: "local" | "external"; components: string; requests: number; failures: number; last: string };
type Egress = { hours: number; local_only: boolean; external_requests: number; hosts: Host[] };

const TONE = { ok: "ok", degraded: "warn", down: "danger", off: "" } as const;
const LABEL = { ok: "Working", degraded: "Degraded", down: "Down", off: "Not set up" } as const;
const GROUPS: [string, string][] = [["core", "Workbench"], ["model connection", "Model connections"], ["add-on", "Add-ons"], ["integration", "Integrations"]];

export default function Status() {
  const [report, setReport] = useState<Report | null>(null);
  const [egress, setEgress] = useState<Egress | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = (fresh: boolean) => {
    setBusy(true);
    api<Report>(`/health${fresh ? "?fresh=true" : ""}`)
      .then((r) => { setReport(r); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => {
        setBusy(false);
        api<Egress>("/egress?hours=24").then(setEgress).catch(() => setEgress(null)); // after the probes, so they are counted
      });
  };
  useEffect(() => load(false), []);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>System status</h1>
          <p className="muted">Each part is checked when you open this page or press Check again, never in the background. Paid search APIs are not probed.</p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => load(true)} disabled={busy}>
          {busy ? <Loader2 size={15} className="spin" /> : <RefreshCw size={15} />} Check again
        </button>
      </div>
      {error && <div className="alert err" style={{ marginBottom: 12 }}>{error}</div>}
      {!report && !error && <Empty icon={<Activity size={22} />} title="Checking…" />}
      {report && (
        <>
          <div className={`alert ${report.status === "ok" ? "ok" : report.status === "down" ? "err" : ""}`} style={{ marginBottom: 16 }}>
            {report.status === "ok" ? "Everything that is set up is working." : report.status === "down" ? "Something is down. Details below." : "Working, with problems. Details below."}
            <span className="small" style={{ marginLeft: 8, opacity: 0.8 }}>Checked {relTime(report.checked_at)}</span>
          </div>
          {GROUPS.map(([kind, title]) => {
            const rows = report.checks.filter((c) => c.kind === kind);
            if (!rows.length) return null;
            return (
              <div key={kind} style={{ marginBottom: 16 }}>
                <h3 className="muted small" style={{ margin: "0 0 6px" }}>{title}</h3>
                <div className="card flush">
                  {rows.map((c) => (
                    <div key={c.name} className="list-item run-item" style={{ cursor: "default" }}>
                      <span className={`badge ${TONE[c.status]}`}>{LABEL[c.status]}</span>
                      <div className="grow">
                        <div className="truncate">{c.name}</div>
                        {c.detail && <div className="small muted" style={{ overflowWrap: "anywhere" }}>{c.detail}</div>}
                      </div>
                      {c.latency_ms > 0 && <div className="small muted run-meta">{c.latency_ms} ms</div>}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </>
      )}
      {egress && (
        <div style={{ marginBottom: 16 }}>
          <h3 className="muted small" style={{ margin: "0 0 6px" }}>Outbound connections, last {egress.hours} hours</h3>
          <div className={`alert ${egress.local_only ? "ok" : ""}`} style={{ marginBottom: 8 }}>
            {egress.local_only
              ? "Local only: Workbench sent nothing beyond this machine or its private network."
              : `${egress.external_requests} requests went to the internet, to ${egress.hosts.filter((h) => h.effect === "external").length} hosts.`}
            <span className="small" style={{ marginLeft: 8, opacity: 0.8 }}>From the audit log of every connection Workbench makes. Pages the browser add-on visits are listed in its own job steps, not here.</span>
          </div>
          {egress.hosts.length > 0 && (
            <div className="card flush">
              {egress.hosts.map((h) => (
                <div key={`${h.host}-${h.effect}`} className="list-item run-item" style={{ cursor: "default" }}>
                  <span className={`badge ${h.effect === "external" ? "warn" : ""}`}>{h.effect === "external" ? "Internet" : "Local"}</span>
                  <div className="grow">
                    <div className="truncate mono">{h.host}</div>
                    <div className="small muted truncate">from {h.components.split(",").join(", ")}</div>
                  </div>
                  <div className="small muted run-meta">
                    <div>{h.requests} requests{h.failures ? `, ${h.failures} failed` : ""}</div>
                    <div>last {relTime(h.last)}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
