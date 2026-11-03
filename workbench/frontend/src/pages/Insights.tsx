import { LineChart } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, SkeletonList } from "../util";

// Phase 3: trends from the run log (GET /api/usage/daily). Zero dependencies: bars are plain SVG.
type Day = { day: string; calls: number; failures: number; tokens_in: number; tokens_out: number; usd: number | null; unpriced: number; avg_ms: number; p95_ms: number };
type Provider = { provider: string; calls: number; failures: number; avg_ms: number | null };
type Daily = { days: Day[]; providers: Provider[]; channels: { channel: string; calls: number }[];
  totals: { calls: number; failures: number; tokens_in: number; tokens_out: number; unpriced: number; usd: number | null } };

const money = (v: number | null) => (v === null ? "unpriced" : v > 0 && v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const secs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "0%");

function Bars({ days, value, fmt, danger }: { days: Day[]; value: (d: Day) => number; fmt: (n: number) => string; danger?: (d: Day) => number }) {
  const max = Math.max(1, ...days.map(value));
  const w = 100 / days.length;
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="insights-bars" role="img">
      {days.map((d, i) => {
        const h = (value(d) / max) * 38;
        const bad = danger ? Math.min(h, (danger(d) / max) * 38) : 0;
        return (
          <g key={d.day}>
            <title>{`${d.day}: ${fmt(value(d))}${danger && danger(d) ? ` (${danger(d)} failed)` : ""}`}</title>
            <rect x={i * w + w * 0.15} y={40 - h} width={w * 0.7} height={h} fill="var(--accent)" opacity={0.75} />
            {bad > 0 && <rect x={i * w + w * 0.15} y={40 - bad} width={w * 0.7} height={bad} fill="var(--danger)" />}
          </g>
        );
      })}
    </svg>
  );
}

function Chart({ title, days, value, fmt, danger, note }: { title: string; days: Day[]; value: (d: Day) => number; fmt: (n: number) => string; danger?: (d: Day) => number; note?: string }) {
  const last = days[days.length - 1];
  return (
    <div className="card insights-chart">
      <div className="row"><b>{title}</b><div className="spacer" /><span className="small muted">today {fmt(value(last))}</span></div>
      <Bars days={days} value={value} fmt={fmt} danger={danger} />
      <div className="row small muted"><span>{days[0].day}</span><div className="spacer" /><span>{last.day}</span></div>
      {note && <div className="small muted">{note}</div>}
    </div>
  );
}

export default function Insights() {
  const [range, setRange] = useState(30);
  const [data, setData] = useState<Daily | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    api<Daily>(`/usage/daily?days=${range}`).then(setData).catch((e) => setError((e as Error).message));
  }, [range]);
  const t = data?.totals;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Insights</h1>
          <p className="muted">How your models have been doing: calls, failures, speed and cost per day, from the run log.</p>
        </div>
        <div className="spacer" />
        <div className="tabs">
          {[7, 30, 90].map((n) => <button key={n} className={`tab ${range === n ? "active" : ""}`} onClick={() => setRange(n)}>{n} days</button>)}
        </div>
      </div>
      {error && <div className="alert err">{error}</div>}
      {!data && !error && <div className="card flush"><SkeletonList rows={4} /></div>}
      {data && t && !t.calls && <Empty icon={<LineChart size={22} />} title="Nothing to chart yet" hint="Chat with the assistant and its model calls show up here." />}
      {data && t && t.calls > 0 && (
        <>
          <div className="insights-stats">
            <div className="card"><div className="small muted">Model calls</div><b>{t.calls.toLocaleString()}</b></div>
            <div className="card"><div className="small muted">Failed</div><b>{t.failures.toLocaleString()} ({pct(t.failures, t.calls)})</b></div>
            <div className="card"><div className="small muted">Tokens</div><b>{(t.tokens_in + t.tokens_out).toLocaleString()}</b></div>
            <div className="card"><div className="small muted">Cost</div><b>{money(t.usd)}</b>{t.unpriced > 0 && <div className="small muted">+ {t.unpriced} unpriced calls</div>}</div>
          </div>
          <div className="insights-grid">
            <Chart title="Calls per day" days={data.days} value={(d) => d.calls} fmt={(n) => `${n} calls`} danger={(d) => d.failures} note="Red: calls that failed (the router may have answered from another provider)." />
            <Chart title="Tokens per day" days={data.days} value={(d) => d.tokens_in + d.tokens_out} fmt={(n) => n.toLocaleString()} />
            <Chart title="Cost per day" days={data.days} value={(d) => d.usd ?? 0} fmt={(n) => money(n)} note="Local and unknown-price models count as unpriced, not free." />
            <Chart title="Slowest typical answer (p95)" days={data.days} value={(d) => d.p95_ms} fmt={secs} note="95% of successful calls that day were faster than this." />
          </div>
          <div className="insights-grid">
            <div className="card flush">
              <div className="list-item"><b>Providers</b></div>
              {data.providers.map((p) => (
                <div key={p.provider || "?"} className="list-item">
                  <div className="grow truncate">{p.provider || "unknown"}</div>
                  <div className="small muted">{p.calls} calls · {pct(p.failures, p.calls)} failed{p.avg_ms ? ` · avg ${secs(p.avg_ms)}` : ""}</div>
                </div>
              ))}
            </div>
            <div className="card flush">
              <div className="list-item"><b>Where calls come from</b></div>
              {data.channels.map((c) => (
                <div key={c.channel || "?"} className="list-item"><div className="grow truncate">{c.channel || "other"}</div><div className="small muted">{c.calls} calls</div></div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
