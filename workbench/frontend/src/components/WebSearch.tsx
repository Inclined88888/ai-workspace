import { Check, Copy, Loader2, Search } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../util";
import { PasswordInput } from "./ui";

type Result = { title: string; url: string; snippet: string };
type Test = { ok: boolean; error?: string; results?: Result[]; providers: string[]; provider?: string };
const NAMES: Record<string, string> = { searxng: "SearXNG add-on", brave: "Brave Search", tavily: "Tavily" };
const ADDON_CMD = "cd ~/ai-workspace && ./setup.sh --with search";

/** Web search for the assistant and research: pick a source, paste a key, and see a real search work. */
export default function WebSearchPanel() {
  const [active, setActive] = useState<string[] | null>(null);
  const [pick, setPick] = useState<"tavily" | "brave" | "searxng">("tavily");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<Test | null>(null);
  const toast = useToast();

  const load = () => api<{ search_providers: string[] }>("/assistant/settings").then((r) => setActive(r.search_providers ?? [])).catch(() => setActive([]));
  useEffect(() => { load(); }, []);

  const runTest = async (provider = "") => {
    setBusy(true);
    setTest(null);
    try { const t = await api<Test>("/search/test", { body: { query: "ROOKS self-hosted AI workspace", provider } }); setTest(t); setActive(t.providers); }
    catch (e) { setTest({ ok: false, error: (e as Error).message, providers: active ?? [] }); }
    finally { setBusy(false); }
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim() || busy) return;
    try {
      await api("/assistant/settings", { method: "PUT", body: pick === "tavily" ? { tavily_api_key: key.trim() } : { brave_api_key: key.trim() } });
      setKey("");
      await runTest(pick);
    } catch (err) { toast.err(err); }
  };
  const removeKey = async (p: string) => {
    if (!confirm(`Remove the ${NAMES[p]} key?`)) return;
    try { await api("/assistant/settings", { method: "PUT", body: p === "tavily" ? { tavily_api_key: "" } : { brave_api_key: "" } }); setTest(null); load(); toast.ok("Key removed"); }
    catch (e) { toast.err(e); }
  };

  return (
    <div className="card col gap-lg" id="search" style={{ scrollMarginTop: 16 }}>
      <div className="row"><Search size={16} aria-hidden="true" /><h2 className="grow">Web search</h2>
        {active && (active.length ? <span className="badge ok"><Check size={11} /> On: {active.map((p) => NAMES[p]).join(", ")}</span> : <span className="badge warn">Off</span>)}</div>
      <p className="muted small" style={{ margin: 0 }}>Lets the assistant and Research look things up online, with sources. Without it ROOKS can still read a web page you give it.</p>

      {active && active.length > 0 && (
        <div className="row wrap">
          <button type="button" className="btn" onClick={() => runTest()} disabled={busy}>{busy ? <><Loader2 size={14} className="spin" /> Searching…</> : "Test web search"}</button>
          {active.filter((p) => p !== "searxng").map((p) => <button key={p} type="button" className="btn ghost sm" onClick={() => removeKey(p)}>Remove {NAMES[p]} key</button>)}
        </div>
      )}

      <div className="provider-grid" role="radiogroup" aria-label="Search source">
        <button type="button" role="radio" aria-checked={pick === "tavily"} className="choice compact" onClick={() => setPick("tavily")}><span className="grow"><strong>Tavily</strong> <span className="badge ok">Free, no card</span><br /><span className="muted small">1,000 searches a month. Key from tavily.com.</span></span></button>
        <button type="button" role="radio" aria-checked={pick === "brave"} className="choice compact" onClick={() => setPick("brave")}><span className="grow"><strong>Brave Search</strong><br /><span className="muted small">Key from brave.com/search/api.</span></span></button>
        <button type="button" role="radio" aria-checked={pick === "searxng"} className="choice compact" onClick={() => setPick("searxng")}><span className="grow"><strong>SearXNG add-on</strong> <span className="badge ok">Free, no key</span><br /><span className="muted small">Private search on your own server.</span></span></button>
      </div>

      {pick === "searxng" ? (
        <div className="col" style={{ gap: 6 }}>
          <p className="muted small" style={{ margin: 0 }}>Runs one small extra container (about 110 MB of memory). Run this in Terminal on the computer where ROOKS is installed, then reload this page:</p>
          <div className="row"><code className="mono grow" style={{ padding: "8px 10px", background: "var(--panel-2)", borderRadius: 7, overflowX: "auto" }}>{ADDON_CMD}</code>
            <button type="button" className="btn sm" onClick={() => { navigator.clipboard?.writeText(ADDON_CMD); toast.ok("Copied"); }} aria-label="Copy command"><Copy size={13} /> Copy</button></div>
        </div>
      ) : (
        <form className="col" style={{ gap: 6 }} onSubmit={save}>
          <label htmlFor="search-key" className="field">{NAMES[pick]} API key</label>
          <PasswordInput id="search-key" value={key} onChange={setKey} autoComplete="off" />
          <div className="row wrap"><button className="btn primary" disabled={busy || !key.trim()}>{busy ? <><Loader2 size={14} className="spin" /> Saving and testing…</> : "Save and test"}</button></div>
        </form>
      )}

      {test && (test.ok ? (
        <div className="alert ok" role="status">
          <strong>Web search works</strong> ({NAMES[test.provider ?? ""] ?? test.provider}). First results:
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{(test.results ?? []).slice(0, 3).map((r) => <li key={r.url}><a href={r.url} target="_blank" rel="noreferrer">{r.title || r.url}</a></li>)}</ul>
          {(test.results ?? []).length === 0 && <div>The search ran but returned nothing for the test query.</div>}
        </div>
      ) : <div className="alert err" role="alert"><strong>Not working yet.</strong> {test.error}</div>)}
      {toast.node}
    </div>
  );
}
