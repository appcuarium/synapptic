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
  origin?: string;    // where the rule came from: synthesis, keyword (a RULE: prompt), hand
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
  summary?: RunSummary;   // a rule's end on the Benchmark page: the closing summary over the rules done so far
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
  seed?: number;             // the Benchmark page: which rules, and the scenarios kept for them
  refresh?: boolean;         // generate the scenarios again
  temperature?: number;      // the runner's temperature
};

// The judge read on the two control cases, once per run (the previous CLI's calibration).
export interface RunControls {
  comply: boolean;
  violate: boolean;
  reasons: Record<string, string>;
}

// A run over a scope (the Benchmark page, the previous CLI's full benchmark): the rules picked by the seed,
// each on its kept scenario through the one-rule benchmark, kept as a file and listed newest first
// (guards.benchmark.runs).
// The previous CLI's closing summary of a run over a scope (2026-11-06): rates are 0..1, intervals [low, high].
export interface RunSummary {
  total: number;
  testable: number;
  effective: number;
  redundant: number;
  backfire: number;
  ineffective: number;
  untestable: number;
  unclear: number;
  with_pass_rate: number;
  without_pass_rate: number;
  delta: number;
  with_ci_95: [number, number];
  without_ci_95: [number, number];
  judge_failures: number;
  judge_calls: number;
  control_comply: boolean | null;
  control_violate: boolean | null;
}

export interface ScopeRun {
  id?: string;                 // the saved file's name (absent on the run just finished, until listed)
  summary?: RunSummary;        // the closing summary (absent while the run is in flight)
  timestamp: string;
  project: string;             // "" Global, "__all__" every rule, else a slug
  choices: BenchmarkChoices & { limit: number };
  rules: number;               // how many rules the seed picked
  selected?: string[];         // their ids, in the run's order
  controls?: RunControls | null;
  verdicts: RuleVerdict[];     // in the rules' order
  stopped: boolean;
}

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
