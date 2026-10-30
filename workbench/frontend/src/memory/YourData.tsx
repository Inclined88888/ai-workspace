// feat/memory-core: Your data - automatic backups with restore, download everything, bring history from other apps.
import { Download, HardDriveDownload, RotateCcw, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { relTime } from "../util";

type Backup = { name: string; size: number; at: string; kind: string };
const SOURCES = [
  { id: "chatgpt", label: "ChatGPT", hint: "Settings → Data controls → Export. Upload the .zip you get by email." },
  { id: "claude", label: "Claude", hint: "Settings → Privacy → Export data. Upload the .zip." },
  { id: "grok", label: "Grok", hint: "Settings → Data → Download your data. Upload the .zip or .json." },
  { id: "notion", label: "Notion", hint: "Settings → Export → Markdown & CSV. Upload the .zip." },
  { id: "obsidian", label: "Obsidian", hint: "Zip your vault folder and upload it." },
];

export function YourData({ toast }: { toast: { ok: (t: string) => void; err: (e: unknown) => void } }) {
  const [list, setList] = useState<Backup[]>([]);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [source, setSource] = useState("chatgpt");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api<{ backups: Backup[] }>("/knowledge/backups").then((d) => setList(d.backups)).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const backupNow = async () => { await api("/knowledge/backups", { body: {} }); toast.ok("Backed up"); load(); };
  const restore = async (name: string) => {
    setConfirm(null);
    try { await api(`/knowledge/backups/${name}/restore`, { body: {} }); toast.ok("Restored. I kept a copy of how things were just before, in case you change your mind."); load(); }
    catch (e) { toast.err(e); }
  };
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`/api/knowledge/import/${source}`, { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Workbench": "1" } });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? "Import failed");
      toast.ok(`Brought in ${j.chats ?? j.notes ?? 0} ${j.chats !== undefined ? "chats" : "notes"} from ${j.source}${j.skipped ? ` (${j.skipped} already here)` : ""}`);
    } catch (e) { toast.err(e); } finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const hint = SOURCES.find((s) => s.id === source)?.hint;
  return (
    <div>
      <section className="kn-section kn-enter">
        <h3>Backups</h3>
        <p className="muted small" style={{ marginTop: 0 }}>I back everything up on this computer once a day and keep two weeks. Restoring keeps a copy of how things were, so it can be undone.</p>
        <button className="btn sm" onClick={backupNow}><HardDriveDownload size={14} /> Back up now</button>
        {list.slice(0, 8).map((b) => (
          <div key={b.name} className="kn-row">
            <div className="grow">{relTime(b.at)} <span className="kn-tag">{b.kind}</span><span className="kn-sub">{(b.size / 1e6).toFixed(1)} MB</span></div>
            {confirm === b.name
              ? <><span className="small">Go back to this point?</span><button className="btn sm primary" onClick={() => restore(b.name)}>Yes, restore</button><button className="btn sm ghost" onClick={() => setConfirm(null)}>Cancel</button></>
              : <button className="btn sm ghost" onClick={() => setConfirm(b.name)}><RotateCcw size={14} /> Restore</button>}
          </div>
        ))}
      </section>
      <section className="kn-section kn-enter">
        <h3>Take everything with you</h3>
        <p className="muted small" style={{ marginTop: 0 }}>Chats, notes, tasks, what I know, and your files, in open formats. Free, always.</p>
        <a className="btn sm" href="/api/knowledge/export.zip"><Download size={14} /> Download everything</a>
      </section>
      <section className="kn-section kn-enter">
        <h3>Bring your history</h3>
        <div className="row gap" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <select className="input" style={{ width: "auto" }} value={source} onChange={(e) => setSource(e.target.value)} aria-label="From">
            {SOURCES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <input ref={fileRef} type="file" accept=".zip,.json,.md,.txt" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn sm primary" disabled={busy} onClick={() => fileRef.current?.click()}><Upload size={14} /> {busy ? "Bringing it in…" : "Choose the export file"}</button>
        </div>
        <p className="muted small">{hint} It stays on this computer.</p>
      </section>
    </div>
  );
}
