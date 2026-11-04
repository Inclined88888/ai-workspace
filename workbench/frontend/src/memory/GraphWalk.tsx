// B2: walk the knowledge graph from one entity: who and what it connects to, how many hops away, and by which links.
import { ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";

type Step = { from: number; rel: string; to: number };
type WalkNode = { id: number; type: string; name: string; rel: string; direction: "in" | "out"; hops: number; path: Step[] };
export type Walk = { start: number; depth: number; direction: string; rel: string | null; nodes: WalkNode[]; truncated: boolean };

const nice = (rel: string) => rel.replace(/_/g, " ");

export default function GraphWalk({ id, name }: { id: number; name: string }) {
  const [depth, setDepth] = useState(2);
  const [direction, setDirection] = useState("both");
  const [rel, setRel] = useState("");
  const [walk, setWalk] = useState<Walk | null>(null);
  const [err, setErr] = useState("");
  const [, setParams] = useSearchParams();

  useEffect(() => {
    const q = new URLSearchParams({ eid: String(id), depth: String(depth), direction });
    if (rel.trim()) q.set("rel", rel.trim());
    api<Walk>(`/knowledge/graph/traverse?${q}`).then((w) => { setWalk(w); setErr(""); }).catch((e) => setErr(String(e?.message ?? e)));
  }, [id, depth, direction, rel]);

  const names: Record<number, string> = { [id]: name };
  walk?.nodes.forEach((n) => { names[n.id] = n.name; });
  const byHop = (walk?.nodes ?? []).reduce<Record<number, WalkNode[]>>((a, n) => ((a[n.hops] ??= []).push(n), a), {});

  return (
    <section className="kn-section" aria-labelledby="graph-walk-h">
      <h3 id="graph-walk-h">Connections</h3>
      <div className="row gap graph-walk-controls">
        <label className="field">How far
          <select className="input" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {[1, 2, 3, 4].map((d) => <option key={d} value={d}>{d === 1 ? "Direct links" : `${d} steps`}</option>)}
          </select>
        </label>
        <label className="field">Direction
          <select className="input" value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="both">Both ways</option><option value="out">From {name}</option><option value="in">To {name}</option>
          </select>
        </label>
        <label className="field">Only this link <input className="input" placeholder="e.g. works_at" value={rel} onChange={(e) => setRel(e.target.value)} /></label>
      </div>
      <div aria-live="polite">
        {err && <div role="alert" className="muted">Could not walk the graph: {err}</div>}
        {walk && !walk.nodes.length && <div className="muted">No connections{rel.trim() ? ` by "${nice(rel.trim())}"` : ""} within {depth} step{depth > 1 ? "s" : ""}.</div>}
        {Object.entries(byHop).map(([hop, list]) => (
          <div key={hop} className="graph-hop">
            <h4 className="kn-sub">{hop === "1" ? "Directly linked" : `${hop} steps away`} ({list.length})</h4>
            <ul className="graph-list">
              {list.map((n) => (
                <li key={n.id} className="kn-row">
                  <button className="btn ghost sm" onClick={() => setParams({ entity: String(n.id) })}>{n.name}</button>
                  <span className="kn-sub">{n.type}</span>
                  <span className="graph-path grow" aria-label={`Path: ${n.path.map((s) => `${names[s.from] ?? s.from} ${nice(s.rel)} ${names[s.to] ?? s.to}`).join(", then ")}`}>
                    {n.path.map((s, i) => <span key={i} className="graph-step">{names[s.from] ?? s.from} <ArrowRight size={12} aria-hidden /> <em>{nice(s.rel)}</em> <ArrowRight size={12} aria-hidden /> {names[s.to] ?? s.to}</span>)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
        {walk?.truncated && <div className="muted small">Showing the first {walk.nodes.length}. Narrow it with a link type or fewer steps.</div>}
      </div>
    </section>
  );
}
