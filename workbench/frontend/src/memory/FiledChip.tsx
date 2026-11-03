// feat/memory-core: "Filed under X, change?" chip. Drop next to any chat, task, file or note.
import { FolderKanban } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { Entity, Filing } from "./types";
import "./memory.css";

export function FiledChip({ kind, itemRef, initial }: { kind: string; itemRef: string; initial?: Filing | null }) {
  const [f, setF] = useState<Filing | null>(initial ?? null);
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Entity[]>([]);
  useEffect(() => {
    if (initial === undefined) api<Filing | null>(`/knowledge/filings/${kind}/${encodeURIComponent(itemRef)}`).then(setF).catch(() => {});
  }, [kind, itemRef, initial]);
  if (!f) return null;
  const change = async (pid: number | null) => {
    setOpen(false);
    setF(await api<Filing | null>(`/knowledge/filings/${f.id}/change`, { body: { project_id: pid } }));
  };
  const showMenu = () => {
    setOpen((o) => !o);
    if (!projects.length) api<Entity[]>("/knowledge/entities?type=project").then(setProjects).catch(() => {});
  };
  return (
    <span className="kn-chip" title={f.reason ? `Because it ${f.reason}` : undefined}>
      <FolderKanban size={13} /> Filed under <b>{f.project_name}</b>
      {f.status === "filed" && <>
        <button className="kn-link" onClick={() => api(`/knowledge/filings/${f.id}/ok`, { body: {} }).then(() => setF({ ...f, status: "confirmed", chip: `Filed under ${f.project_name}` }))}>Right</button>
        <button className="kn-link" onClick={showMenu} aria-expanded={open}>Change?</button>
      </>}
      {f.status !== "filed" && <button className="kn-link" onClick={showMenu} aria-expanded={open}>Move</button>}
      {open && (
        <span className="kn-menu" role="menu">
          {projects.filter((p) => p.id !== f.project_id).map((p) => <button key={p.id} role="menuitem" onClick={() => change(p.id)}>{p.name}</button>)}
          <button role="menuitem" onClick={() => change(null)}>Not part of a project</button>
        </span>
      )}
    </span>
  );
}
