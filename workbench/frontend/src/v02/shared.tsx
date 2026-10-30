// feat/v02-core: small pieces shared by Projects, History and Checked answers.
import { Sparkles } from "lucide-react";
import { ApiError } from "../api";

export const UPGRADE_TEXT: Record<string, string> = {
  projects_unlimited: "The free plan keeps one project going at a time. Archive one, or upgrade to Pro for unlimited projects.",
  team_tasks: "Giving work to your team and tracking it is part of the Team and Business plans. Your own task list stays free.", // feat/team-lead
  checked_answers: "Checked answers is part of Pro: every claim gets a source and a second check.",
};

export const isUpgrade = (e: unknown) => e instanceof ApiError && e.status === 402;

/** The friendly card shown instead of an error when a Pro feature is used on the free plan. */
export function UpgradeCard({ feature, onClose }: { feature: keyof typeof UPGRADE_TEXT | string; onClose?: () => void }) {
  return (
    <div className="card v02-upgrade" role="status">
      <Sparkles size={18} aria-hidden="true" />
      <div className="grow">
        <div className="v02-kicker">Pro</div>
        <div>{UPGRADE_TEXT[feature] ?? "This is part of Pro."}</div>
      </div>
      <a className="btn primary sm" href="/settings#plan">See plans</a>
      {onClose && <button className="btn ghost sm" onClick={onClose}>Not now</button>}
    </div>
  );
}

export type Project = {
  id: number; name: string; kind: string; description: string; keywords: string; status: string;
  counts?: Record<string, number>; next_deadline?: { title: string; due: string } | null; last_used_at?: string | null;
};
export type Limit = { unlimited: boolean; free_limit: number; active: number; can_add: boolean };
export type ProjectList = { projects: Project[]; active_id: number | null; kinds: Record<string, string>; limit: Limit };
