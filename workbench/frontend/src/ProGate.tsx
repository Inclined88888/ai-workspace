// Pro gating in the UI. Free features are never wrapped. When a Pro feature is locked we show a friendly card,
// never an error, and nothing the user already made is hidden or removed.
import { Lock, Sparkles, X } from "lucide-react";
import { ReactNode, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hasFeature, themeLocked, useAccount } from "./account";
import { DEFAULTS, applyTo, useAppearance } from "./appearance";
import { AccountSetup } from "./pages/Plan";

type Upgrade = { feature?: string; label?: string; message?: string };

export function UpgradeCard({ feature, label, message, onClose }: Upgrade & { onClose?: () => void }) {
  const navigate = useNavigate();
  const [account] = useAccount();
  const go = () => { onClose?.(); navigate(`/plan${feature ? `?feature=${encodeURIComponent(feature)}` : ""}`); };
  return (
    <div className="card col gap-lg upgrade-card" role="region" aria-label="Upgrade to Pro">
      <div className="row">
        <span className="badge accent"><Sparkles size={12} /> Pro</span>
        <div className="spacer" />
        {onClose && <button className="btn ghost icon sm" onClick={onClose} aria-label="Close"><X size={16} /></button>}
      </div>
      <div className="col" style={{ gap: 6 }}>
        <h2 style={{ margin: 0 }}>{label ?? "This"} is part of Pro</h2>
        <p className="muted" style={{ margin: 0 }}>
          {message ?? "Upgrade to unlock it."} Everything you already made stays right where it is, and the free app keeps working.
        </p>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button className="btn primary" onClick={go}><Lock size={14} /> {account?.signed_in ? "See plans" : "Sign in or upgrade"}</button>
        {onClose && <button className="btn ghost" onClick={onClose}>Not now</button>}
      </div>
    </div>
  );
}

/** Wrap a Pro-only screen. While billing is not live, or the account is still loading, the children show. */
export function ProGate({ feature, label, children }: { feature: string; label?: string; children: ReactNode }) {
  const [account] = useAccount();
  if (hasFeature(account, feature)) return <>{children}</>;
  const f = account?.pro_features.find((p) => p.id === feature);
  return <UpgradeCard feature={feature} label={label ?? f?.label} />;
}

/** One app-wide listener: any API call answered with 402 upgrade_required shows the upgrade card. */
export function UpgradePrompt() {
  const [up, setUp] = useState<Upgrade | null>(null);
  useEffect(() => {
    const on = (e: Event) => setUp((e as CustomEvent<Upgrade>).detail ?? {});
    window.addEventListener("wb:upgrade", on);
    return () => window.removeEventListener("wb:upgrade", on);
  }, []);
  if (!up) return null;
  return (
    <div className="modal-back" onClick={() => setUp(null)}>
      <div className="modal" style={{ padding: 0, border: "none" }} onClick={(e) => e.stopPropagation()}>
        <UpgradeCard {...up} onClose={() => setUp(null)} />
      </div>
    </div>
  );
}

/** Mounted once inside the signed-in app: the optional sign-in step after first-run setup, the upgrade card, and
 * premium themes falling back to the default look (the saved choice is kept and comes back on upgrade). */
export function AccountLayer() {
  const [account] = useAccount();
  const [appearance] = useAppearance();
  const [stepDone, setStepDone] = useState(false);
  useEffect(() => {
    if (themeLocked(account, appearance.theme, DEFAULTS.theme)) applyTo(document.documentElement, { ...appearance, theme: DEFAULTS.theme });
  }, [account, appearance]);
  return (
    <>
      <UpgradePrompt />
      {account?.setup_prompt && !stepDone && (
        <div style={{ position: "fixed", inset: 0, zIndex: 60, background: "var(--bg)", overflow: "auto" }}>
          <AccountSetup onDone={() => setStepDone(true)} />
        </div>
      )}
    </>
  );
}
