// Shapes of the server methods guards.list / guards.changes / guards.switch / guards.undo / profile.get
// (src/synapptic/dashboard/handlers.py): the same data `synapptic guards`, `stats`, `diff`, `rollback` and
// `archetype` use. The patterns a rule listens for never reach the page.
export interface DayNumbers {
  day: string;        // YYYY-MM-DD (UTC)
  fires: number;
  idle: number;
  misses: number;
}

export interface GuardNumbers {
  fires: number;      // cards shown over the window
  idle: number;       // shown in a turn that never touched the family
  misses: number;
  chars: number;
  sessions: number;
  daily: DayNumbers[];
}

export interface GuardRow {
  id: string;
  core?: boolean;     // a rule synapptic ships, read-only here (the author dashboard lists them)
  off: boolean;
  where: string;      // the directory pattern the rule applies under; "" = everywhere
  context: string;    // what the agent is told when a prompt matches
  message: string;    // what the agent is told when it is refused
  family: boolean;
  anchor: string;
  why: string;
  projects: string[]; // the indexed projects it applies to; [] = everywhere (Global)
  numbers: GuardNumbers;
}

export interface GuardProject {
  project: string;    // "" is Global
  label: string;
  rules: number;
  sub?: string;       // the tile's small line (default: the project slug)
  twin?: boolean;     // whether a twin exists for this card (default true; false for a card that is not a project)
}

export interface GuardsList {
  days: number;
  events: number;
  transcripts: number;
  projects: GuardProject[];
  rules: GuardRow[];
}

export interface GuardChange {
  id: string;
  ts: number;         // seconds since the epoch
  rule_id: string;
  kind: string;       // approved, promoted, narrowed, reverted, pruned, excluded, undo
  key: string;
  numbers: { fires: number; idle: number; misses: number } | null;
  undone?: number | null;
  added: number;      // alternatives the change brought in
  removed: number;    // alternatives it took away
}

export interface ProfileView {
  project: string;
  archetype: string;  // the narrative, markdown as written to the memory system
  dimensions: { name: string; count: number }[];
  version: number;
}
