import { CheckCircle2, CircleDashed, CircleOff, Clock, Loader2, Puzzle, Search, Settings2, Sparkles, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Empty, relTime } from "../util";

type Cap = { id: string; category: string; name: string; about: string; examples: string[]; status: "ready" | "needs_setup" | "off" | "optional" | "not_built";
  kind: string; needs: string; setup: string; note: string; permissions: { id: string; label: string; mode: string }[];
  last_ok: string | null; last_error: { at: string; tool: string; error: string } | null };

const STATUS: Record<Cap["status"], { label: string; tone: string; icon: typeof CheckCircle2 }> = {
  ready: { label: "Ready", tone: "ok", icon: CheckCircle2 },
  needs_setup: { label: "Needs setup", tone: "warn", icon: Settings2 },
  off: { label: "Turned off", tone: "", icon: CircleOff },
  optional: { label: "Optional add-on", tone: "", icon: Puzzle },
  not_built: { label: "Not available yet", tone: "", icon: CircleDashed },
};
const MODE: Record<string, string> = { on: "allowed", ask: "asks first", off: "off" };
const ORDER = ["Research", "Computer Use", "Browser", "Files and Documents", "Productivity", "Communication", "Business Operations", "Automations", "Coding", "Models", "Custom Skills"];

function score(c: Cap, words: string[]): number {
  const hay = `${c.name} ${c.about} ${c.category} ${c.examples.join(" ")} ${c.note}`.toLowerCase();
  return words.reduce((n, w) => n + (hay.includes(w) ? (c.name.toLowerCase().includes(w) ? 3 : 1) : 0), 0);
}

/** What ROOKS can do, with each capability's real state: nothing unavailable is presented as working. */
export default function Capabilities() {
  const [caps, setCaps] = useState<Cap[] | null>(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [only, setOnly] = useState<"" | Cap["status"]>("");
  const navigate = useNavigate();
  useEffect(() => { api<{ capabilities: Cap[] }>("/capabilities").then((r) => setCaps(r.capabilities)).catch((e) => setError((e as Error).message)); }, []);
  const words = q.toLowerCase().split(/\W+/).filter((w) => w.length > 2 && !["the", "and", "can", "you", "for", "how", "what", "with", "rooks", "my"].includes(w));
  const shown = useMemo(() => {
    let list = (caps ?? []).filter((c) => (!cat || c.category === cat) && (!only || c.status === only));
    if (words.length) list = list.map((c) => [c, score(c, words)] as const).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]).map(([c]) => c);
    return list;
  }, [caps, cat, only, q]); // eslint-disable-line react-hooks/exhaustive-deps
  const cats = ORDER.filter((c) => caps?.some((x) => x.category === c));
  const try_ = (text: string) => navigate("/assistant", { state: { ask: text } });

  return (
    <div className="page">
      <div className="page-head"><div><h1>What ROOKS can do</h1><p className="muted">Every capability with its real state right now: ready, needing setup, turned off, an optional add-on, or not available yet.</p></div></div>
      <div className="card col" style={{ marginBottom: 16 }}>
        <div className="row" style={{ position: "relative" }}>
          <Search size={15} className="faint" style={{ position: "absolute", left: 11 }} aria-hidden="true" />
          <input className="input lg" style={{ paddingLeft: 34 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Describe what you want to do, e.g. “send an email” or “sort my files”" aria-label="Search capabilities" />
        </div>
        <div className="row wrap" role="toolbar" aria-label="Filter by area">
          <button type="button" className={`chip ${!cat ? "on" : ""}`} aria-pressed={!cat} onClick={() => setCat("")}>All areas</button>
          {cats.map((c) => <button key={c} type="button" className={`chip ${cat === c ? "on" : ""}`} aria-pressed={cat === c} onClick={() => setCat(cat === c ? "" : c)}>{c}</button>)}
        </div>
        {caps && <div className="row wrap small" role="toolbar" aria-label="Filter by state">
          {(["", "ready", "needs_setup", "optional", "off", "not_built"] as const).map((s) => {
            const n = s ? caps.filter((c) => c.status === s).length : caps.length;
            return n || !s ? <button key={s} type="button" className={`btn sm ${only === s ? "primary" : "ghost"}`} aria-pressed={only === s} onClick={() => setOnly(s)}>{s ? STATUS[s].label : "Everything"} ({n})</button> : null;
          })}
        </div>}
      </div>
      {error && <div className="alert err" role="alert">{error}</div>}
      {!caps && !error && <div className="cap-grid">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className="skeleton" style={{ height: 150 }} />)}</div>}
      {caps && shown.length === 0 && <div className="card"><Empty icon={<Sparkles size={22} />} title="Nothing matches" hint="Try other words, or ask ROOKS in chat: it will say plainly if it cannot do something." /></div>}
      <div className="cap-grid">
        {shown.map((c) => {
          const st = STATUS[c.status];
          return (
            <article key={c.id} className={`cap-card ${c.status}`} aria-labelledby={`cap-${c.id}`}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                <div className="grow"><h3 id={`cap-${c.id}`}>{c.name}</h3><div className="faint small">{c.category}</div></div>
                <span className={`badge ${st.tone}`}><st.icon size={11} aria-hidden="true" /> {st.label}</span>
              </div>
              <p className="small" style={{ margin: 0 }}>{c.about}</p>
              {c.note && <p className="small muted" style={{ margin: 0 }}>{c.note}</p>}
              {c.needs && <p className="small" style={{ margin: 0, color: "var(--warn)" }}>{c.needs}</p>}
              {c.examples.length > 0 && c.status !== "not_built" && (
                <div className="col" style={{ gap: 4 }}>
                  {c.examples.map((e) => c.status === "ready"
                    ? <button key={e} type="button" className="cap-example" onClick={() => try_(e)} title="Ask ROOKS this now">“{e}”</button>
                    : <span key={e} className="cap-example muted">“{e}”</span>)}
                </div>
              )}
              {c.permissions.length > 0 && <div className="small muted">Permission: {c.permissions.map((p) => `${p.label} ${MODE[p.mode] ?? p.mode}`).join(", ")} · <Link to="/settings#permissions">change</Link></div>}
              {(c.last_ok || c.last_error) && <div className="small faint row wrap" style={{ gap: 10 }}>
                {c.last_ok && <span><Clock size={11} aria-hidden="true" /> Last worked {relTime(c.last_ok)}</span>}
                {c.last_error && <span style={{ color: "var(--danger)" }} title={c.last_error.error}><XCircle size={11} aria-hidden="true" /> Last problem {relTime(c.last_error.at)}: {c.last_error.error.slice(0, 80)}</span>}
              </div>}
              <div className="row wrap" style={{ marginTop: "auto" }}>
                {c.status !== "ready" && c.status !== "not_built" && c.setup && <Link className="btn sm primary" to={c.setup}>Set up</Link>}
                {c.status === "ready" && c.setup && <Link className="btn sm ghost" to={c.setup}>Open</Link>}
              </div>
            </article>
          );
        })}
      </div>
      {!caps && !error && <div className="sr-only" role="status"><Loader2 /> Loading</div>}
    </div>
  );
}
