/**
 * Face ID / fingerprint before anything that acts for you from the phone: approving a held action or sending a draft
 * (feat/grok-style). With no biometrics enrolled, the device passcode is used; with neither, the step is refused.
 */
import * as LocalAuthentication from 'expo-local-authentication';

export type GuardResult = { ok: true } | { ok: false; reason: string };

export async function confirmWithBiometrics(what: string, la: Pick<typeof LocalAuthentication, 'hasHardwareAsync' | 'isEnrolledAsync' | 'authenticateAsync' | 'getEnrolledLevelAsync'> = LocalAuthentication): Promise<GuardResult> {
  try {
    const level = await la.getEnrolledLevelAsync();
    if (level === LocalAuthentication.SecurityLevel.NONE) return { ok: false, reason: 'Set a passcode or Face ID on this phone to approve from it.' };
    const r = await la.authenticateAsync({ promptMessage: what, cancelLabel: 'Cancel', disableDeviceFallback: false });
    return r.success ? { ok: true } : { ok: false, reason: r.error === 'user_cancel' ? 'Cancelled' : "Couldn't confirm it's you." };
  } catch {
    return { ok: false, reason: "Couldn't confirm it's you." };
  }
}
