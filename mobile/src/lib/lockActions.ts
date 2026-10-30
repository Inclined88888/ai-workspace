/**
 * Approve or decline from the lock screen (feat/grok-style). Approval pushes use the "approval" category; iOS asks for
 * Face ID / passcode before running the action (isAuthenticationRequired), and the app checks again with
 * confirmWithBiometrics before it calls the computer, so a locked phone in someone else's hand can't approve.
 */
export const APPROVAL_CATEGORY = 'approval';

export const approvalActions = [
  { identifier: 'approve', buttonTitle: 'Approve', options: { opensAppToForeground: false, isAuthenticationRequired: true } },
  { identifier: 'decline', buttonTitle: 'Decline', options: { opensAppToForeground: false, isAuthenticationRequired: true, isDestructive: true } },
  { identifier: 'open', buttonTitle: 'Open', options: { opensAppToForeground: true } },
];

export type LockDecision = { kind: 'decide'; id: number; approve: boolean } | { kind: 'open'; path: string } | null;

/** What a tapped notification action means. Anything malformed does nothing. */
export function decisionFor(actionIdentifier: string, data: Record<string, unknown> | undefined): LockDecision {
  const id = Number(data?.approval_id ?? data?.id);
  if (data?.kind !== 'approval' || !Number.isInteger(id) || id <= 0) return null;
  if (actionIdentifier === 'approve') return { kind: 'decide', id, approve: true };
  if (actionIdentifier === 'decline') return { kind: 'decide', id, approve: false };
  return { kind: 'open', path: '/approvals' };
}

export async function registerApprovalCategory(n: { setNotificationCategoryAsync: (id: string, actions: any[]) => Promise<unknown> }) {
  await n.setNotificationCategoryAsync(APPROVAL_CATEGORY, approvalActions);
}

export type LockDeps = {
  confirm: (what: string) => Promise<{ ok: boolean; reason?: string }>;
  decide: (id: number, approve: boolean) => Promise<unknown>;
  open: (path: string) => void;
  notify: (title: string, body: string) => Promise<unknown> | void;
};

/** Run a lock-screen action: Face ID / passcode first (again, in the app), then tell the computer. Any failure opens
 *  Approvals so nothing is lost; declining also needs the check, so a stranger can't silently reject things either. */
export async function handleLockAction(actionIdentifier: string, data: Record<string, unknown> | undefined, deps: LockDeps): Promise<string> {
  const d = decisionFor(actionIdentifier, data);
  if (!d) return 'ignored';
  if (d.kind === 'open') { deps.open(d.path); return 'opened'; }
  const what = String(data?.tool ?? 'this action').replace(/_/g, ' ');
  const g = await deps.confirm(`${d.approve ? 'Approve' : 'Decline'}: ${what}`);
  if (!g.ok) { deps.open('/approvals'); return 'not-confirmed'; }
  try {
    await deps.decide(d.id, d.approve);
    await deps.notify(d.approve ? 'Approved' : 'Declined', what);
    return d.approve ? 'approved' : 'declined';
  } catch {
    deps.open('/approvals');
    return 'failed';
  }
}
