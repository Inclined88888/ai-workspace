/** Packs on the phone (feat/v02-packs): review due flashcards and look over quotes and invoices. Nothing here sends. */
import { Client } from './api';

export type PackRow = { id: string; name: string; tagline: string; tier: string; installed: boolean };
export type Card = { id: number; deck: string; front: string; back: string; source: string; due_at: string; reps: number };
export type Deck = { deck: string; cards: number; due: number };
export type Grade = 'again' | 'hard' | 'good' | 'easy';
export type TradeDoc = {
  id: number; kind: 'quote' | 'invoice'; number: string; status: string; title: string; overdue_days: number; due_on: string | null;
  customer: { name: string; email: string }; totals: { total: number; tax: number; subtotal_ex_tax: number };
  items?: { description: string; qty: number; unit: string; unit_cost: number; kind: string }[];
};
export type Reminder = { id: number; tone: string; to: string; subject: string; body: string; send_on?: string };

export const packsApi = (c: Client) => ({
  list: () => c.req<PackRow[]>('GET', '/packs'),
  decks: () => c.req<Deck[]>('GET', '/packs/study/decks'),
  due: (deck = '') => c.req<Card[]>('GET', `/packs/study/due${deck ? `?deck=${encodeURIComponent(deck)}` : ''}`),
  review: (id: number, grade: Grade) => c.req<{ next_in: string }>('POST', `/packs/study/cards/${id}/review`, { grade }),
  docs: () => c.req<TradeDoc[]>('GET', '/packs/trade/docs'),
  doc: (id: number) => c.req<TradeDoc>('GET', `/packs/trade/docs/${id}`),
  reminders: (id: number) => c.req<{ drafts: Reminder[]; suggested: string; note: string }>('POST', `/packs/trade/docs/${id}/reminders`),
});

export const GRADES: { grade: Grade; label: string; hint: string }[] = [
  { grade: 'again', label: 'Again', hint: 'Forgot' },
  { grade: 'hard', label: 'Hard', hint: 'Got it, slowly' },
  { grade: 'good', label: 'Good', hint: 'Got it' },
  { grade: 'easy', label: 'Easy', hint: 'Instant' },
];

export function money(n: number): string {
  const v = Math.round((Number(n) || 0) * 100) / 100;
  return `$${v.toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function docStatus(d: Pick<TradeDoc, 'kind' | 'status' | 'overdue_days'>): { label: string; tone: 'ok' | 'warn' | 'quiet' } {
  if (d.kind === 'invoice' && d.status !== 'paid' && d.overdue_days > 0) return { label: `${d.overdue_days} days overdue`, tone: 'warn' };
  if (d.status === 'paid' || d.status === 'accepted') return { label: d.status === 'paid' ? 'Paid' : 'Accepted', tone: 'ok' };
  return { label: d.status.charAt(0).toUpperCase() + d.status.slice(1), tone: 'quiet' };
}

/** A reminder as one block for the share sheet: the person picks their own mail app and presses send themselves. */
export function shareText(r: Pick<Reminder, 'to' | 'subject' | 'body'>): string {
  return `${r.to ? `To: ${r.to}\n` : ''}Subject: ${r.subject}\n\n${r.body}`;
}
