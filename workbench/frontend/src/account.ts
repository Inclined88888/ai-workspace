// Your plan: the account linked to AI Workspace Cloud, and which Pro features this install may use.
// The backend verifies the signed entitlement offline; this file only reads it and starts sign-in or checkout.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export type ProFeature = { id: string; label: string; unlocked: boolean };
export type Account = {
  cloud_configured: boolean;
  billing_live: boolean;
  signed_in: boolean;
  email: string;
  plan: string;
  plan_name: string;
  features: string[];
  pro_features: ProFeature[];
  credits: number | null;
  status: string | null;
  renews_at: string | null;
  team: { id: string; name: string; role: string } | null;
  offline_until: number | null;
  premium_themes: string[];
  setup_prompt: boolean;
};

export type Product = "pro_monthly" | "pro_yearly" | "team_monthly" | "business_monthly" | "credits_small" | "credits_medium" | "credits_large";

let cached: Account | null = null;
const listeners = new Set<(a: Account) => void>();

function publish(a: Account) {
  cached = a;
  listeners.forEach((l) => l(a));
  return a;
}

export const getAccount = () => api<Account>("/account").then(publish);
export const refreshAccount = () => api<Account>("/account/refresh", { method: "POST" }).then(publish);
export const startEmail = (email: string) => api<{ sent: boolean; email: string; expires_in?: number }>("/account/sign-in/email", { body: { email } });
export const enterCode = (code: string) => api<Account>("/account/sign-in/code", { body: { code } }).then(publish);
export const pollSignIn = () =>
  api<{ status: string; account?: Account }>("/account/sign-in/poll", { method: "POST" }).then((r) => {
    if (r.account) publish(r.account);
    return r;
  });
export const startOAuth = (provider: "google" | "apple") => api<{ url: string }>("/account/sign-in/oauth", { body: { provider } });
export const signOut = () => api<Account>("/account/sign-out", { method: "POST" }).then(publish);
export const skipSetup = () => api("/account/skip", { method: "POST" });
export const checkout = (product: Product, quantity = 1, team_id = "") => api<{ url: string }>("/account/checkout", { body: { product, quantity, team_id } });
export const billingPortal = () => api<{ url: string }>("/account/portal", { method: "POST" });
export const acceptInvite = (invite_code: string) => api<Account>("/account/invite", { body: { invite_code } }).then(publish);
export const providers = () => api<{ email: boolean; google: boolean; apple: boolean }>("/account/providers");

/** Free features and everything while billing is not live are always allowed. */
export function hasFeature(a: Account | null, feature: string): boolean {
  if (!a) return true; // unknown yet: never block on a slow or offline account check
  return !a.billing_live || a.features.includes(feature) || !a.pro_features.some((f) => f.id === feature);
}

export function themeLocked(a: Account | null, theme: string, defaultTheme = ""): boolean {
  return !!a && theme !== defaultTheme && a.premium_themes.includes(theme) && !hasFeature(a, "premium_themes");
}

/** Open a checkout or billing page outside the app window. */
export function openExternal(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

export function useAccount(): [Account | null, () => Promise<Account | void>] {
  const [a, setA] = useState<Account | null>(cached);
  useEffect(() => {
    listeners.add(setA);
    if (!cached) getAccount().catch(() => undefined);
    return () => void listeners.delete(setA);
  }, []);
  const reload = useCallback(() => getAccount().catch(() => undefined), []);
  return [a, reload];
}
export const teamCheckout = (name: string, seats: number, product: "team_monthly" | "business_monthly") =>
  api<{ url: string }>("/account/team-checkout", { body: { name, seats, product } });
