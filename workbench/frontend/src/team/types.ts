// feat/team-lead: shapes returned by /api/team.
export type Risk = { level: "late" | "at_risk" | "stuck"; label: string } | null;
export type Load = { member_id: number; open: number; hours: number; capacity: number; percent: number; label: string };
export type Member = {
  id: number; name: string; first_name: string; role: string; skills: string[]; channel: string; has_app: boolean;
  hours_per_week: number; work_days: string[]; away: { from: string; to: string; note: string }[]; contact: Record<string, string>;
  link_url: string; last_heard_at: string | null; load: Load;
};
export type Task = {
  id: number; title: string; notes: string; assignee_id: number | null; status: "todo" | "doing" | "blocked" | "done"; percent: number;
  priority: number; start: string | null; due: string | null; project: string; project_id: number | null; estimate_hours: number;
  blocked_reason: string; source: string; created_at: string; assigned_at: string | null; risk?: Risk; checklist: { text: string; done: boolean }[];
};
export type Suggestion = { id: number; kind: "assign" | "rebalance" | "next"; headline: string; reason: string; member_id: number; member_name: string; task: Task | null };
export type OutboxItem = { id: number; member_id: number; task_id: number | null; kind: string; channel: string; subject: string; body: string; status: string; why: string; error: string };
export type Reply = { id: number; member_id: number; member_name: string; task_id: number | null; task_title: string | null; summary: string; text: string; source: string; created_at: string };
export type Settings = {
  auto_notify: boolean; auto_nudge: boolean; auto_assign: boolean; digest: string; stuck_days: number; read_inbox: boolean;
  replies_review: boolean; privacy: string; team_unlocked: boolean;
  sms: { configured: boolean; from: string };
  trackers: Record<"jira" | "asana" | "trello", { connected: boolean; fields: Record<string, string>; last_sync: string }>;
};
export type Overview = {
  members: Member[]; tasks: Task[]; counts: Record<string, number>; at_risk: number; unassigned: number; settings: Settings;
  suggestions: Suggestion[]; outbox: OutboxItem[]; replies: Reply[];
};
export type PlanPerson = { member: { id: number; name: string; first_name: string; role: string }; working: boolean; tasks: Task[]; message: string; load?: Load };
export type Plan = { date: string; people: PlanPerson[]; unassigned: Task[] };
export type Notice = { id: number; kind: string; title: string; body: string; seen: number; created_at: string; task_id: number | null };

export const STATUS_LABEL: Record<string, string> = { todo: "To do", doing: "Doing", blocked: "Stuck", done: "Done" };
export const PRIORITY_LABEL = ["", "Low", "High", "Urgent"];
export const CHANNEL_LABEL: Record<string, string> = { email: "Email", sms: "Text", telegram: "Telegram", slack: "Slack", link: "Private link", app: "AI Workspace app" };

/** Days between two ISO dates (b - a). */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);
}
export function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export function niceDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
/** Timeline bars: start (or assigned/created) to due, clipped to the window. Pure so it can be tested. */
export function bar(t: Pick<Task, "start" | "due" | "created_at" | "assigned_at" | "estimate_hours">, from: string, days: number) {
  const end = t.due ?? null;
  const begin = t.start ?? (t.assigned_at ?? t.created_at ?? "").slice(0, 10);
  if (!end && !begin) return null;
  const s = begin && end && begin > end ? end : begin || end!;
  const e = end || addDays(s, Math.max(0, Math.ceil(t.estimate_hours / 8) - 1));
  const a = Math.max(0, daysBetween(from, s)), b = Math.min(days - 1, daysBetween(from, e));
  if (b < 0 || a > days - 1) return null;
  return { left: a, width: b - a + 1 };
}
