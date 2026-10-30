// Shared bits for the pack pages (feat/v02-packs).
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";

export type WorkFile = { id: number; name: string; kind: string };

export async function uploadFile(file: File): Promise<WorkFile> {
  const res = await fetch(`/api/files/upload?name=${encodeURIComponent(file.name)}`, {
    method: "POST", credentials: "same-origin", headers: { "X-Workbench": "1", "Content-Type": "application/octet-stream" }, body: file,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Could not add ${file.name}`);
  return body;
}

export function useFiles() {
  const [files, setFiles] = useState<WorkFile[]>([]);
  const reload = useCallback(() => { api<WorkFile[]>("/files").then(setFiles).catch(() => setFiles([])); }, []);
  useEffect(reload, [reload]);
  return { files, reload };
}

/** Pick a file from Files, or add one from this computer. */
export function FilePick({ value, onChange, accept, label, files, onAdded }: {
  value: number | null; onChange: (id: number | null) => void; accept?: string; label: string; files: WorkFile[]; onAdded: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  return (
    <div className="pk-filepick">
      <select className="input" aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{label}</option>
        {files.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
      </select>
      <label className="btn">
        {busy ? "Adding…" : "Add from computer"}
        <input type="file" hidden accept={accept} onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          setBusy(true); setErr("");
          try { const up = await uploadFile(f); onAdded(); onChange(up.id); } catch (x) { setErr(String((x as Error).message)); }
          setBusy(false);
          e.target.value = "";
        }} />
      </label>
      {err && <span className="pk-err">{err}</span>}
    </div>
  );
}

/** Shown when a pack page is opened before the pack is added (409) or without Pro (402). */
export function PackGate({ error, packId, name }: { error: unknown; packId: string; name: string }) {
  const status = error instanceof ApiError ? error.status : 0;
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState("");
  if (status === 402) {
    return <div className="pk-gate card"><p className="kicker">Pro</p><h2 className="pk-serif">{name} is part of Pro</h2>
      <p className="muted">{(error as Error).message}</p><Link className="btn primary" to="/settings">Go to Account</Link></div>;
  }
  return (
    <div className="pk-gate card">
      <p className="kicker">Pack</p>
      <h2 className="pk-serif">Add the {name} to use this page</h2>
      <p className="muted">It adds a few skills and shortcuts. Each one goes through the same security check as skills from GitHub.</p>
      <div className="row">
        <button className="btn primary" disabled={adding} onClick={async () => {
          setAdding(true); setMsg("");
          try { await api(`/packs/${packId}/add`, { method: "POST" }); window.location.reload(); } catch (x) { setMsg((x as Error).message); setAdding(false); }
        }}>{adding ? "Checking and adding…" : `Add ${name}`}</button>
        <Link className="btn ghost" to="/packs">All packs</Link>
      </div>
      {msg && <p className="pk-err">{msg}</p>}
    </div>
  );
}

export function NotAdvice({ text }: { text: string }) {
  return <p className="pk-notice" role="note"><strong>Not legal advice.</strong> {text.replace(/^This is general information[^.]*\.\s*It is not legal advice\.\s*/i, "")}</p>;
}

export function download(url: string) {
  window.open(url, "_blank", "noopener");
}

export function copy(text: string) {
  return navigator.clipboard?.writeText(text);
}
