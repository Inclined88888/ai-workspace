import { Check, ChevronDown, ChevronRight, Plug, RefreshCw, Trash2, Wrench, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { Empty, useToast } from "../util";

// feat/mcp-client: the user's own MCP servers. Tools they add show up in the assistant as mcp__<server>__<tool>;
// everything a server answers is treated as untrusted third-party content.

type Spec = {
  command: string; args: string[]; env: Record<string, string>; url: string; headers: Record<string, string>;
  transport: string; enabled: boolean; secret_env?: string[]; secret_headers?: string[];
};
type Server = {
  id: number; name: string; transport: string; url: string; command: string; enabled: boolean; spec: Spec;
  secrets: { env: string[]; headers: string[] }; needs_secrets: { env: string[]; headers: string[] };
  last_error: string; created_at: string;
};
type CatalogueEntry = {
  name: string; description: string; transport: string; command: string; args: string[];
  env: Record<string, string>; secret_env?: string[]; placeholders?: { args?: Record<string, string>; env?: Record<string, string> };
};
type ToolsInfo = { name: string; status: string; last_error: string; server_info: Record<string, string>; tools: { name: string; qualified: string; description: string }[] };

export default function McpServers() {
  const [servers, setServers] = useState<Server[] | null>(null);
  const [tools, setTools] = useState<ToolsInfo[]>([]);
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const [adding, setAdding] = useState<CatalogueEntry | "custom" | null>(null);
  const [openTools, setOpenTools] = useState<string | null>(null);
  const toast = useToast();

  const refresh = () => {
    api<{ servers: Server[] }>("/mcp/servers").then((r) => setServers(r.servers)).catch((e) => toast.err(e));
    api<{ servers: ToolsInfo[] }>("/mcp/tools").then((r) => setTools(r.servers)).catch(() => setTools([]));
  };
  useEffect(() => {
    refresh();
    api<{ catalogue: CatalogueEntry[] }>("/mcp/catalogue").then((r) => setCatalogue(r.catalogue)).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (s: Server) => {
    await api(`/mcp/servers/${s.id}/toggle`, { body: { enabled: !s.enabled } });
    refresh();
  };
  const remove = async (s: Server) => {
    if (!confirm(`Remove the MCP server "${s.name}"? Its tools leave the assistant right away.`)) return;
    await api(`/mcp/servers/${s.id}`, { method: "DELETE" });
    toast.ok(`Removed ${s.name}.`);
    refresh();
  };
  const test = async (s: Server) => {
    try {
      const r = await api<{ status: string; error?: string; tools: { name: string }[] }>(`/mcp/servers/${s.id}/test`, { body: {} });
      if (r.status === "connected") toast.ok(`${s.name}: connected, ${r.tools.length} tool${r.tools.length === 1 ? "" : "s"}.`);
      else toast.err(`${s.name}: ${r.error}`);
    } catch (e) {
      toast.err(e);
    }
    refresh();
  };

  const catalogued = new Set(servers?.map((s) => s.name) ?? []);
  return (
    <div className="page" style={{ maxWidth: 900 }}>
      <div className="page-head">
        <div className="grow">
          <p className="kicker">Agents</p>
          <h1>MCP servers</h1>
          <p className="muted">
            Connect the assistant to tools that speak MCP. Their tools appear as <code>mcp__server__tool</code>;
            everything they answer is treated as untrusted content, and actions that leave the app after it still
            wait for your approval.
          </p>
        </div>
        <button className="btn" onClick={() => setAdding("custom")}><Plug size={14} /> Add a server</button>
      </div>

      {adding && (
        <AddForm
          entry={adding === "custom" ? null : adding}
          onDone={() => { setAdding(null); refresh(); }}
          onCancel={() => setAdding(null)}
        />
      )}

      {servers === null ? <p className="muted">Loading…</p> : servers.length === 0 && !adding ? (
        <Empty icon={<Plug size={22} />} title="No MCP servers yet"
               hint="Pick one from the catalogue below, or add any server with its command or URL." />
      ) : (
        <div className="col" style={{ gap: 10 }}>
          {servers?.map((s) => {
            const info = tools.find((t) => t.name === s.name);
            return (
              <div key={s.id} className="card">
                <div className="row" style={{ gap: 10, alignItems: "center" }}>
                  <div className="grow">
                    <div className="row" style={{ gap: 8, alignItems: "center" }}>
                      <strong>{s.name}</strong>
                      <span className="badge">{s.transport}</span>
                      {!s.enabled && <span className="badge">disabled</span>}
                      {info?.status === "connected" && s.enabled && <span className="badge ok">connected</span>}
                      {info?.status === "error" && s.enabled && <span className="badge warn">error</span>}
                    </div>
                    <p className="muted sm" style={{ margin: "4px 0 0" }}>
                      {s.transport === "stdio" ? `${s.command} ${s.spec.args.join(" ")}`.trim() : s.url}
                      {s.last_error && s.enabled && <span className="warn-text"> · {s.last_error}</span>}
                    </p>
                  </div>
                  {info && info.tools.length > 0 && (
                    <button className="btn ghost sm" onClick={() => setOpenTools(openTools === s.name ? null : s.name)}>
                      <Wrench size={13} /> {info.tools.length} tool{info.tools.length === 1 ? "" : "s"}
                      {openTools === s.name ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    </button>
                  )}
                  <button className="btn ghost sm" onClick={() => test(s)} disabled={!s.enabled}><RefreshCw size={13} /> Test</button>
                  <button className="btn ghost sm" onClick={() => toggle(s)}>{s.enabled ? <><X size={13} /> Disable</> : <><Check size={13} /> Enable</>}</button>
                  <button className="btn ghost sm" onClick={() => remove(s)} aria-label={`Remove ${s.name}`}><Trash2 size={13} /></button>
                </div>
                {openTools === s.name && info && (
                  <ul className="muted sm" style={{ margin: "10px 0 0", paddingLeft: 18 }}>
                    {info.tools.map((t) => (
                      <li key={t.qualified}><code>{t.qualified}</code>{t.description ? ` — ${t.description}` : ""}</li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      <h2 style={{ marginTop: 28 }}>Catalogue</h2>
      <p className="muted">The official reference servers. They run on this machine with npx or uvx; add one and it is available in chat.</p>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
        {catalogue.map((c) => (
          <div key={c.name} className="card">
            <strong>{c.name}</strong>
            <p className="muted sm">{c.description}</p>
            <p className="muted sm"><code>{c.command} {c.args.join(" ")}</code></p>
            {catalogued.has(c.name) ? (
              <span className="badge ok"><Check size={12} /> added</span>
            ) : (
              <button className="btn sm" onClick={() => setAdding(c)}>Add</button>
            )}
          </div>
        ))}
      </div>
      {toast.node}
    </div>
  );
}

function AddForm({ entry, onDone, onCancel }: { entry: CatalogueEntry | null; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(entry?.name ?? "");
  const [transport, setTransport] = useState(entry?.transport ?? "stdio");
  const [command, setCommand] = useState(entry?.command ?? "");
  const [argsText, setArgsText] = useState(entry?.args.join(" ") ?? "");
  const [url, setUrl] = useState("");
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const secretSlots = entry?.secret_env ?? [];
  const argPlaceholders = entry?.placeholders?.args ?? {};
  // Placeholders the user has not replaced yet (still "<...>" in the args line) get their own input below.
  const openPlaceholders = Object.keys(argPlaceholders).filter((ph) => argsText.includes(ph));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      let line = argsText;
      for (const ph of openPlaceholders) {
        const v = (secrets[`arg:${ph}`] ?? "").trim();
        if (v) line = line.split(ph).join(v);
      }
      if (/<[^>]+>/.test(line)) {
        toast.err("Replace every <placeholder> in the arguments first.");
        return;
      }
      const env: Record<string, string> = {};
      for (const k of secretSlots) env[k] = "";
      await api("/mcp/servers", {
        body: {
          name: name.trim(), transport, command, args: line.split(" ").filter(Boolean), env, url,
          secret_env: secretSlots,
          secrets: { env: Object.fromEntries(secretSlots.map((k) => [k, secrets[k] ?? ""])) },
        },
      });
      toast.ok(`Added ${name.trim()}.`);
      onDone();
    } catch (err) {
      toast.err(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit} style={{ marginBottom: 16 }}>
      <h3 style={{ marginTop: 0 }}>{entry ? `Add ${entry.name}` : "Add an MCP server"}</h3>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <label className="field">Name
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="my-server" required
                 pattern="[a-z0-9][a-z0-9_-]{0,30}" title="lowercase letters, digits, - or _" />
        </label>
        <label className="field">Transport
          <select className="input" value={transport} onChange={(e) => setTransport(e.target.value)}>
            <option value="stdio">stdio (runs a command here)</option>
            <option value="streamable_http">Streamable HTTP</option>
            <option value="sse">SSE (legacy)</option>
          </select>
        </label>
      </div>
      {transport === "stdio" ? (
        <>
          <label className="field">Command
            <input className="input" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" required />
          </label>
          <label className="field">Arguments
            <input className="input" value={argsText} onChange={(e) => setArgsText(e.target.value)}
                   placeholder={entry?.args.join(" ") ?? "-y @modelcontextprotocol/server-memory"} />
          </label>
          {openPlaceholders.map((ph) => (
            <label className="field" key={ph}>{argPlaceholders[ph]}
              <input className="input" value={secrets[`arg:${ph}`] ?? ""}
                     onChange={(e) => setSecrets((s) => ({ ...s, [`arg:${ph}`]: e.target.value }))} required />
            </label>
          ))}
        </>
      ) : (
        <label className="field">URL
          <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/mcp" required type="url" />
        </label>
      )}
      {secretSlots.map((k) => (
        <label className="field" key={k}>{entry?.placeholders?.env?.[k] ?? k}
          <input className="input" type="password" value={secrets[k] ?? ""} onChange={(e) => setSecrets((s) => ({ ...s, [k]: e.target.value }))}
                 placeholder="Stored encrypted, never shown again" required />
        </label>
      ))}
      <div className="row" style={{ gap: 8, marginTop: 10 }}>
        <button className="btn" disabled={busy}>{busy ? "Adding…" : "Add server"}</button>
        <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
      {toast.node}
    </form>
  );
}
