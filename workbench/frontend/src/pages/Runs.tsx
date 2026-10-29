import { ArrowLeft, ChevronDown, ChevronRight, ListTree, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime } from "../util";

// One row of the run log (backend actions.py). Text fields are already redacted on the server.
type Action = {
  id: number; ts: string; kind: "turn" | "model_call" | "tool_call" | "decision"; channel: string; conversation_id: number | null;
  provider: string; model: string; auth_kind: string; protocol: string; tool: string; effect: string; args: string;
  latency_ms: number; tokens_in: number; tokens_out: number; usd_cost: number | null; outcome: string; failure_reason: string;
  verified: number | null; evidence: string; detail: string; from_text: number; request_id: string; parent_action_id: number | null;
};
type Totals = { steps: number; model_calls: number; tool_calls: number; total_tokens_in: number; total_tokens_out: number; total_usd: number | null; unpriced: number; failures: number };
type Run = Action & Totals & { conversation_title: string | null };
type Node = Action & { children: Node[] };

const GOOD = new Set(["ok", "verified", "backed", "fixed", "no_claim", "approved"]);
const PENDING = new Set(["running", "held"]);
const tone = (o: string) => (GOOD.has(o) ? "ok" : PENDING.has(o) ? "warn" : o === "budget_paused" ? "warn" : "danger");
const usd = (v: number | null, unpriced = 0) => (v === null ? (unpriced ? "price unknown" : "$0") : `$${v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2)}${unpriced ? " + unpriced" : ""}`);
const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
const KIND = { turn: "Turn", model_call: "Model call", tool_call: "Tool call", decision: "Your decision" };

function title(r: Run) {
  if (r.kind === "turn") return r.conversation_title || `Turn #${r.id}`;
  return `${KIND[r.kind]} · ${r.channel || "unknown"}`;
}

export default function Runs() {
  const { id } = useParams();
  return id ? <Trace id={Number(id)} /> : <RunList />;
}

function RunList() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [channels, setChannels] = useState<string[]>([]);
  const [channel, setChannel] = useState("");
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  const load = (before?: number) => {
    setLoading(true);
    const q = new URLSearchParams({ limit: "50", ...(channel && { channel }), ...(failed && { failed: "true" }), ...(before && { before: String(before) }) });
    api<{ runs: Run[]; channels: string[] }>(`/runs?${q}`)
      .then((r) => {
        setRuns((prev) => (before ? [...prev, ...r.runs] : r.runs));
        setChannels(r.channels);
        setMore(r.runs.length === 50);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(() => load(), [channel, failed]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Run log</h1>
          <p className="muted">Every turn and model call, with the tool calls and approvals under it. Read from the one run log; secrets are redacted before they are stored.</p>
        </div>
        <div className="spacer" />
        <select className="input" style={{ width: "auto" }} value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="Channel">
          <option value="">All channels</option>
          {channels.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <div className="tabs">
          <button className={`tab ${!failed ? "active" : ""}`} onClick={() => setFailed(false)}>All</button>
          <button className={`tab ${failed ? "active" : ""}`} onClick={() => setFailed(true)}>With failures</button>
        </div>
      </div>
      {error && <div className="alert err" style={{ marginBottom: 12 }}>{error}</div>}
      {!loading && !runs.length && !error && <Empty icon={<ListTree size={22} />} title="No runs yet" hint={failed ? "Nothing failed." : "Chat with the assistant and its runs appear here."} />}
      {runs.length > 0 && (
        <div className="card flush">
          {runs.map((r) => (
            <a key={r.id} className="list-item run-item" href={`/runs/${r.id}`} onClick={(e) => { e.preventDefault(); navigate(`/runs/${r.id}`); }}>
              <span className={`badge ${tone(r.outcome)}`}>{r.outcome}</span>
              <div className="grow">
                <div className="truncate">{title(r)}</div>
                <div className="small muted truncate">
                  {r.channel || "no channel"} · {r.model || "no model"} · {r.model_calls} model / {r.tool_calls} tool calls
                  {r.failures ? ` · ${r.failures} failed` : ""}
                </div>
              </div>
              <div className="small muted run-meta">
                <div>{usd(r.total_usd, r.unpriced)} · {(r.total_tokens_in + r.total_tokens_out).toLocaleString()} tokens</div>
                <div>{relTime(r.ts)}{r.latency_ms ? ` · ${secs(r.latency_ms)}` : ""}</div>
              </div>
            </a>
          ))}
        </div>
      )}
      {loading && <div className="row muted" style={{ marginTop: 12 }}><Loader2 size={14} className="spin" /> Loading</div>}
      {more && !loading && <button className="btn" style={{ marginTop: 12 }} onClick={() => load(runs[runs.length - 1].id)}>Load older runs</button>}
    </div>
  );
}

function Trace({ id }: { id: number }) {
  const [run, setRun] = useState<(Node & Totals & { conversation_title: string | null }) | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setRun(null);
    api<Node & Totals & { conversation_title: string | null }>(`/runs/${id}`).then(setRun).catch((e) => setError(e.message));
  }, [id]);

  return (
    <div className="page">
      <div className="page-head">
        <Link className="btn ghost sm" to="/runs"><ArrowLeft size={14} /> Run log</Link>
      </div>
      {error && <div className="alert err">{error}</div>}
      {!run && !error && <div className="row muted"><Loader2 size={14} className="spin" /> Loading</div>}
      {run && (
        <>
          <div className="page-head">
            <div>
              <h1>{run.kind === "turn" ? run.conversation_title || `Turn #${run.id}` : `${KIND[run.kind]} #${run.id}`}</h1>
              <p className="muted">
                {new Date(run.ts).toLocaleString()} · {run.channel || "no channel"}
                {run.conversation_id && <> · <Link to={`/assistant/${run.conversation_id}`}>open conversation</Link></>}
              </p>
            </div>
          </div>
          <div className="grid three" style={{ marginBottom: 16 }}>
            <div className="card stat"><span className="muted small">Outcome</span><span><span className={`badge ${tone(run.outcome)}`}>{run.outcome}</span>{run.failures ? <span className="small muted"> · {run.failures} failed {run.failures === 1 ? "step" : "steps"}</span> : null}</span></div>
            <div className="card stat"><span className="muted small">Cost</span><span>{usd(run.total_usd, run.unpriced)}</span><span className="small muted">{run.total_tokens_in.toLocaleString()} in · {run.total_tokens_out.toLocaleString()} out</span></div>
            <div className="card stat"><span className="muted small">Steps</span><span>{run.model_calls} model · {run.tool_calls} tool</span><span className="small muted">{run.latency_ms ? `${secs(run.latency_ms)} total` : ""}</span></div>
          </div>
          <div className="card flush trace">
            <TraceNode node={run} depth={0} />
          </div>
        </>
      )}
    </div>
  );
}

function TraceNode({ node, depth }: { node: Node; depth: number }) {
  const [open, setOpen] = useState(node.outcome !== "ok" && node.outcome !== "verified" && depth > 0);
  const label = node.kind === "tool_call" ? node.tool : node.kind === "model_call" ? node.model : node.kind === "decision" ? `${node.outcome} ${node.tool}` : node.model || "no model";
  const facts: [string, string][] = [
    ["Provider", node.provider && `${node.provider} (${node.auth_kind}, ${node.protocol})`],
    ["Effect", node.effect],
    ["Arguments", node.args],
    ["Failure", node.failure_reason],
    ["Receipt", node.evidence + (node.verified === 0 ? " (contradicted)" : node.verified === 1 ? " (confirmed)" : "")],
    ["Detail", node.detail],
    ["Recovered from text", node.from_text ? "yes: the model wrote this call as text, not as a tool call" : ""],
    ["Request id", node.request_id],
  ].filter(([, v]) => v && v.trim()) as [string, string][];
  return (
    <>
      <div className="trace-row" style={{ paddingLeft: 14 + depth * 20 }} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="badge">{KIND[node.kind]}</span>
        <span className="truncate grow mono">{label}</span>
        <span className={`badge ${tone(node.outcome)}`}>{node.outcome}</span>
        <span className="small muted trace-meta">
          {node.latency_ms ? secs(node.latency_ms) : ""}
          {node.kind === "model_call" ? ` · ${node.tokens_in}/${node.tokens_out} tok · ${usd(node.usd_cost, node.usd_cost === null ? 1 : 0)}` : ""}
        </span>
      </div>
      {open && (
        <dl className="trace-facts" style={{ marginLeft: 36 + depth * 20 }}>
          {facts.map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd className={k === "Arguments" || k === "Request id" ? "mono" : ""}>{v}</dd></div>
          ))}
          {!facts.length && <div className="muted small">Nothing else recorded.</div>}
        </dl>
      )}
      {node.children.map((c) => <TraceNode key={c.id} node={c} depth={depth + 1} />)}
    </>
  );
}
