/** The sidebar the first time someone opens the app. They can pin more pages and drag these into any order. */
export const DEFAULT_SIDEBAR = ["/", "/assistant", "/mail", "/calendar", "/tasks", "/notes"];

const KEY = "wb-sidebar";

export function sidebarKey() {
  return KEY;
}

/** Keep a saved order only when every entry is a real page. An empty list is a choice (everything unpinned). */
export function readSidebar(raw: string | null, known: readonly string[]): string[] {
  if (raw == null) return [...DEFAULT_SIDEBAR];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [...DEFAULT_SIDEBAR];
  }
  if (!Array.isArray(parsed)) return [...DEFAULT_SIDEBAR];
  const allowed = new Set(known);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of parsed) {
    if (typeof item !== "string" || !allowed.has(item) || seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out;
}

export function pinPage(order: readonly string[], path: string, known: readonly string[]): string[] {
  if (!known.includes(path) || order.includes(path)) return [...order];
  return [...order, path];
}

export function unpinPage(order: readonly string[], path: string): string[] {
  return order.filter((p) => p !== path);
}

/** Drop `from` onto `to`. Dragging down lands after the page you drop on; dragging up lands before it. */
export function movePage(order: readonly string[], from: string, to: string): string[] {
  const fromI = order.indexOf(from);
  const toI = order.indexOf(to);
  if (fromI < 0 || toI < 0 || fromI === toI) return [...order];
  const next = [...order];
  next.splice(fromI, 1);
  next.splice(fromI < toI ? toI : toI, 0, from);
  return next;
}
