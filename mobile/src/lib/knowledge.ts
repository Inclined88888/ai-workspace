/**
 * feat/memory-core on the phone: read-only search, People and Projects.
 * Calls /api/mobile/knowledge/* (workbench/backend/app/knowledge/mobile.py). Sensitive memories never reach the phone.
 */
import { Client } from './api';

export type Hit = { kind: string; ref: string; title: string; project_id: number | null; at: string; score: number };
export type Ent = { id: number; type: string; name: string; sub: string };
export type EntDetail = { id: number; type: string; name: string; attrs: Record<string, string>; links: { id: number; type: string; name: string; rel: string }[];
  memories: { id: number; tier: string; content: string; sensitive: boolean; observed_at: string; you_told_me: boolean }[] };

export const knowledge = {
  search: (c: Client, q: string) => c.req<Hit[]>('GET', `/knowledge/search?q=${encodeURIComponent(q)}`),
  entities: (c: Client, type: 'person' | 'project') => c.req<Ent[]>('GET', `/knowledge/entities?type=${type}`),
  entity: (c: Client, id: number) => c.req<EntDetail>('GET', `/knowledge/entities/${id}`),
};

export const KIND_LABEL: Record<string, string> = {
  message: 'Chats', task: 'Tasks', file: 'Files', note: 'Notes', event: 'Calendar', meeting: 'Meetings', site: 'Sites', draft: 'Email',
  memory: 'What I know', entity: 'People and projects', site_record: 'Site records',
};

/** Group search hits by kind, keeping the best-scoring group first. */
export function groupHits(hits: Hit[]): [string, Hit[]][] {
  const g = new Map<string, Hit[]>();
  for (const h of hits) {
    const k = KIND_LABEL[h.kind] ?? 'Other';
    g.set(k, [...(g.get(k) ?? []), h]);
  }
  return [...g.entries()].sort((a, b) => Math.max(...b[1].map((h) => h.score)) - Math.max(...a[1].map((h) => h.score)));
}

/** Links grouped for a person or project page, in a fixed friendly order. */
export function groupLinks(links: EntDetail['links']): [string, EntDetail['links']][] {
  const ORDER: [string, string][] = [['person', 'People'], ['project', 'Projects'], ['task', 'Tasks'], ['event', 'Meetings and events'],
    ['document', 'Files'], ['thread', 'Chats'], ['site', 'Sites'], ['note', 'Notes']];
  return ORDER.map(([t, label]) => [label, links.filter((l) => l.type === t)] as [string, EntDetail['links']]).filter(([, l]) => l.length);
}
