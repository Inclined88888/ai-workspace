/**
 * Streaming-style reveal. The desktop answers a turn in one message event, so the phone reveals it at reading pace:
 * whole words, a little faster for long replies, capped so nobody waits more than ~2.2 s. Pure, tested.
 */

export function revealSteps(text: string, maxMs = 2200, tickMs = 16): number[] {
  if (!text) return [];
  const bounds: number[] = [];
  const re = /\S+\s*/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) bounds.push(m.index + m[0].length);
  if (!bounds.length || bounds[bounds.length - 1] !== text.length) bounds.push(text.length);
  const ticks = Math.max(1, Math.floor(maxMs / tickMs));
  const perTick = Math.max(1, Math.ceil(bounds.length / ticks));
  const out: number[] = [];
  for (let i = perTick - 1; i < bounds.length; i += perTick) out.push(bounds[i]);
  if (out[out.length - 1] !== text.length) out.push(text.length);
  return out;
}
