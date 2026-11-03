// feat/grok-style: Pages. Reports and plans your teammates save, which you can edit, export and share.
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import "./teammates.css";

type Page = { id: number; title: string; body?: string; shared: boolean; share_path: string; updated_at: string; teammate_id: number | null };

export default function Pages() {
  const { id } = useParams();
  const nav = useNavigate();
  const [list, setList] = useState<Page[]>([]);
  const [page, setPage] = useState<Page | null>(null);
  const [edit, setEdit] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Page[]>("/pages").then(setList).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setEdit(false); if (id) api<Page>(`/pages/${id}`).then(setPage).catch((e) => setErr(e.message)); else setPage(null); }, [id]);
  return <div className="tm-shell">
    <nav className="tm-side" aria-label="Pages">
      <div className="tm-side-head"><h2>Pages</h2><button className="btn primary sm" onClick={async () => {
        const p = await api<Page>("/pages", { body: { title: "Untitled page", body: "" } }); load(); nav(`/pages/${p.id}`);
      }}>New</button></div>
      {list.map((p) => <button key={p.id} className={`tm-person${String(p.id) === id ? " on" : ""}`} onClick={() => nav(`/pages/${p.id}`)}>
        <span><b>{p.title}</b><small>{p.shared ? "Shared · " : ""}{p.updated_at?.slice(0, 10)}</small></span></button>)}
      {!list.length && <p className="tm-muted">Ask a teammate to "save that as a page".</p>}
    </nav>
    <main className="tm-chat">
      {err && <p className="tm-err">{err}</p>}
      {page ? <>
        <header className="tm-chat-head"><div>{edit ? <input value={page.title} onChange={(e) => setPage({ ...page, title: e.target.value })} /> : <b>{page.title}</b>}</div>
          <button className="btn ghost sm" onClick={async () => { if (edit) { await api(`/pages/${page.id}`, { method: "PATCH", body: { title: page.title, body: page.body } }); load(); } setEdit(!edit); }}>{edit ? "Done" : "Edit"}</button>
          <a className="btn ghost sm" href={`/api/pages/${page.id}/export.html`}>Export HTML</a>
          <button className="btn ghost sm" onClick={async () => setPage({ ...page, ...(await api<Page>(`/pages/${page.id}/share?on=${!page.shared}`, { method: "POST" })) })}>{page.shared ? "Stop sharing" : "Share link"}</button>
          <button className="btn ghost sm" onClick={async () => { if (confirm("Delete this page?")) { await api(`/pages/${page.id}`, { method: "DELETE" }); load(); nav("/pages"); } }}>Delete</button></header>
        {page.shared && <p className="tm-muted tm-pad">Anyone with this link can read it: <a href={page.share_path} target="_blank" rel="noreferrer">{location.origin}{page.share_path}</a></p>}
        {edit ? <textarea className="tm-editor" value={page.body ?? ""} onChange={(e) => setPage({ ...page, body: e.target.value })} />
          : <iframe className="tm-viewer" title={page.title} sandbox="" src={`/api/pages/${page.id}/export.html`} />}
      </> : <p className="tm-empty">Pick a page, or make a new one.</p>}
    </main>
  </div>;
}
