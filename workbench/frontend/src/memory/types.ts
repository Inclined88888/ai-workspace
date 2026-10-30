// feat/memory-core: shapes returned by /api/knowledge
export type Mem = { id: number; tier: string; content: string; subject_id: number | null; project_id: number | null; scope: string; privacy: string;
  pinned: boolean; confidence: number; status: string; source_kind: string; source_ref: string; evidence: string; observed_at: string; uses: number };
export type Entity = { id: number; type: string; name: string; attrs: Record<string, string>; ref?: string; scope: string; updated_at?: string; rel?: string; direction?: string };
export type EntityDetail = Entity & { links: Entity[]; memories: Mem[] };
export type Why = { memory: Mem; summary: string; source: string; replaced: Mem[]; summarised_from: number[]; scope: string; privacy: string };
export type Conflict = { id: number; old: Mem; new: Mem; question: string };
export type Hit = { kind: string; ref: string; title: string; project_id: number | null; at: string; score: number };
export type Filing = { id: number; item_kind: string; item_ref: string; project_id: number; project_name: string; reason: string; status: string; chip: string };
export type Tidy = { duplicates: { type: string; a: Entity; b: Entity; why: string }[]; conflicts: Conflict[] };

export const TIER_LABEL: Record<string, string> = { profile: "About you", project: "Projects", procedural: "How you like things done", episodic: "Recent happenings" };
export const SCOPE_LABEL = (s: string) => s === "shared" ? "Shared with all your assistants" : s === "private" ? "Only you" : s.startsWith("assistant:") ? "One assistant only" : s.startsWith("team:") ? "Your team" : s;
export const KIND_ROUTE: Record<string, (ref: string) => string> = {
  message: () => "/assistant", task: () => "/tasks", file: () => "/files", note: (r) => `/notes/${r}`, event: () => "/calendar",
  meeting: () => "/meetings", site: () => "/sites", draft: () => "/mail", memory: () => "/knowledge?tab=memories", entity: (r) => `/knowledge?entity=${r}`,
  site_record: () => "/sites",
};
