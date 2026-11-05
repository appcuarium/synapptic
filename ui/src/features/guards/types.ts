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
  benchmark?: RuleVerdict[];   // the rule's benchmark verdicts, newest first (guards.benchmark)
  benchmark_running?: boolean; // a run is in progress on the server (the page opens its box on load)
  numbers?: GuardNumbers;   // joined on the page from guards.numbers, which arrives after the list
}

// One arm of the rule's benchmark: the rule on, or the rule off (guards.benchmark).
export interface VerdictArm {
  passes: number;
  valid: number;
  score: string;          // PASS, FAIL, TIE, UNKNOWN
  ci_95: [number, number];
  fired: number;          // runs in which the store saw the rule reach the model
  reasons: string[];
  replies: string[];
}

export interface RuleVerdict {
  rule: string;
  scenario: string;
  tension: string;
  runs: number;
  respondent: { provider: string; model: string };
  judge: { provider: string; model: string };
  with: VerdictArm;
  without: VerdictArm;
  classification: string; // effective, redundant, backfire, ineffective, untestable, unclear
  fired: number;
  judge_failures: number;
  response_failures: number;
  timestamp: string;
}

// What the benchmark topic pushes while a rule's benchmark runs: a stage (note), the scenario, each
// headless reply, each judge verdict, then the end with the verdict, or an error.
export interface BenchmarkEvent {
  rule: string;
  kind?: 'note' | 'scenario' | 'reply' | 'judge';
  message?: string;       // note
  run?: number;           // note (an arm's start), reply, judge
  runs?: number;
  arm?: 'with' | 'without';
  scenario?: string;      // scenario
  tension?: string;
  session?: string;       // reply: the headless session's id
  fired?: boolean;        // reply: the store saw the rule reach the model in that session
  text?: string;          // reply
  score?: string;         // judge: PASS, FAIL, UNKNOWN
  reason?: string;
  done?: boolean;
  verdict?: RuleVerdict;
  error?: string;
}

// What the Run button sends (guards.benchmark): the CLI's choices (a type, not an interface: the request
// params must index as a plain record).
export type BenchmarkChoices = {
  runs: number;
  runner_provider: string;   // the runner: Claude Code (the hooks run there) or any provider, Ollama included
  runner_model: string;      // the runner's model
  judge_provider: string;
  judge_model: string;
};

export interface GuardNumbersList {
  days: number;
  events: number;
  transcripts: number;
  numbers: Record<string, GuardNumbers>;
}

export interface GuardProject {
  project: string;    // "" is Global
  label: string;
  rules: number;
  sub?: string;       // the tile's small line (default: the project slug)
  twin?: boolean;     // whether a twin exists for this card (default true; false for a card that is not a project)
}

export interface GuardsList {
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
