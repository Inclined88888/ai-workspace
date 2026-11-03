/** Pairing payloads: the QR code the desktop shows, or an address and code typed by hand. */

export type PairingOffer = { code: string; urls: string[]; name: string };

export const CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
export const DEFAULT_PORT = 8766; // the desktop app's phone listener (WORKBENCH_MOBILE_PORT)

/** Upper case, letters and digits only (the desktop shows ABCD-EFGH; the dash is optional). */
export function normaliseCode(input: string): string {
  return input.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
}

export function formatCode(code: string): string {
  const c = normaliseCode(code);
  return c.length > 4 ? `${c.slice(0, 4)}-${c.slice(4)}` : c;
}

export function isCompleteCode(code: string): boolean {
  const c = normaliseCode(code);
  return c.length === 8 && [...c].every((ch) => CODE_ALPHABET.includes(ch));
}

/** "192.168.1.20" -> "http://192.168.1.20:8766"; keeps scheme, port and path the user typed. */
export function normaliseAddress(input: string): string | null {
  let s = input.trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  let url: URL;
  try {
    url = new URL(s);
  } catch {
    return null;
  }
  if (!url.hostname) return null;
  if (!url.port && url.protocol === 'http:' && !input.includes('://')) url.port = String(DEFAULT_PORT);
  return `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, '')}`;
}

/** Parse aiworkspace://pair?c=CODE&u=<url>&u=<url>&n=<name>. Returns null for anything else. */
export function parsePairingUrl(data: string): PairingOffer | null {
  const s = (data || '').trim();
  const m = /^aiworkspace:\/\/pair\?(.*)$/i.exec(s);
  if (!m) return null;
  const urls: string[] = [];
  let code = '';
  let name = 'AI Workspace';
  for (const part of m[1].split('&')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    const k = part.slice(0, eq);
    let v: string;
    try {
      v = decodeURIComponent(part.slice(eq + 1).replace(/\+/g, ' '));
    } catch {
      continue;
    }
    if (k === 'c') code = normaliseCode(v);
    else if (k === 'u') {
      const u = normaliseAddress(v);
      if (u && !urls.includes(u)) urls.push(u);
    } else if (k === 'n' && v.trim()) name = v.trim().slice(0, 80);
  }
  if (!isCompleteCode(code) || urls.length === 0) return null;
  return { code, urls, name };
}
