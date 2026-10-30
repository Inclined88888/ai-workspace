import { ArrowRight, Check, ExternalLink, Github, KeyRound, Lock, Puzzle, RefreshCw, Search, ShieldAlert, ShieldCheck, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, stream } from "../api";
import { Tracer } from "../Tracer";
import { Empty, relTime, useToast } from "../util";
import { SkillsTab } from "./Assistant";

// ---------------------------------------------------------------------------------------------------- types
type Finding = { rule: string; severity: "critical" | "high" | "medium" | "low"; category: string; message: string; file: string; line: number; snippet: string; tool?: string };
type Perms = { network: string[]; files: string[]; env: string[]; shell: boolean; tools: string[]; scripts: string[]; undeclared: string[] };
type Violation = { kind: string; what: string; target: string };
type Run = { script: string; status: string; exit: number | null; isolation?: string; stdout: string; stderr: string; violations: Violation[] };
type Trial = { status: string; runs: Run[]; violations: Violation[]; layers: string[]; note?: string };
type Risk = { level: "low" | "medium" | "high"; reasons: string[]; counts: Record<string, number> };
type Diff = { added: string[]; removed: string[]; changed: string[]; patches: string[]; permissions: string[] };
type Report = {
  static: Finding[]; injection: Finding[]; permissions: Perms; permissions_plain: string[]; trial: Trial; risk: Risk;
  scanners: string[]; files: string[]; manifest: { kind: string; mcp?: { command: string; args: string[]; remote_code?: boolean } | null };
  source: { owner: string; repo: string; path: string; ref: string; sha: string }; diff?: Diff;
};
type HubSkill = {
  id: number; name: string; description: string; kind: "skill" | "mcp"; source_url: string; owner: string; repo: string; path: string;
  ref: string; commit_sha: string; status: string; risk: Risk["level"]; report: Report; granted: Partial<Perms>; update_of: number | null;
  updated_at: string; installed_at: string | null; confirm_phrase?: string;
};
type StageId = "fetch" | "static" | "injection" | "permissions" | "sandbox" | "risk";
type StageState = { status: "waiting" | "running" | "done" | "choose"; detail: string };
type Choice = { name: string; path: string; url: string };
type SearchHit = { name: string; full_name: string; path: string; description: string; stars: number | null; url: string; kind: string };
type AuditRow = { id: number; skill_name: string; event: string; detail: string; created_at: string };
type PermRequest = { id: number; skill_name: string; kind: string; target: string; what: string; created_at: string };
type HubSettings = { has_token: boolean; scanners: string[]; isolation: string[] };

const STAGES: { id: StageId; label: string }[] = [
  { id: "fetch", label: "Fetch, pinned to one commit" },
  { id: "static", label: "Code scan" },
  { id: "injection", label: "Prompt-injection scan" },
  { id: "permissions", label: "What it can touch" },
  { id: "sandbox", label: "Sealed trial run" },
  { id: "risk", label: "Risk verdict" },
];
const blankStages = () => Object.fromEntries(STAGES.map((s) => [s.id, { status: "waiting", detail: "" }])) as Record<StageId, StageState>;
const RISK_COPY = { low: "Low risk", medium: "Medium risk", high: "High risk · blocked" } as const;

// ---------------------------------------------------------------------------------------------------- page
export default function Skills() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "installed" | "add" | "activity") || "installed";
  const go = (t: string) => setParams(t === "installed" ? {} : { tab: t }, { replace: true });
  const [pending, setPending] = useState(0);
  useEffect(() => { api<PermRequest[]>("/hub/requests").then((r) => setPending(r.length)).catch(() => {}); }, [tab]);

  return (
    <div className="page" style={{ maxWidth: 1040 }}>
      <div className="page-head">
        <div className="grow">
          <p className="kicker">Agents</p>
          <h1>Skills</h1>
          <p className="muted">Teach the assistant and Rooks Code new tricks. Anything from GitHub is checked before it can run.</p>
        </div>
        <div className="tabs" role="tablist">
          {([["installed", "Installed"], ["add", "Add from GitHub"], ["activity", pending ? `Activity · ${pending}` : "Activity"]] as const).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => go(id)}>{label}</button>
          ))}
        </div>
      </div>
      {tab === "installed" && <Installed onAdd={() => go("add")} />}
      {tab === "add" && <AddFromGitHub onInstalled={() => go("installed")} />}
      {tab === "activity" && <ActivityTab onChange={setPending} />}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- installed
function Installed({ onAdd }: { onAdd: () => void }) {
  const [items, setItems] = useState<HubSkill[] | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [updating, setUpdating] = useState<{ id: number; stages: Record<StageId, StageState>; result?: HubSkill; upToDate?: boolean; error?: string } | null>(null);
  const toast = useToast();
  const load = () => api<HubSkill[]>("/hub/skills").then((r) => setItems(r.filter((s) => s.status === "installed" || s.status === "disabled"))).catch(toast.err);
  useEffect(() => { load(); }, []);

  const toggle = async (s: HubSkill) => {
    try { await api(`/hub/skills/${s.id}/${s.status === "installed" ? "disable" : "enable"}`, { method: "POST" }); load(); } catch (e) { toast.err(e); }
  };
  const remove = async (s: HubSkill) => {
    if (!confirm(`Uninstall ${s.name}? Its files are deleted; the audit log keeps a record.`)) return;
    try { await api(`/hub/skills/${s.id}`, { method: "DELETE" }); toast.ok(`${s.name} uninstalled`); load(); } catch (e) { toast.err(e); }
  };
  const update = async (s: HubSkill) => {
    let st = blankStages();
    setUpdating({ id: s.id, stages: st });
    await stream(`/hub/skills/${s.id}/update`, {}, (e) => {
      if (e.error) setUpdating((u) => u && { ...u, error: String(e.error) });
      else if (e.done) setUpdating((u) => u && { ...u, upToDate: !!e.up_to_date, result: e.up_to_date ? undefined : (e.skill as HubSkill) });
      else if (e.stage) { st = { ...st, [e.stage as StageId]: { status: e.status as StageState["status"], detail: String(e.detail ?? "") } }; setUpdating((u) => u && { ...u, stages: st }); }
    });
  };

  if (!items) return <div className="card"><div className="skeleton" style={{ height: 14, width: "40%" }} /></div>;
  return (
    <div className="col gap-lg">
      <section className="col">
        <div className="row"><h3 className="grow" style={{ margin: 0 }}>From GitHub</h3><button className="btn sm" onClick={onAdd}><Github size={13} /> Add a skill</button></div>
        {items.length === 0 ? (
          <Empty icon={<Puzzle size={24} />} title="No GitHub skills yet"
            hint="Paste a link to a GitHub folder with a SKILL.md, or search. Every skill is scanned and trial-run in a sandbox before you decide.">
            <button className="btn primary" onClick={onAdd}><Github size={14} /> Add from GitHub</button>
          </Empty>
        ) : (
          <div className="card flush">
            {items.map((s) => (
              <div key={s.id} className="sk-row">
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <div className="grow col" style={{ gap: 4 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <strong className="mono">{s.name}</strong>
                      <RiskBadge level={s.risk} />
                      {s.kind === "mcp" && <span className="badge">MCP server</span>}
                      {s.status === "disabled" && <span className="badge">Off</span>}
                    </div>
                    <div className="muted small">{s.description || "No description"}</div>
                    <div className="faint small mono">{s.owner}/{s.repo}{s.path ? `/${s.path}` : ""} @ {s.commit_sha.slice(0, 10)} · installed {relTime(s.installed_at || s.updated_at)}</div>
                    <div className="small">{grantSummary(s.granted)}</div>
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    <button className="btn sm" onClick={() => update(s)} disabled={updating?.id === s.id && !updating.result && !updating.upToDate && !updating.error}><RefreshCw size={13} /> Check for update</button>
                    <button className="btn sm" onClick={() => toggle(s)}>{s.status === "installed" ? "Turn off" : "Turn on"}</button>
                    <button className="btn ghost sm danger" aria-label={`Uninstall ${s.name}`} onClick={() => remove(s)}><Trash2 size={13} /></button>
                  </div>
                </div>
                <button className="link-btn small" onClick={() => setOpen(open === s.id ? null : s.id)}>{open === s.id ? "Hide report" : "Show security report"}</button>
                {open === s.id && <ReportView r={s.report} />}
                {updating?.id === s.id && (
                  <div className="col" style={{ marginTop: 12 }}>
                    {updating.upToDate && <div className="alert ok">Already up to date with {s.ref}.</div>}
                    {updating.error && <div className="alert err">{updating.error}</div>}
                    {!updating.upToDate && !updating.error && <Stepper stages={updating.stages} />}
                    {updating.result && <Review skill={updating.result} onDone={() => { setUpdating(null); load(); }} />}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="col">
        <h3 style={{ margin: 0 }}>Your own skills</h3>
        <SkillsTab />
      </section>
      {toast.node}
    </div>
  );
}

function grantSummary(g: Partial<Perms>): string {
  const bits = [
    g.network?.length ? `internet: ${g.network.join(", ")}` : "",
    g.files?.length ? `files: ${g.files.join(", ")}` : "",
    g.env?.length ? `settings: ${g.env.join(", ")}` : "",
    g.shell ? "can run programs" : "",
  ].filter(Boolean);
  return bits.length ? `Allowed ${bits.join(" · ")}` : "No extra access granted. Anything else asks you first.";
}

// ---------------------------------------------------------------------------------------------------- add
function AddFromGitHub({ onInstalled }: { onInstalled: () => void }) {
  const [url, setUrl] = useState("");
  const [stages, setStages] = useState<Record<StageId, StageState> | null>(null);
  const [choices, setChoices] = useState<Choice[] | null>(null);
  const [result, setResult] = useState<HubSkill | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [settings, setSettings] = useState<HubSettings | null>(null);
  const toast = useToast();
  useEffect(() => { api<HubSettings>("/hub/settings").then(setSettings).catch(() => {}); }, []);

  const check = async (target: string) => {
    const u = target.trim();
    if (!u || running) return;
    setUrl(u); setChoices(null); setResult(null); setError(""); setRunning(true);
    let st = blankStages();
    setStages(st);
    try {
      await stream("/hub/check", { url: u }, (e) => {
        if (e.error) setError(String(e.error));
        else if (e.done) setResult(e.skill as HubSkill);
        else if (e.stage) {
          st = { ...st, [e.stage as StageId]: { status: e.status as StageState["status"], detail: String(e.detail ?? "") } };
          setStages(st);
          if (e.status === "choose") setChoices(e.choices as Choice[]);
        }
      });
    } catch (e) { setError(String((e as Error).message || e)); }
    setRunning(false);
  };
  const submit = (e: FormEvent) => { e.preventDefault(); check(url); };
  const search = async (e: FormEvent) => {
    e.preventDefault();
    setSearching(true);
    try { setHits(await api<SearchHit[]>(`/hub/search?q=${encodeURIComponent(q)}`)); } catch (err) { toast.err(err); }
    setSearching(false);
  };

  return (
    <div className="col gap-lg">
      <form className="card col" onSubmit={submit}>
        <label className="label" htmlFor="sk-url">Paste a GitHub link</label>
        <div className="row">
          <input id="sk-url" className="input mono grow" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false}
            placeholder="https://github.com/owner/repo/tree/main/skills/my-skill" />
          <button className="btn primary" disabled={!url.trim() || running}>{running ? "Checking…" : <>Check it <ArrowRight size={14} /></>}</button>
        </div>
        <p className="faint small" style={{ margin: 0 }}>A repository or a folder. Works with SKILL.md skills and MCP servers. Nothing is installed until you say so.</p>
      </form>

      {stages && (
        <section className="col">
          <Stepper stages={stages} />
          {error && <div className="alert err" role="alert">{error}</div>}
          {choices && (
            <div className="card flush">
              <div className="sk-row"><strong>This repository holds {choices.length} skills. Which one?</strong></div>
              {choices.map((c) => (
                <div key={c.path} className="sk-row row">
                  <span className="mono grow">{c.name}</span><span className="faint small mono truncate" style={{ maxWidth: 320 }}>{c.path}</span>
                  <button className="btn sm" onClick={() => check(c.url)}>Check this one</button>
                </div>
              ))}
            </div>
          )}
          {result && <Review skill={result} onDone={(installed) => { setStages(null); setResult(null); setUrl(""); if (installed) onInstalled(); }} />}
        </section>
      )}

      {!stages && (
        <section className="col">
          <form className="row" onSubmit={search}>
            <div className="input-icon grow"><Search size={14} /><input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search GitHub, e.g. pdf, spreadsheet, notion" aria-label="Search GitHub for skills" /></div>
            <button className="btn" disabled={searching}>{searching ? "Searching…" : "Search"}</button>
          </form>
          {hits === null ? (
            <p className="faint small" style={{ margin: 0 }}>
              {settings?.has_token ? "Searching SKILL.md files and repositories tagged agent-skills, claude-skills or mcp-server." : "Searches repositories tagged agent-skills, claude-skills or mcp-server. Add a GitHub token below to search inside repositories too."}
            </p>
          ) : hits.length === 0 ? (
            <Empty icon={<Search size={22} />} title="Nothing found" hint="Try a broader word, or paste the link if you already know where the skill lives." />
          ) : (
            <div className="card flush">
              {hits.map((h) => (
                <div key={h.url} className="sk-row row" style={{ alignItems: "flex-start" }}>
                  <div className="grow col" style={{ gap: 2 }}>
                    <div className="row" style={{ gap: 8 }}><strong className="mono">{h.name}</strong>{h.kind === "mcp" && <span className="badge">MCP</span>}{h.stars != null && <span className="faint small">★ {h.stars}</span>}</div>
                    <span className="faint small mono">{h.full_name}{h.path ? `/${h.path}` : ""}</span>
                    {h.description && <span className="muted small">{h.description}</span>}
                  </div>
                  <a className="btn ghost sm" href={h.url} target="_blank" rel="noreferrer" aria-label="Open on GitHub"><ExternalLink size={13} /></a>
                  <button className="btn sm" onClick={() => check(h.url)}>Check</button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {settings && <HubSettingsCard settings={settings} onSaved={setSettings} />}
      {toast.node}
    </div>
  );
}

function HubSettingsCard({ settings, onSaved }: { settings: HubSettings; onSaved: (s: HubSettings) => void }) {
  const [open, setOpen] = useState(false);
  const [tok, setTok] = useState("");
  const toast = useToast();
  const save = async (value: string) => {
    try { onSaved(await api<HubSettings>("/hub/settings", { method: "PUT", body: { token: value } })); setTok(""); toast.ok(value ? "Token saved, encrypted" : "Token removed"); } catch (e) { toast.err(e); }
  };
  return (
    <details className="card sk-settings" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary><span className="grow">How checking works</span><span className="faint small">{settings.has_token ? "GitHub token set" : "No GitHub token"}</span></summary>
      <div className="col" style={{ marginTop: 12 }}>
        <p className="small muted" style={{ margin: 0 }}>
          Scanners: built-in rules{settings.scanners.length ? ` + ${settings.scanners.join(", ")}` : " (install semgrep, bandit, gitleaks or osv-scanner to add theirs)"}.
          Sandbox: {settings.isolation.join(", ")}.
        </p>
        <label className="label" htmlFor="sk-token" style={{ marginBottom: 0 }}>GitHub token (optional)</label>
        <div className="row">
          <input id="sk-token" className="input mono grow" type="password" autoComplete="off" value={tok} onChange={(e) => setTok(e.target.value)} placeholder={settings.has_token ? "•••••••• saved" : "Only needs public read access"} />
          <button className="btn" disabled={!tok.trim()} onClick={() => save(tok)}><KeyRound size={13} /> Save</button>
          {settings.has_token && <button className="btn ghost" onClick={() => save("")}>Remove</button>}
        </div>
        <p className="faint small" style={{ margin: 0 }}>Raises GitHub's rate limit and lets search look inside repositories. Stored encrypted on this machine.</p>
      </div>
      {toast.node}
    </details>
  );
}

// ---------------------------------------------------------------------------------------------------- stepper
function Stepper({ stages }: { stages: Record<StageId, StageState> }) {
  return (
    <ol className="sk-steps" aria-label="Security check">
      {STAGES.map((s, i) => {
        const st = stages[s.id];
        return (
          <li key={s.id} className={`sk-step has-tracer is-${st.status}`} aria-current={st.status === "running" ? "step" : undefined}>
            <Tracer active={st.status === "running"} />
            <span className="sk-num mono">{st.status === "done" ? <Check size={12} strokeWidth={3} /> : String(i + 1).padStart(2, "0")}</span>
            <span className="col" style={{ gap: 2, minWidth: 0 }}>
              <span className="sk-step-label">{s.label}</span>
              <span className="sk-step-detail small">{st.detail || (st.status === "waiting" ? "Waiting" : "")}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------------------------------- review + approve
function Review({ skill, onDone }: { skill: HubSkill; onDone: (installed: boolean) => void }) {
  const r = skill.report;
  const p = r.permissions;
  const phrase = skill.confirm_phrase || `install ${skill.name} anyway`;
  const [grant, setGrant] = useState<Record<string, boolean>>(() => {
    const g: Record<string, boolean> = {};
    const on = skill.risk !== "high";
    p.network.forEach((d) => (g[`network:${d}`] = on));
    p.files.forEach((f) => (g[`files:${f}`] = on));
    p.env.forEach((v) => (g[`env:${v}`] = on));
    p.tools.forEach((t) => (g[`tools:${t}`] = on));
    if (p.shell) g["shell"] = on;
    return g;
  });
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const high = skill.risk === "high";
  const unlocked = !high || typed.trim().toLowerCase() === phrase;

  const install = async () => {
    const pick = (k: string) => Object.entries(grant).filter(([key, v]) => v && key.startsWith(k + ":")).map(([key]) => key.slice(k.length + 1));
    setBusy(true); setErr("");
    try {
      await api(`/hub/skills/${skill.id}/install`, { body: { grants: { network: pick("network"), files: pick("files"), env: pick("env"), tools: pick("tools"), shell: !!grant.shell }, confirm: typed } });
      onDone(true);
    } catch (e) { setErr(String((e as Error).message || e)); }
    setBusy(false);
  };
  const reject = async () => {
    try { await api(`/hub/skills/${skill.id}/reject`, { method: "POST" }); } catch { /* already gone */ }
    onDone(false);
  };
  const items = useMemo(() => [
    ...p.network.map((d) => ({ key: `network:${d}`, text: `Connect to ${d}` })),
    ...p.files.map((f) => ({ key: `files:${f}`, text: `Read or write ${f}` })),
    ...p.env.map((v) => ({ key: `env:${v}`, text: `Read the setting ${v}` })),
    ...p.tools.map((t) => ({ key: `tools:${t}`, text: `Use the ${t} tool` })),
    ...(p.shell ? [{ key: "shell", text: "Run programs and shell commands" }] : []),
  ], [p]);

  return (
    <div className={`card sk-review risk-${skill.risk}`}>
      <div className="sk-verdict">
        {high ? <ShieldAlert size={22} /> : <ShieldCheck size={22} />}
        <div className="grow">
          <p className="kicker" style={{ margin: 0 }}>Security report · {skill.kind === "mcp" ? "MCP server" : "Skill"}</p>
          <h2 style={{ margin: "2px 0 0" }}><span className="mono" style={{ fontSize: "0.8em" }}>{skill.name}</span> · {RISK_COPY[skill.risk]}</h2>
        </div>
        <a className="btn ghost sm" href={`https://github.com/${r.source.owner}/${r.source.repo}/tree/${r.source.sha}/${r.source.path}`} target="_blank" rel="noreferrer">
          <Github size={13} /> {r.source.sha.slice(0, 10)}
        </a>
      </div>
      {skill.description && <p className="muted" style={{ margin: "0 0 14px" }}>{skill.description}</p>}
      <ul className="sk-reasons">{r.risk.reasons.map((x) => <li key={x}>{x}</li>)}</ul>

      {r.diff && <DiffView d={r.diff} />}
      <ReportView r={r} />

      <div className="sk-approve">
        {items.length > 0 ? (
          <fieldset className="col" style={{ border: "none", padding: 0, margin: 0, gap: 8 }}>
            <legend className="label">Allow it to</legend>
            {items.map((it) => (
              <label key={it.key} className="check">
                <input type="checkbox" checked={!!grant[it.key]} onChange={(e) => setGrant({ ...grant, [it.key]: e.target.checked })} /> {it.text}
              </label>
            ))}
            <p className="faint small" style={{ margin: 0 }}>Anything you leave unticked, or anything it tries that isn't listed, is blocked and asks you first.</p>
          </fieldset>
        ) : <p className="small" style={{ margin: 0 }}><Lock size={12} /> Needs no extra access: no internet, no files outside its folder, no programs.</p>}

        {high && (
          <div className="sk-block" role="alert">
            <strong>Blocked by default.</strong> This skill did something dangerous in the checks above. If you wrote it or fully trust it, type
            <code className="mono"> {phrase} </code> to override.
            <input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={phrase} aria-label="Type the override phrase" spellCheck={false} />
          </div>
        )}
        {err && <div className="alert err">{err}</div>}
        <div className="row">
          <button className={`btn ${high ? "danger-solid" : "primary"}`} disabled={busy || !unlocked} onClick={install}>
            {busy ? "Installing…" : high ? "Install anyway" : skill.update_of ? "Approve update" : "Approve and install"}
          </button>
          <button className="btn ghost" onClick={reject}><X size={14} /> {high ? "Discard" : "Don't install"}</button>
        </div>
      </div>
    </div>
  );
}

function RiskBadge({ level }: { level: Risk["level"] }) {
  return <span className={`badge risk ${level}`}>{level === "high" ? "High risk" : level === "medium" ? "Medium risk" : "Low risk"}</span>;
}

function ReportView({ r }: { r: Report }) {
  const findings = [...r.injection.map((f) => ({ ...f, where: "Instructions" })), ...r.static.map((f) => ({ ...f, where: "Code" }))];
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  findings.sort((a, b) => order[a.severity] - order[b.severity]);
  return (
    <div className="sk-report">
      <div className="sk-cols">
        <div>
          <p className="label">What it can touch</p>
          <ul className="sk-plain">{r.permissions_plain.map((x) => <li key={x}>{x}</li>)}</ul>
          {r.permissions.undeclared.length > 0 && <p className="small" style={{ color: "var(--danger-text)" }}>Not declared by the author: {r.permissions.undeclared.join(", ")}</p>}
        </div>
        <div>
          <p className="label">Sealed trial run · {r.trial.status}</p>
          {r.trial.note && <p className="small muted" style={{ marginTop: 0 }}>{r.trial.note}</p>}
          {r.trial.runs.length === 0 && !r.trial.note && <p className="small muted" style={{ marginTop: 0 }}>Instructions only. Nothing to run.</p>}
          {r.trial.runs.map((run, i) => (
            <div key={i} className="sk-run">
              <div className="row small"><span className="mono grow">{run.script}</span><span className="faint">{run.status}{run.exit != null ? ` · exit ${run.exit}` : ""}</span></div>
              {run.violations.map((v, j) => <div key={j} className="sk-violation small"><X size={11} strokeWidth={3} /> Blocked {v.kind.replace("-", " ")}: <span className="mono">{v.target || v.what}</span></div>)}
              {run.stdout.trim() && <pre className="sk-pre">{run.stdout.trim().slice(0, 400)}</pre>}
            </div>
          ))}
        </div>
      </div>
      <p className="label" style={{ marginTop: 18 }}>Findings · {findings.length || "none"}</p>
      {findings.length > 0 && (
        <table className="sk-findings">
          <tbody>
            {findings.slice(0, 40).map((f, i) => (
              <tr key={i}>
                <td><span className={`sev ${f.severity}`}>{f.severity}</span></td>
                <td>
                  <div>{f.message}</div>
                  <div className="faint small mono">{f.where} · {f.file}{f.line ? `:${f.line}` : ""}{f.tool ? ` · ${f.tool}` : ""}</div>
                  {f.snippet && <code className="sk-snippet">{f.snippet}</code>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="faint small" style={{ marginBottom: 0 }}>Checked {r.files.length} files at {r.source.owner}/{r.source.repo}@{r.source.sha.slice(0, 12)} with built-in rules{r.scanners.length ? `, ${r.scanners.join(", ")}` : ""}.</p>
    </div>
  );
}

function DiffView({ d }: { d: Diff }) {
  const none = !d.added.length && !d.removed.length && !d.changed.length;
  return (
    <div className="sk-diff">
      <p className="label">What changed since your version</p>
      {none ? <p className="small muted">No file changes.</p> : (
        <p className="small" style={{ marginTop: 0 }}>
          {d.added.length > 0 && <>Added {d.added.join(", ")}. </>}
          {d.removed.length > 0 && <>Removed {d.removed.join(", ")}. </>}
          {d.changed.length > 0 && <>Changed {d.changed.join(", ")}.</>}
        </p>
      )}
      {d.permissions.map((x) => <div key={x} className="sk-violation small">{x}</div>)}
      {d.patches.map((p, i) => (
        <pre key={i} className="sk-pre sk-patch">{p.split("\n").map((ln, j) => <span key={j} className={ln.startsWith("+") && !ln.startsWith("+++") ? "add" : ln.startsWith("-") && !ln.startsWith("---") ? "del" : ""}>{ln + "\n"}</span>)}</pre>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------- activity
function ActivityTab({ onChange }: { onChange: (n: number) => void }) {
  const [reqs, setReqs] = useState<PermRequest[] | null>(null);
  const [log, setLog] = useState<AuditRow[] | null>(null);
  const toast = useToast();
  const load = () => {
    api<PermRequest[]>("/hub/requests").then((r) => { setReqs(r); onChange(r.length); }).catch(toast.err);
    api<AuditRow[]>("/hub/audit").then(setLog).catch(toast.err);
  };
  useEffect(() => { load(); }, []);
  const decide = async (id: number, allow: boolean) => {
    try { await api(`/hub/requests/${id}/${allow ? "allow" : "deny"}`, { method: "POST" }); load(); } catch (e) { toast.err(e); }
  };
  return (
    <div className="col gap-lg">
      <section className="col">
        <h3 style={{ margin: 0 }}>Waiting for you</h3>
        {reqs && reqs.length === 0 && <p className="muted small" style={{ margin: 0 }}>Nothing waiting. When a skill tries something you didn't allow, it's stopped and shows up here.</p>}
        {reqs && reqs.length > 0 && (
          <div className="card flush">
            {reqs.map((r) => (
              <div key={r.id} className="sk-row row">
                <ShieldAlert size={16} style={{ color: "var(--accent)" }} />
                <div className="grow col" style={{ gap: 2 }}>
                  <span><strong className="mono">{r.skill_name}</strong> tried to {r.kind === "network" ? "connect to" : r.kind.replace("-", " ")} <span className="mono">{r.target || r.what}</span></span>
                  <span className="faint small">{relTime(r.created_at)} · {r.what}</span>
                </div>
                <button className="btn sm" onClick={() => decide(r.id, true)}>Allow from now on</button>
                <button className="btn ghost sm" onClick={() => decide(r.id, false)}>Keep blocked</button>
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="col">
        <h3 style={{ margin: 0 }}>Audit log</h3>
        {log && log.length === 0 && <p className="muted small" style={{ margin: 0 }}>Every check, install, update, permission decision and uninstall is recorded here.</p>}
        {log && log.length > 0 && (
          <table className="sk-audit">
            <tbody>
              {log.map((a) => (
                <tr key={a.id}><td className="faint small mono">{new Date(a.created_at.endsWith("Z") || a.created_at.includes("+") ? a.created_at : a.created_at + "Z").toLocaleString()}</td><td className="mono">{a.skill_name}</td><td><span className="badge">{a.event}</span></td><td className="small muted">{a.detail}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {toast.node}
    </div>
  );
}
