import { Building2, Pause, Play, Plus, Trash2, UserPlus, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, Markdown, ModelSelect, relTime, useModels, useToast } from "../util";

type Agent = { id: number; name: string; title: string; reports_to: number | null; model: string; instructions: string; monthly_budget_cents: number;
  monthly_token_limit: number; status: string; paused_reason: string; month: { cents: number; tokens: number; unpriced_calls: number } };
type Task = { id: number; title: string; description: string; agent_id: number | null; agent_name: string | null; status: string; result: string;
  created_by: string; conversation_id: number | null; updated_at: string };
type Overview = { mission: string; agents: Agent[]; tasks: Task[] };

const COLUMNS: [string, string[]][] = [["To do", ["todo"]], ["Queued", ["queued"]], ["Running", ["running"]], ["Done", ["done"]], ["Needs you", ["failed", "blocked"]]];
const blank = { name: "", title: "", reports_to: null as number | null, model: "", instructions: "", monthly_budget_cents: 500, monthly_token_limit: 0 };

function OrgNode({ agent, all, onEdit }: { agent: Agent; all: Agent[]; onEdit: (a: Agent) => void }) {
  const reports = all.filter((a) => a.reports_to === agent.id);
  const pct = agent.monthly_budget_cents ? Math.min(100, (agent.month.cents / agent.monthly_budget_cents) * 100) : 0;
  return (
    <div className="col" style={{ alignItems: "center", gap: 10 }}>
      <div className="card" style={{ width: 200, padding: 12, cursor: "pointer", borderColor: agent.status === "paused" ? "var(--danger)" : undefined }} onClick={() => onEdit(agent)}>
        <div className="row"><strong className="grow truncate">{agent.name}</strong>{agent.status === "paused" && <span className="badge danger">paused</span>}</div>
        <div className="muted small truncate">{agent.title || "Team member"}</div>
        <div className="faint small" style={{ marginTop: 6 }}>
          {agent.monthly_budget_cents ? `$${(agent.month.cents / 100).toFixed(2)} of $${(agent.monthly_budget_cents / 100).toFixed(2)}` : `$${(agent.month.cents / 100).toFixed(2)} · no money cap`}
        </div>
        {agent.monthly_budget_cents > 0 && <div className="progress" style={{ marginTop: 4 }}><div style={{ width: `${pct}%`, background: pct >= 80 ? "var(--danger)" : undefined }} /></div>}
      </div>
      {reports.length > 0 && (
        <div className="row" style={{ alignItems: "flex-start", gap: 16, borderTop: "1px solid var(--border-strong)", paddingTop: 10 }}>
          {reports.map((r) => <OrgNode key={r.id} agent={r} all={all} onEdit={onEdit} />)}
        </div>
      )}
    </div>
  );
}

export default function Company() {
  const [data, setData] = useState<Overview>({ mission: "", agents: [], tasks: [] });
  const [mission, setMission] = useState("");
  const [edit, setEdit] = useState<(typeof blank & { id?: number }) | null>(null);
  const [task, setTask] = useState({ title: "", description: "", agent_id: 0 });
  const [open, setOpen] = useState<number | null>(null);
  const { models } = useModels();
  const toast = useToast();

  const load = () => api<Overview>("/company").then((d) => { setData(d); setMission((m) => m || d.mission); }).catch(toast.err);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, []);

  const saveAgent = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    try {
      const body = { ...edit, reports_to: edit.reports_to || null };
      if (edit.id) await api(`/company/agents/${edit.id}`, { method: "PUT", body }); else await api("/company/agents", { body });
      setEdit(null); load();
    } catch (err) { toast.err(err); }
  };
  const addTask = async (start: boolean) => {
    try { await api("/company/tasks", { body: { ...task, agent_id: task.agent_id || null, start } }); setTask({ title: "", description: "", agent_id: task.agent_id }); load(); }
    catch (e) { toast.err(e); }
  };
  const patchTask = async (t: Task, body: Record<string, unknown>) => { try { await api(`/company/tasks/${t.id}`, { method: "PATCH", body }); load(); } catch (e) { toast.err(e); } };
  const setStatus = async (a: Agent, status: string) => { try { await api(`/company/agents/${a.id}/status`, { body: { status } }); load(); } catch (e) { toast.err(e); } };

  const roots = data.agents.filter((a) => !a.reports_to || !data.agents.some((b) => b.id === a.reports_to));
  const spend = data.agents.reduce((s, a) => s + a.month.cents, 0);
  const unpriced = data.agents.some((a) => a.month.unpriced_calls > 0 && a.monthly_budget_cents > 0);

  return (
    <div className="page wide" style={{ maxWidth: 1400 }}>
      <div className="page-head">
        <div><h1>Company</h1><p className="muted">You are the board. Set the mission, hire agents, assign work, cap spend. One task runs at a time.</p></div>
        <div className="spacer" />
        <span className="badge">${(spend / 100).toFixed(2)} this month</span>
        <button className="btn primary" onClick={() => setEdit({ ...blank })}><UserPlus size={15} /> Hire</button>
      </div>

      <div className="card col" style={{ marginBottom: 16 }}>
        <h3>Mission</h3>
        <div className="row">
          <textarea className="input grow" rows={2} value={mission} onChange={(e) => setMission(e.target.value)} placeholder="What is this company trying to achieve?" />
          <button className="btn" disabled={mission === data.mission} onClick={async () => { await api("/company/mission", { method: "PUT", body: { mission } }); toast.ok("Mission saved"); load(); }}>Save</button>
        </div>
      </div>
      {unpriced && <div className="alert" style={{ marginBottom: 16 }}>Some agents use a model with no price set, so their money budget cannot be enforced. Set prices in Settings → Usage (OpenRouter models are priced automatically), or give them a token limit.</div>}

      <div className="card" style={{ marginBottom: 16, overflowX: "auto" }}>
        <h3 style={{ marginBottom: 12 }}>Org chart</h3>
        {data.agents.length === 0 ? <Empty icon={<Building2 size={24} />} title="No one hired yet" hint="Hire a CEO, then people who report to them." /> : (
          <div className="row" style={{ alignItems: "flex-start", justifyContent: "center", gap: 24, minWidth: "fit-content" }}>
            {roots.map((r) => <OrgNode key={r.id} agent={r} all={data.agents} onEdit={(a) => setEdit({ ...a })} />)}
          </div>
        )}
      </div>

      <div className="card col" style={{ marginBottom: 16 }}>
        <h3>New task</h3>
        <div className="row wrap">
          <input className="input grow" style={{ minWidth: 220 }} placeholder="Task title" value={task.title} onChange={(e) => setTask({ ...task, title: e.target.value })} />
          <select className="input" style={{ width: 200 }} value={task.agent_id} onChange={(e) => setTask({ ...task, agent_id: Number(e.target.value) })}>
            <option value={0}>Unassigned</option>
            {data.agents.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.title || "agent"})</option>)}
          </select>
          <button className="btn" disabled={!task.title.trim()} onClick={() => addTask(false)}><Plus size={14} /> Add</button>
          <button className="btn primary" disabled={!task.title.trim() || !task.agent_id} onClick={() => addTask(true)}><Play size={14} /> Add and start</button>
        </div>
        <textarea className="input" rows={2} placeholder="Details (optional)" value={task.description} onChange={(e) => setTask({ ...task, description: e.target.value })} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(220px, 1fr))", gap: 12, overflowX: "auto" }}>
        {COLUMNS.map(([label, statuses]) => {
          const list = data.tasks.filter((t) => statuses.includes(t.status));
          return (
            <div key={label} className="col" style={{ minWidth: 220 }}>
              <div className="row"><h3 className="grow">{label}</h3><span className="faint small">{list.length}</span></div>
              {list.map((t) => (
                <div key={t.id} className="card col" style={{ padding: 10, gap: 6 }}>
                  <div className="row" style={{ alignItems: "flex-start" }}>
                    <strong className="grow" style={{ cursor: "pointer" }} onClick={() => setOpen(open === t.id ? null : t.id)}>{t.title}</strong>
                    <button className="btn ghost sm icon" onClick={async () => { await api(`/company/tasks/${t.id}`, { method: "DELETE" }); load(); }}><X size={12} /></button>
                  </div>
                  <div className="faint small">{t.agent_name ?? "unassigned"} · {t.created_by === "board" ? "from you" : "delegated"} · {relTime(t.updated_at)}</div>
                  {open === t.id && (t.result ? <Markdown text={t.result} /> : <div className="muted small">{t.description || "No details."}</div>)}
                  {open === t.id && t.conversation_id && <Link className="small" to={`/assistant/${t.conversation_id}`}>Open work log</Link>}
                  {["todo", "failed", "blocked", "done"].includes(t.status) && t.agent_id && (
                    <button className="btn sm" onClick={() => patchTask(t, { status: "queued" })}><Play size={12} /> {t.status === "todo" ? "Start" : "Run again"}</button>
                  )}
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {edit && (
        <div className="modal-back" onClick={() => setEdit(null)}>
          <form className="modal col gap-lg" onClick={(e) => e.stopPropagation()} onSubmit={saveAgent}>
            <div className="row"><h2 className="grow">{edit.id ? `Edit ${edit.name}` : "Hire an agent"}</h2><button type="button" className="btn ghost sm icon" onClick={() => setEdit(null)}><X size={15} /></button></div>
            <div className="row">
              <label className="field grow">Name<input className="input" required value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
              <label className="field grow">Role<input className="input" placeholder="CEO, CTO, Engineer…" value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></label>
            </div>
            <label className="field">Reports to
              <select className="input" value={edit.reports_to ?? 0} onChange={(e) => setEdit({ ...edit, reports_to: Number(e.target.value) || null })}>
                <option value={0}>The board (you)</option>
                {data.agents.filter((a) => a.id !== edit.id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </label>
            <label className="field">Model<ModelSelect value={edit.model} onChange={(v) => setEdit({ ...edit, model: v })} models={models} /></label>
            <label className="field">Instructions<textarea className="input" rows={4} value={edit.instructions} onChange={(e) => setEdit({ ...edit, instructions: e.target.value })} placeholder="Responsibilities, standards, what to escalate to the board" /></label>
            <div className="row">
              <label className="field grow">Monthly budget (USD)<input className="input" type="number" min={0} step={0.5} value={edit.monthly_budget_cents / 100} onChange={(e) => setEdit({ ...edit, monthly_budget_cents: Math.round(Number(e.target.value) * 100) })} /></label>
              <label className="field grow">Monthly token limit (0 = none)<input className="input" type="number" min={0} step={10000} value={edit.monthly_token_limit} onChange={(e) => setEdit({ ...edit, monthly_token_limit: Number(e.target.value) })} /></label>
            </div>
            <div className="muted small">The agent is paused automatically when it reaches either limit. Checks run before each model call, so a limit can be passed by at most one call.</div>
            <div className="row">
              {edit.id && <button type="button" className="btn ghost danger" onClick={async () => { if (confirm(`Remove ${edit.name}? Their reports move up to the board.`)) { await api(`/company/agents/${edit.id}`, { method: "DELETE" }); setEdit(null); load(); } }}><Trash2 size={14} /></button>}
              {edit.id && (() => { const a = data.agents.find((x) => x.id === edit.id); return a ? (a.status === "paused"
                ? <button type="button" className="btn" onClick={() => setStatus(a, "active")}><Play size={14} /> Resume</button>
                : <button type="button" className="btn" onClick={() => setStatus(a, "paused")}><Pause size={14} /> Pause</button>) : null; })()}
              <div className="spacer" />
              <button className="btn primary">{edit.id ? "Save" : "Hire"}</button>
            </div>
            {edit.id && data.agents.find((x) => x.id === edit.id)?.paused_reason && <div className="alert err">{data.agents.find((x) => x.id === edit.id)?.paused_reason}</div>}
          </form>
        </div>
      )}
      {toast.node}
    </div>
  );
}
