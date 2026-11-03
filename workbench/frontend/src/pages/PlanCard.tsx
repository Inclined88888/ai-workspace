import { ArrowRight, Check, Download, ExternalLink, Loader2, UserRound } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

type PlanState = { installed: boolean; signed_in: boolean; account: string; can_install: boolean; connected: boolean; name: string; terms: string; quota?: string };
type SignIn = { id: string; status: string; url: string; code: string; needs_code: boolean; error: string };

/** A plan through the vendor's own app (Claude Code, Codex), all from here: install, sign in, start chatting. No terminal. */
export default function PlanCard({ agent, title, blurb, onConnected, cta = "Start chatting" }: {
  agent: "claude" | "codex" | "copilot"; title: string; blurb: string; onConnected: () => Promise<void> | void; cta?: string;
}) {
  const [st, setSt] = useState<PlanState | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [signin, setSignin] = useState<SignIn | null>(null);
  const [code, setCode] = useState("");
  const timer = useRef<number>();
  const load = useCallback(async () => {
    try { setSt((await api<Record<string, PlanState>>("/plans"))[agent]); } catch (e) { setError((e as Error).message); }
  }, [agent]);
  useEffect(() => { load(); return () => window.clearInterval(timer.current); }, [load]);
  const act = async (label: string, f: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try { await f(); } catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  };
  const install = () => act("Installing… this takes about a minute", async () => {
    setSt(await api<PlanState>(`/plans/${agent}/install`, { method: "POST" }));
  });
  const start = () => act("Opening the sign-in…", async () => {
    const s = await api<SignIn>(`/plans/${agent}/signin`, { method: "POST" });
    setSignin(s);
    if (s.url) window.open(s.url, "_blank", "noopener");
    window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      const n = await api<SignIn>(`/plans/signin/${s.id}`).catch(() => null);
      if (n) setSignin(n);
      if (!n || (n.status !== "waiting" && n.status !== "starting")) { window.clearInterval(timer.current); load(); }
    }, 2000);
  });
  const sendCode = () => act("Finishing…", async () => {
    const n = await api<SignIn>(`/plans/signin/${signin!.id}/code`, { body: { code: code.trim() } });
    setSignin(n);
    if (n.status === "failed") throw new Error(n.error || "That code didn't work. Press Sign in again and copy the newest code.");
    await load();
  });
  const go = () => act("Connecting…", async () => {
    await api(`/plans/${agent}/connect`, { method: "POST" });
    await onConnected();
  });

  return (
    <div className="sub-card on">
      <div className="row" style={{ gap: 8 }}><strong className="sub-name">{title}</strong>{st?.signed_in && <span className="badge ok"><Check size={11} /> Signed in</span>}</div>
      <p className="small muted">{blurb} {st && <a href={st.terms} target="_blank" rel="noreferrer">Terms</a>}</p>
      {error && <div className="alert err">{error}</div>}
      {!st ? <Loader2 size={14} className="spin" /> : busy ? (
        <div className="row small"><Loader2 size={14} className="spin" /> {busy}</div>
      ) : !st.installed ? (
        st.can_install ? (
          <div className="row"><button className="btn primary" onClick={install}><Download size={15} /> Set up {st.name}</button></div>
        ) : (
          <div className="col small" style={{ gap: 6 }}>
            <span>This needs Node.js first. It's free and installs like any other app.</span>
            <div className="row">
              <a className="btn sm" href="https://nodejs.org/en/download" target="_blank" rel="noreferrer">Get Node.js <ExternalLink size={12} /></a>
              <button className="btn sm ghost" onClick={load}>I've installed it</button>
            </div>
          </div>
        )
      ) : st.signed_in ? (
        <div className="row">
          <span className="small grow">{st.account ? `Signed in as ${st.account}` : `Signed in with your ${st.name} plan`}{st.quota ? <span className="muted"> · {st.quota}</span> : null}</span>
          <button className="btn primary" onClick={go}>{cta} <ArrowRight size={16} /></button>
        </div>
      ) : signin && signin.status === "waiting" ? (
        <div className="col" style={{ gap: 8 }}>
          {signin.needs_code ? (
            <>
              <ol className="small plan-steps"><li>Sign in on the page that just opened.</li><li>Copy the code it shows you.</li><li>Paste it here.</li></ol>
              <div className="row">
                <input className="input mono grow" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Paste the code" autoFocus aria-label="Sign-in code" />
                <button className="btn primary" disabled={code.trim().length < 4} onClick={sendCode}>Finish</button>
              </div>
            </>
          ) : (
            <>
              <span className="small">Sign in on the page that just opened and type this code:</span>
              <div className="device-code mono" aria-label="One-time code">{signin.code}</div>
              <span className="row small muted"><Loader2 size={13} className="spin" /> Waiting for you to approve it…</span>
            </>
          )}
          <a className="small" href={signin.url} target="_blank" rel="noreferrer">Open the sign-in page again</a>
        </div>
      ) : (
        <div className="col" style={{ gap: 6 }}>
          {signin?.status === "failed" && <div className="alert err">{signin.error || "Sign-in didn't finish. Try again."}</div>}
          <div className="row"><button className="btn primary" onClick={start}><UserRound size={15} /> Sign in with {st.name}</button></div>
        </div>
      )}
    </div>
  );
}
