// "Your plan": sign in to AI Workspace (optional), see what's unlocked, upgrade, buy credits, manage billing.
import { Apple, Check, CreditCard, ExternalLink, Loader2, Lock, LogOut, Mail, RefreshCw, Sparkles, Users } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Account, Product, acceptInvite, billingPortal, checkout, enterCode, openExternal, pollSignIn, providers, refreshAccount, signOut,
  skipSetup, startEmail, startOAuth, teamCheckout, useAccount,
} from "../account";

const PLANS = [
  { id: "free", name: "Free", price: "A$0", blurb: "The whole app with your own keys or Ollama. Forever." },
  { id: "pro", name: "Pro", price: "A$19/mo", blurb: "Cloud models with no setup, computer use, watchers, learning, phone sync and premium themes." },
  { id: "team", name: "Team", price: "A$29/seat/mo", blurb: "Pro for everyone, plus shared skills and admin controls." },
  { id: "business", name: "Business", price: "A$49/seat/mo", blurb: "Team plus SSO, audit export and priority support." },
];
const CREDITS: { id: Product; label: string }[] = [
  { id: "credits_small", label: "5,000 credits" },
  { id: "credits_medium", label: "15,000 credits" },
  { id: "credits_large", label: "50,000 credits" },
];

function errText(e: unknown) {
  return e instanceof Error ? e.message : String(e);
}

/** Email link or 6-digit code, plus Google and Apple when the server has them. Used by the page and the setup step. */
export function SignIn({ onSignedIn }: { onSignedIn?: (a: Account) => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [social, setSocial] = useState({ google: false, apple: false });
  const polling = useRef<number>();
  useEffect(() => { providers().then(setSocial).catch(() => undefined); }, []);
  useEffect(() => () => window.clearInterval(polling.current), []);

  const poll = () => {
    window.clearInterval(polling.current);
    polling.current = window.setInterval(() => {
      pollSignIn().then((r) => {
        if (r.status === "approved" && r.account) { window.clearInterval(polling.current); onSignedIn?.(r.account); }
        if (r.status === "expired") { window.clearInterval(polling.current); setErr("That sign-in expired. Start again."); setSent(false); }
      }).catch(() => undefined);
    }, 2500);
  };
  const send = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { await startEmail(email); setSent(true); poll(); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  const verify = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { const a = await enterCode(code); window.clearInterval(polling.current); onSignedIn?.(a); } catch (x) { setErr(errText(x)); } finally { setBusy(false); }
  };
  const oauth = async (p: "google" | "apple") => {
    setErr("");
    try { const { url } = await startOAuth(p); openExternal(url); poll(); } catch (x) { setErr(errText(x)); }
  };

  return (
    <div className="col gap-lg">
      {!sent ? (
        <form className="col" style={{ gap: 10 }} onSubmit={send}>
          <label className="field">Email
            <input className="input" type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </label>
          <button className="btn primary" disabled={busy || !email}>{busy ? <Loader2 size={14} className="spin" /> : <Mail size={14} />} Email me a sign-in link</button>
        </form>
      ) : (
        <form className="col" style={{ gap: 10 }} onSubmit={verify}>
          <p className="muted" style={{ margin: 0 }}>We sent a link and a 6-digit code to <strong>{email}</strong>. Click the link, or type the code here.</p>
          <label className="field">Code
            <input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="123456" />
          </label>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" disabled={busy || code.length < 6}>{busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />} Sign in</button>
            <button type="button" className="btn ghost" onClick={() => { setSent(false); setCode(""); window.clearInterval(polling.current); }}>Use another email</button>
          </div>
        </form>
      )}
      {(social.google || social.apple) && (
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {social.google && <button className="btn" onClick={() => oauth("google")}>Continue with Google</button>}
          {social.apple && <button className="btn" onClick={() => oauth("apple")}><Apple size={14} /> Continue with Apple</button>}
        </div>
      )}
      {err && <div className="alert err" role="alert">{err}</div>}
    </div>
  );
}

/** Shown once after first-run setup when accounts are switched on. Skipping keeps the free app as it is. */
export function AccountSetup({ onDone }: { onDone: () => void }) {
  const finish = () => { skipSetup().catch(() => undefined).finally(onDone); };
  return (
    <div className="auth welcome">
      <div className="card auth-card welcome-card col gap-lg">
        <div className="welcome-hero">
          <div className="kicker">Optional</div>
          <h1 className="welcome-title">Sign in to AI Workspace</h1>
          <p className="muted">Free works without an account. Sign in to use Pro: cloud models with no setup, computer use, website watchers, learning from a demo and phone sync.</p>
        </div>
        <SignIn onSignedIn={finish} />
        <button className="btn ghost" onClick={finish}>Skip, use Free</button>
      </div>
    </div>
  );
}

export default function Plan() {
  const [account, reload] = useAccount();
  const [params] = useSearchParams();
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [invite, setInvite] = useState("");
  const [teamName, setTeamName] = useState("");
  const [seats, setSeats] = useState(3);
  const wanted = params.get("feature");

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr("");
    try { await fn(); } catch (x) { setErr(errText(x)); } finally { setBusy(""); }
  };
  const buy = (p: Product) => run(p, async () => openExternal((await checkout(p)).url));

  if (!account) return <div className="page"><Loader2 className="spin" /></div>;
  const paid = account.plan !== "free";

  return (
    <div className="page col gap-lg plan-page">
      <div className="page-head col" style={{ alignItems: "flex-start", gap: 6 }}>
        <div className="kicker">Account</div>
        <h1>Your plan</h1>
        <p className="lede">Free is the whole app with your own keys or Ollama. Pro adds the things that need our servers.</p>
      </div>

      {!account.cloud_configured && <div className="alert">Accounts aren't switched on in this build yet, so every Pro feature is unlocked while in beta.</div>}
      {account.cloud_configured && !account.billing_live && <div className="alert">Pro features are free while in beta. Sign in now and your plan carries over.</div>}
      {wanted && account.billing_live && !account.features.includes(wanted) && (
        <div className="alert row"><Lock size={14} /> {account.pro_features.find((f) => f.id === wanted)?.label ?? "That feature"} needs Pro.</div>
      )}
      {err && <div className="alert err" role="alert">{err}</div>}

      <div className="grid two">
        <div className="card col gap-lg">
          <div className="row">
            <h2 style={{ margin: 0 }}>{account.plan_name}</h2>
            {paid && <span className="badge accent"><Sparkles size={12} /> {account.status === "past_due" ? "Payment due" : "Active"}</span>}
            <div className="spacer" />
            {account.signed_in && <button className="btn ghost sm" onClick={() => run("refresh", refreshAccount)} aria-label="Refresh plan">
              {busy === "refresh" ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />}</button>}
          </div>
          {account.signed_in ? (
            <div className="col" style={{ gap: 4 }}>
              <span>Signed in as <strong>{account.email}</strong></span>
              {account.team && <span className="muted"><Users size={13} /> {account.team.name} ({account.team.role})</span>}
              {account.credits != null && <span className="muted">{account.credits.toLocaleString()} cloud model credits left</span>}
              {account.renews_at && <span className="muted">Renews {new Date(account.renews_at).toLocaleDateString()}</span>}
              {account.offline_until && <span className="muted small">Works offline until {new Date(account.offline_until * 1000).toLocaleDateString()}</span>}
            </div>
          ) : account.cloud_configured ? (
            <SignIn onSignedIn={() => reload()} />
          ) : null}
          {account.signed_in && (
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              {paid && <button className="btn" onClick={() => run("portal", async () => openExternal((await billingPortal()).url))}><CreditCard size={14} /> Manage billing <ExternalLink size={12} /></button>}
              <button className="btn ghost" onClick={() => run("out", signOut)}><LogOut size={14} /> Sign out</button>
            </div>
          )}
        </div>

        <div className="card col" style={{ gap: 10 }}>
          <h2 style={{ margin: 0 }}>What's included</h2>
          {["Your own keys and Ollama", "Every Free feature, always"].map((l) => <div key={l} className="row"><Check size={14} /> {l}</div>)}
          {account.pro_features.map((f) => (
            <div key={f.id} className="row" style={{ opacity: f.unlocked ? 1 : 0.6 }}>{f.unlocked ? <Check size={14} /> : <Lock size={14} />} {f.label}</div>
          ))}
        </div>
      </div>

      {account.signed_in && (
        <>
          <div className="grid two">
            {PLANS.map((p) => (
              <div key={p.id} className={`card col gap-lg${account.plan === p.id ? " current" : ""}`} style={account.plan === p.id ? { borderColor: "var(--accent)" } : undefined}>
                <div className="row"><h3 style={{ margin: 0 }}>{p.name}</h3><div className="spacer" /><strong>{p.price}</strong></div>
                <p className="muted" style={{ margin: 0 }}>{p.blurb}</p>
                {p.id === "pro" && account.plan === "free" && (
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <button className="btn primary" disabled={!!busy} onClick={() => buy("pro_monthly")}>{busy === "pro_monthly" ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />} Upgrade monthly</button>
                    <button className="btn" disabled={!!busy} onClick={() => buy("pro_yearly")}>Yearly (save 2 months)</button>
                  </div>
                )}
                {(p.id === "team" || p.id === "business") && account.plan !== p.id && (
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <input className="input" style={{ flex: "1 1 140px" }} placeholder="Team name" value={teamName} onChange={(e) => setTeamName(e.target.value)} />
                    <input className="input" style={{ width: 80 }} type="number" min={1} max={500} value={seats} onChange={(e) => setSeats(Number(e.target.value) || 1)} aria-label="Seats" />
                    <button className="btn" disabled={!!busy || !teamName.trim()} onClick={() => run(p.id, async () => openExternal((await teamCheckout(teamName, seats, p.id === "team" ? "team_monthly" : "business_monthly")).url))}>
                      <Users size={14} /> Start {p.name}</button>
                  </div>
                )}
                {account.plan === p.id && <span className="badge ok">Your plan</span>}
              </div>
            ))}
          </div>

          <div className="grid two">
            <div className="card col gap-lg">
              <h3 style={{ margin: 0 }}>Cloud model credits</h3>
              <p className="muted" style={{ margin: 0 }}>Pro includes 5,000 a month. Top up any time; top-ups never expire.</p>
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                {CREDITS.map((c) => <button key={c.id} className="btn" disabled={!!busy} onClick={() => buy(c.id)}>{c.label}</button>)}
              </div>
            </div>
            <form className="card col gap-lg" onSubmit={(e) => { e.preventDefault(); run("invite", () => acceptInvite(invite)); }}>
              <h3 style={{ margin: 0 }}>Join a team</h3>
              <p className="muted" style={{ margin: 0 }}>Paste the invite code from your email.</p>
              <div className="row" style={{ gap: 8 }}>
                <input className="input grow" value={invite} onChange={(e) => setInvite(e.target.value)} placeholder="Invite code" />
                <button className="btn" disabled={!!busy || invite.trim().length < 8}>Join</button>
              </div>
            </form>
          </div>
        </>
      )}
    </div>
  );
}
