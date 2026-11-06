// Benchmark (operator 2026-11-04, and 2026-11-05: the previous CLI's full benchmark over the rules, not a
// collect-all of one-rule runs): the guards strip picks the scope; the knowledge under test is listed first
// (every rule in scope, with its text, where it came from and its last verdict); the CLI's choices (runner,
// judge, runs per arm, how many rules, the seed, the runner's temperature, whether the scenarios kept for
// the seed are used or generated again) and a Run button start one job: the rules picked by the seed, one
// scenario per rule kept for that seed so both arms and every rerun face the same cases, the two control
// cases read by the judge, then each rule through the one-rule benchmark; the run streams in the same box
// the rule's own button uses; then the summary tiles, one row per rule with its verdict, counts, scenario
// and the judge's reasons on request; the runs so far by runner and judge (what `synapptic results`
// printed); past runs as a strip, with a compare and a flush. Built on Profile & Guards (strip, headers,
// blocks, label tones, runs toggles), the session browser's stat cells and the Settings fields.
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { NumberStepper, SettingRow, SettingSelect } from '@/components/machine/SettingsForm';
import { TileLabel, tileLabelStyle } from '@/components/machine/TileLabel';
import { TileStrip, useDragScroll } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { StatCell } from '@/features/sessions/SessionsPage';
import { cn } from '@/lib/cn';
import { useWsMutation, useWsQuery } from '@/lib/ws/react';
import { BenchmarkChoiceFields, BenchmarkLog, runnerNote } from './BenchmarkPanel';
import { formatWhen } from './format';
import { ALL, GLOBAL, appliesTo, labelTone, runToggleClasses, scopeOf } from './GuardsPage';
import { type Band, ratioBand, ratioTone, ruleOnTone, verdictBand, verdictTone } from './tone';
import type { BenchmarkChoices, GuardRow, GuardsList, RuleVerdict, RunControls, RunSummary, ScopeRun } from './types';
import { SCOPE_JOB, useScopeBenchmarkRun } from './useBenchmark';

// The Settings page's button corners (template tokens for the rest).
const buttonClasses = 'rounded-xs shadow-none';
const RUNS_QUERY = 'guards.benchmark.runs';
// Providers on Settings: the chosen tile at full strength, the others low.
const SELECTED_TILE_STRENGTH = 1;
const UNSELECTED_TILE_STRENGTH = 0.15;
// the CLI's --max-guards: how many rules of the scope the run takes
const MAX_RULES = 50;
const DEFAULT_RULE_COUNT = 10;
// the CLI's --seed (which rules, and the scenarios kept for them) and --temperature (the runner's)
const DEFAULT_SEED = 1337;   // operator 2026-11-06
const SEED_MAX = 999999;
// a stepper from 0 to 1: a click steps a tenth, a typed value keeps two decimals (operator 2026-11-06)
const MAX_TEMPERATURE = 1;
const TEMPERATURE_STEP = 0.1;
const TEMPERATURE_DECIMALS = 2;
const DEFAULT_TEMPERATURE = 0.1;
// the summary tiles, in the order the CLI printed them
const VERDICTS = ['effective', 'redundant', 'ineffective', 'backfire', 'untestable', 'stopped'];
// where a rule came from, in words
const ORIGIN_LABELS: Record<string, string> = {
  synthesis: 'learned from your sessions',
  keyword: 'from a RULE: prompt',
  hand: 'written by hand',
};

function projectLabel(list: GuardsList, project: string): string {
  if (project === ALL) return 'All';
  return list.projects.find((p) => p.project === project)?.label ?? project;
}

function runName(run: ScopeRun, list: GuardsList | undefined): string {
  const stamp = formatWhen(Date.parse(run.timestamp) / 1000);
  return `${list ? projectLabel(list, run.project) : run.project} · ${run.verdicts.length} rule${run.verdicts.length === 1 ? '' : 's'} · ${stamp}${run.stopped ? ' · stopped' : ''}`;
}

// The results rows wear the cards' and labels' schema (operator 2026-11-06: "copy the color schema from the
// slider cards and the rule labels"): the dark teal fill, the tiles' text for a label (the name line's 80%),
// the saturated 400 shades for the verdict values as Machine's stats bar has them on its dark surface (operator:
// "the verdict colors should be darker, not those washed colors"), the name line's shade for a zero.
// the summary row before the first rule of a run is done (operator 2026-11-06: both rows visible and updating
// while the benchmark runs): every number at zero until the first rule's end brings the real ones
const EMPTY_SUMMARY: RunSummary = {
  total: 0, testable: 0, effective: 0, redundant: 0, backfire: 0, ineffective: 0, untestable: 0, unclear: 0,
  with_pass_rate: 0, without_pass_rate: 0, delta: 0, with_ci_95: [0, 0], without_ci_95: [0, 0],
  judge_failures: 0, judge_calls: 0, control_comply: null, control_violate: null,
};
// both banner rows: the cells centred vertically, room above and below (operator 2026-11-06: "let it breathe")
const BANNER_ROW = 'flex items-center justify-around px-1 sm:px-2 py-2 min-h-[52px] overflow-x-auto';
const STRIP_LABEL = 'text-tile-fg/80';
// the values a step larger than the stat cell's own (operator 2026-11-06: "make the values text bigger")
const STRIP_VALUE = 'text-base sm:text-2xl';
const STRIP_PLAIN = cn('text-tile-fg', STRIP_VALUE);   // a fact (testable): the tiles' value text
// measured on machine.dev/strategy-analytics (2026-11-06): values text-teal-400 and text-rose-400; amber is
// not in its strip, its page uses text-amber-500
const STRIP_TEXT: Record<Band, string> = {
  teal: cn('text-teal-400', STRIP_VALUE), amber: cn('text-amber-500', STRIP_VALUE), rose: cn('text-rose-400', STRIP_VALUE), none: cn('text-tile-fg/80', STRIP_VALUE),
};
// a verdict count's colour: its verdict's, plain at zero; untestable and stopped are the run's own health
// (operator 2026-11-06, as judge failures): teal at zero, rose above
const HEALTH_COUNTS = new Set(['untestable', 'stopped']);

function healthBand(count: number): Band {
  return count > 0 ? 'rose' : 'teal';
}

function countBand(name: string, count: number): Band {
  if (HEALTH_COUNTS.has(name)) return healthBand(count);
  return count > 0 ? verdictBand(name) : 'none';
}

// The closing summary's numbers as the CLI printed them: whole percents, the interval only from five testable
// rules (the CLI's bar), and its caveat that the rules may be correlated.
const CI_MIN_TESTABLE = 5;

function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

function interval(ci: [number, number], testable: number): string {
  return testable >= CI_MIN_TESTABLE ? `95% CI ${percent(ci[0])} to ${percent(ci[1])}, assuming independent rules` : 'too few rules for an interval';
}

function controlsNote(controls: RunControls | null | undefined): string {
  if (!controls) return '';
  const word = (ok: boolean) => (ok ? 'ok' : 'broken');
  return `controls: comply ${word(controls.comply)}, violate ${word(controls.violate)}`;
}

// One rule of the knowledge under test: its label in its last verdict's tone, the scope and the origin, the
// text, the last verdict when it has one (the row's top half on Profile & Guards, without the numbers).
function KnowledgeRow({ rule }: { rule: GuardRow }) {
  const last = rule.benchmark?.[0];
  return (
    <div className="bg-page flex flex-col gap-1 px-4 py-3">
      <div className="flex items-center gap-3">
        <TileLabel tone={labelTone(rule.benchmark ?? [])}>{rule.id}</TileLabel>
        <span className="text-[13px] text-fg-subtle truncate">{scopeOf(rule)} · {ORIGIN_LABELS[rule.origin ?? 'hand'] ?? rule.origin}</span>
        {last && <span className={`ml-auto shrink-0 text-[13px] ${verdictTone(last.classification)}`}>{last.classification}</span>}
      </div>
      {rule.context && <p className="text-[13px] text-fg-subtle">{rule.context}</p>}
    </div>
  );
}

// The runs so far by runner and judge, what `synapptic results` printed: how many runs, the verdicts summed.
function AcrossRuns({ runs }: { runs: ScopeRun[] }) {
  const groups = new Map<string, { runs: number; last: string; counts: Record<string, number> }>();
  for (const run of runs) {
    const key = `runner ${run.choices.runner_provider} ${run.choices.runner_model} · judge ${run.choices.judge_provider || 'configured'} ${run.choices.judge_model || ''}`.trim();
    const group = groups.get(key) ?? { runs: 0, last: run.timestamp, counts: {} };
    group.runs += 1;
    if (run.timestamp > group.last) group.last = run.timestamp;
    for (const verdict of run.verdicts) group.counts[verdict.classification] = (group.counts[verdict.classification] ?? 0) + 1;
    groups.set(key, group);
  }
  return (
    <>
      {[...groups.entries()].map(([key, group]) => (
        <div key={key} className="bg-page flex flex-col gap-3 px-4 py-4">
          <span className="text-[13px] text-fg-subtle">{key} · {group.runs} run{group.runs === 1 ? '' : 's'}, the last {formatWhen(Date.parse(group.last) / 1000)}</span>
          <div className="flex items-start justify-around px-1 sm:px-2 min-h-[52px] overflow-x-auto">
            {VERDICTS.map((name) => (
              <StatCell key={name} label={name} value={String(group.counts[name] ?? 0)}
                valueClassName={(group.counts[name] ?? 0) > 0 ? verdictTone(name) : 'text-fg-subtle'} sub={`over ${group.runs} run${group.runs === 1 ? '' : 's'}`} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

// One rule of the run: its label in the rule-on tone, the scope, the arms and the verdict in the stats row
// (the other run's verdict beside it when comparing), the scenario, the judge's reasons on request.
function ScopeRuleRow({ verdict, rule, other, otherName }: { verdict: RuleVerdict; rule: GuardRow | undefined; other: RuleVerdict | undefined; otherName: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bg-page flex flex-col gap-3 px-4 py-4">
      {/* the label sized to its name, as the toggle row's label slot sizes it on Profile & Guards */}
      <div className="flex flex-col items-start gap-1">
        <TileLabel tone={labelTone([verdict])}>{verdict.rule}</TileLabel>
        {rule && <span className="text-[13px] text-fg-subtle">{scopeOf(rule)}</span>}
      </div>
      {rule?.context && <p className="text-[13px] text-fg-subtle">{rule.context}</p>}
      {/* the session browser's stats row: one row at every width, smaller cells on phones */}
      <div className="flex items-start justify-around px-1 sm:px-2 min-h-[52px] overflow-x-auto">
        {/* the rule's own numbers at the results rows' size (operator 2026-11-06) */}
        <StatCell label="rule on" value={`${verdict.with.passes}/${verdict.with.valid}`}
          valueClassName={cn(ruleOnTone(verdict.with, verdict.without), STRIP_VALUE)}
          sub={`complied with the rule on; the rule reached the model in ${verdict.fired} of ${verdict.runs} runs`} />
        <StatCell label="rule off" value={`${verdict.without.passes}/${verdict.without.valid}`}
          valueClassName={cn(ratioTone(verdict.without.passes, verdict.without.valid), STRIP_VALUE)} sub="complied with the rule off" />
        <StatCell label="verdict" value={verdict.classification} valueClassName={cn(verdictTone(verdict.classification), STRIP_VALUE)}
          sub={`majority vote over ${verdict.runs} runs per arm · judge ${verdict.judge.model}, runner ${verdict.respondent.model}`} />
        {other && (
          <StatCell label={otherName} value={other.classification} valueClassName={cn(verdictTone(other.classification), STRIP_VALUE)}
            sub={`the compared run: rule on ${other.with.passes}/${other.with.valid}, rule off ${other.without.passes}/${other.without.valid}`} />
        )}
      </div>
      <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle">{verdict.scenario}</pre>
      {verdict.tension && <p className="text-xs text-fg-faint">tension: {verdict.tension}</p>}
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs text-fg-faint">{open ? 'the judge’s reasons, rule on then rule off' : ''}</span>
        <Button variant="secondary" className={buttonClasses} onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Hide reasons' : 'Reasons'}
        </Button>
      </div>
      {open && (
        <div className="flex flex-col gap-3">
          {(['with', 'without'] as const).map((arm) => (
            <div key={arm} className="flex flex-col gap-1">
              <span className="text-[10px] text-fg-subtle uppercase tracking-wide">{arm === 'with' ? 'rule on' : 'rule off'}</span>
              {verdict[arm].reasons.length === 0 && <span className="text-xs text-fg-faint">no verdicts</span>}
              {verdict[arm].reasons.map((reason, i) => (
                <pre key={i} className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle">{reason}</pre>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// The strip of past runs, the rule's benchmark runs' way: one toggle per saved run, newest first.
// The run in flight as the strip's first card (operator 2026-11-06): the Sessions strip's live dot pulsing,
// the word running, the card, how many rules are done of the run's, and when it started; picked while it runs.
export const LIVE_RUN = 'live';
export interface LiveRun { label: string; done: number; total: number; startedAt: number }

function PastRunsStrip({ runs, picked, onPick, list, running }: {
  runs: ScopeRun[]; picked: string; onPick: (id: string) => void; list: GuardsList | undefined; running?: LiveRun;
}) {
  // Machine's strip (strategy-analytics, 2026-11-06): the row takes the page's width and scrolls inside it (the
  // toggle group's own w-fit grew it to its content, so there was nothing to scroll), dragged like the tiles
  const { ref, wasDragged } = useDragScroll();
  return (
    <ToggleGroup ref={ref} type="single" spacing={2} aria-label="Past runs" value={running ? LIVE_RUN : picked}
      onValueChange={(value) => { if (value && !wasDragged()) onPick(value); }} className="flex w-full justify-start overflow-x-auto strip-scroll">
      {running && (
        <ToggleGroupItem value={LIVE_RUN} className={runToggleClasses}>
          <span className="flex items-center gap-2">
            {/* the labels' dark teal (operator 2026-11-06), pulsing */}
            <span className="size-1.5 rounded-full motion-safe:animate-pulse" style={tileLabelStyle} aria-hidden="true" />
            <span>running · {running.label} · {running.done}/{running.total} rules · since {formatWhen(running.startedAt / 1000)}</span>
          </span>
        </ToggleGroupItem>
      )}
      {runs.map((run, index) => (
        <ToggleGroupItem key={run.id} value={run.id ?? String(index)} className={runToggleClasses}>
          <span>run {runs.length - index} · {runName(run, list)}</span>
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

export function BenchmarkPage() {
  const [params, setParams] = useSearchParams();
  const project = params.get('project') ?? GLOBAL;
  const guards = useWsQuery<GuardsList>('guards.list', {}, 'guards');
  const runs = useWsQuery<ScopeRun[]>(RUNS_QUERY);
  const queryClient = useQueryClient();
  // stable: the run's subscription takes it as its "done" callback
  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: [RUNS_QUERY] });
    queryClient.invalidateQueries({ queryKey: ['guards.list'] });
  }, [queryClient]);
  const [bench, startBench] = useScopeBenchmarkRun(refresh);
  const start = useWsMutation<Record<string, unknown>, BenchmarkChoices & { project: string; limit: number }>('guards.benchmark.scope');
  const stop = useWsMutation<Record<string, unknown>, { id: string }>('guards.benchmark.stop');
  const flush = useWsMutation<{ flushed: number }, Record<string, never>>('guards.benchmark.flush', { onSuccess: refresh });
  const forget = useWsMutation<{ flushed: number }, Record<string, never>>('guards.benchmark.flush_scenarios');
  const [confirmForget, setConfirmForget] = useState(false);
  const [choices, setChoices] = useState<BenchmarkChoices | null>(null);
  const [limit, setLimit] = useState(DEFAULT_RULE_COUNT);
  const [seed, setSeed] = useState(DEFAULT_SEED);
  const [temperature, setTemperature] = useState(DEFAULT_TEMPERATURE);
  const [regenerate, setRegenerate] = useState(false);   // generate the scenarios again instead of the ones kept for the seed
  const [picked, setPicked] = useState('');       // a past run's id; "" is the newest (or the one just finished)
  const [startedAt, setStartedAt] = useState(0);  // when the run in flight started, for its card in the strip
  const [compare, setCompare] = useState('');     // another run's id to read beside the picked one
  const [confirmFlush, setConfirmFlush] = useState(false);
  // a page reloaded mid-run takes the run's own choices (operator 2026-11-06: 17 rules picked, 10 shown after a
  // reload): the form shows what runs, and the totals count the run's rules, not the form's default
  useEffect(() => {
    if (!bench.choices) return;
    setLimit(bench.choices.limit);
    setSeed(bench.choices.seed);
    setTemperature(bench.choices.temperature);
    setStartedAt(bench.startedAt);   // the running card's "since", not the epoch
  }, [bench.choices, bench.startedAt]);
  const runLimit = bench.choices?.limit ?? limit;

  if (guards.error) {
    return (
      <PageHeader title="Benchmark">
        <span className="text-[13px] text-fg-subtle">Could not load the guards: {guards.error.message}</span>
      </PageHeader>
    );
  }
  if (!guards.data) {
    return <PageHeader title="Benchmark"><span className="text-[13px] text-fg-subtle">Loading…</span></PageHeader>;
  }

  const list = guards.data;
  const byId = new Map(list.rules.map((rule) => [rule.id, rule]));
  const scoped = list.rules.filter((rule) => !rule.core && appliesTo(rule, project));
  // the runs of the card picked in the strip only (operator 2026-11-06): another card's results never show here;
  // the All card holds every run, Global's and each project's
  const saved = (runs.data ?? []).filter((run) => project === ALL || run.project === project);
  const shownRun: ScopeRun | null = bench.run ?? (saved.find((run) => run.id === picked) ?? saved[0] ?? null);
  const verdicts = bench.running ? bench.verdicts : shownRun?.verdicts ?? [];
  // the closing summary: over the rules done so far while the run is in flight, the saved run's after
  const summary: RunSummary | undefined = bench.running ? bench.summary ?? EMPTY_SUMMARY : shownRun?.summary;
  const compared = saved.some((run) => run.id === compare) ? compare : '';
  const otherRun = compared ? saved.find((run) => run.id === compared) : undefined;
  const otherByRule = new Map((otherRun?.verdicts ?? []).map((verdict) => [verdict.rule, verdict]));
  const counts = VERDICTS.map((name) => [name, verdicts.filter((verdict) => verdict.classification === name).length] as const);
  const failure = start.error ?? stop.error ?? flush.error ?? runs.error;
  const label = projectLabel(list, project);
  const liveRun: LiveRun | undefined = bench.running ? { label, done: bench.verdicts.length, total: Math.min(runLimit, scoped.length), startedAt } : undefined;
  return (
    <section className="flex flex-col">
      <PageHeader title="Benchmark">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          the rule on against the rule off, over every rule under a card; {saved.length} past run{saved.length === 1 ? '' : 's'} for {label}
        </span>
      </PageHeader>

      <TileStrip label="Projects" selectedId={project}
        statusLeft={<span>{list.projects.length - 1} projects</span>}
        onSelect={(id: string) => setParams(id === GLOBAL ? {} : { project: id })}
        tiles={[
          // the All card first: every rule, whatever it applies to
          { id: ALL, name: `${list.rules.length} rule${list.rules.length === 1 ? '' : 's'}`, value: 'All', sub: 'every rule',
            strength: project === ALL ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH },
          ...list.projects.map((p) => ({
            id: p.project,
            name: `${p.rules} rule${p.rules === 1 ? '' : 's'}`,
            value: p.label,
            sub: p.sub ?? (p.project === GLOBAL ? 'applies everywhere' : p.project),
            strength: p.project === project ? SELECTED_TILE_STRENGTH : UNSELECTED_TILE_STRENGTH,
          })),
        ]} />

      {/* Machine's strategy edit panel: the strip, then a tab row, then the tab's content (operator 2026-11-05):
          Run first and by default, the guards under test, the history of runs */}
      <Tabs defaultValue="run">
        {/* 20px of air between the strip's cards and the tab row (operator 2026-11-05) */}
        <TabsList aria-label="Benchmark" className="mt-5">
          <TabsTrigger value="run">Run</TabsTrigger>
          <TabsTrigger value="guards">Guards · {scoped.length}</TabsTrigger>
          <TabsTrigger value="past">History · {saved.length}</TabsTrigger>
        </TabsList>

      <TabsContent value="run">
      <PageHeader title="Run">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          the rule on against the rule off, on one scenario per rule kept for the seed; the same seed reruns on the same scenarios
        </span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        <div className="bg-page flex flex-col gap-3 px-4 py-4">
          <BenchmarkChoiceFields onChange={setChoices}>
            <SettingRow id="bench-rules-label" label="Rules" note="how many rules the seed picks from the card">
              <NumberStepper value={limit} onChange={setLimit} min={1} max={MAX_RULES} labelledBy="bench-rules-label" />
            </SettingRow>
            <SettingRow id="bench-seed-label" label="Seed" note="which rules, and the scenarios kept for them">
              <NumberStepper value={seed} onChange={setSeed} min={0} max={SEED_MAX} labelledBy="bench-seed-label" />
            </SettingRow>
            <SettingRow id="bench-temperature-label" label="Temperature" note="the runner’s; 0 is the same answer every run">
              <NumberStepper value={temperature} onChange={setTemperature} min={0} max={MAX_TEMPERATURE} step={TEMPERATURE_STEP} decimals={TEMPERATURE_DECIMALS} labelledBy="bench-temperature-label" />
            </SettingRow>
            <SettingRow id="bench-scenarios-label" label="Scenarios" note="kept for the seed, or generated again">
              <SettingSelect id="bench-scenarios" labelledBy="bench-scenarios-label" value={regenerate ? 'fresh' : 'kept'} width="w-[180px]"
                onValueChange={(v) => setRegenerate(v === 'fresh')}
                options={[{ id: 'kept', label: 'the ones kept' }, { id: 'fresh', label: 'generated again' }]} />
            </SettingRow>
          </BenchmarkChoiceFields>
          <div className="flex items-start justify-between gap-3">
            <span className="text-xs text-fg-faint">{runnerNote(choices)}</span>
            <span className="flex items-center gap-2 shrink-0">
              {bench.running && (
                <Button variant="secondary" className={buttonClasses} onClick={() => stop.mutate({ id: SCOPE_JOB })}>Stop</Button>
              )}
              {/* the filled button (operator 2026-11-06: Run dark teal, as every filled button) */}
              <Button className={buttonClasses} disabled={bench.running || !choices || scoped.length === 0}
                onClick={() => { if (choices) { setStartedAt(Date.now()); startBench(); start.mutate({ ...choices, project, limit, seed, temperature, refresh: regenerate }); } }}>
                {bench.running ? `Running… ${bench.verdicts.length} of ${Math.min(runLimit, scoped.length)} done` : 'Run'}
              </Button>
            </span>
          </div>
          <BenchmarkLog events={bench.events} open={bench.running || bench.events.length > 0} named bordered />
          {bench.error && <p className="text-sm text-fg-muted">Not done: {bench.error}</p>}
        </div>
      </div>

      <PageHeader title="Results">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          {bench.running ? 'the run so far' : shownRun ? [`${runName(shownRun, list)} · runner ${shownRun.choices.runner_model}, judge ${shownRun.choices.judge_model || 'configured'}, ${shownRun.choices.runs} runs per arm`,
            shownRun.choices.seed !== undefined ? `seed ${shownRun.choices.seed}` : '', controlsNote(shownRun.controls)].filter(Boolean).join(' · ') : 'no run yet'}
        </span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        {/* the runs' history as the rule card shows it: one toggle per saved run, newest first, the picked one read below */}
        {(saved.length > 0 || liveRun) && (
          <div className="bg-page px-4 py-3">
            <PastRunsStrip runs={saved} list={list} picked={picked || saved[0]?.id || ''} onPick={(id) => setPicked(id)} running={liveRun} />
          </div>
        )}
        {verdicts.length === 0 && !bench.running && (
          <p className="bg-page p-4 text-sm text-fg-subtle gutter">No run yet. Pick a scope and press Run.</p>
        )}
        {/* the tiles from the run's first second (operator 2026-11-06), the counts ticking up as each rule's verdict lands,
            with how many rules are done of the run's */}
        {/* the stats rows on one surface in the cards' dark teal (operator 2026-11-06), a lighter teal line between
            the two rows, not the page's line */}
        {(verdicts.length > 0 || bench.running || Boolean(summary)) && (
        <div style={tileLabelStyle}>
        {(verdicts.length > 0 || bench.running) && (
          <div className={BANNER_ROW}>
            {bench.running && (
              <StatCell label="done" value={`${bench.verdicts.length}/${Math.min(runLimit, scoped.length)}`} valueClassName={STRIP_TEXT.teal} labelClassName={STRIP_LABEL}
                sub="rules judged so far, of the rules in this run" />
            )}
            {counts.map(([name, count]) => (
              <StatCell key={name} label={name} value={String(count)} valueClassName={STRIP_TEXT[countBand(name, count)]} labelClassName={STRIP_LABEL}
                sub={`rules whose verdict was ${name}`} />
            ))}
          </div>
        )}
        {/* the previous CLI's closing lines (operator 2026-11-06), once the run is done: testable of total, the pass
            rates with the rule on and off (the 95% interval as the tooltip, from five testable rules), the net
            impact with the rules improved and regressed, the judge's failures over its calls and its model */}
        {summary && (
          <div className={cn(BANNER_ROW, (verdicts.length > 0 || bench.running) && 'border-t border-teal-700')}>
            {/* testable, runner and judge are facts, not verdicts: the tiles' text (operator 2026-11-06) */}
            <StatCell label="testable" value={`${summary.testable}/${summary.total}`} valueClassName={STRIP_PLAIN} labelClassName={STRIP_LABEL}
              sub="rules the judge could read, of the rules in the run" />
            <StatCell label="rule on" value={percent(summary.with_pass_rate)} valueClassName={STRIP_TEXT[ratioBand(summary.with_pass_rate, 1)]} labelClassName={STRIP_LABEL}
              sub={`runs that complied with the rule on, over every judged run; ${interval(summary.with_ci_95, summary.testable)}`} />
            <StatCell label="rule off" value={percent(summary.without_pass_rate)} valueClassName={STRIP_TEXT[ratioBand(summary.without_pass_rate, 1)]} labelClassName={STRIP_LABEL}
              sub={`runs that complied with the rule off, over every judged run; ${interval(summary.without_ci_95, summary.testable)}`} />
            <StatCell label="impact" value={`${summary.delta >= 0 ? '+' : ''}${percent(summary.delta)}`}
              valueClassName={STRIP_TEXT[summary.delta > 0 ? 'teal' : summary.delta < 0 ? 'rose' : 'none']} labelClassName={STRIP_LABEL}
              sub={`the rule on's hit rate less the rule off's; ${summary.effective} rules improved, ${summary.backfire} regressed`} />
            <StatCell label="judge failures" value={String(summary.judge_failures)}
              valueClassName={STRIP_TEXT[healthBand(summary.judge_failures)]} labelClassName={STRIP_LABEL}
              sub={`of ${summary.judge_calls} judge calls`} />
          </div>
        )}
        </div>
        )}
        {verdicts.map((verdict) => (
          <ScopeRuleRow key={verdict.rule} verdict={verdict} rule={byId.get(verdict.rule)}
            other={otherByRule.get(verdict.rule)} otherName={otherRun ? formatWhen(Date.parse(otherRun.timestamp) / 1000) : ''} />
        ))}
      </div>

      </TabsContent>

      <TabsContent value="guards">
      <PageHeader title={project === ALL ? 'Guards under test' : project === GLOBAL ? 'Guards under test everywhere' : `Guards under test in ${label}`}>
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          the knowledge the run measures: {scoped.length} rule{scoped.length === 1 ? '' : 's'} in scope, the seed picks {Math.min(limit, scoped.length)}
        </span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        {scoped.length === 0 && <p className="bg-page p-4 text-sm text-fg-subtle gutter">No rules under this card.</p>}
        {scoped.map((rule) => <KnowledgeRow key={rule.id} rule={rule} />)}
      </div>
      </TabsContent>

      <TabsContent value="past">
      <PageHeader title="Across runs">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">every saved run by runner and judge, the verdicts summed</span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        {saved.length === 0 && <p className="bg-page p-4 text-sm text-fg-subtle gutter">None yet.</p>}
        {saved.length > 0 && <AcrossRuns runs={saved} />}
      </div>

      <PageHeader title="Runs">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">newest first; pick one to read it under Run, another to compare</span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        <div className="bg-page flex flex-col gap-3 px-4 py-4">
          {saved.length === 0 && !liveRun && <p className="text-sm text-fg-subtle">None yet.</p>}
          {(saved.length > 0 || liveRun) && (
            <PastRunsStrip runs={saved} list={list} picked={picked || saved[0]?.id || ''} onPick={(id) => setPicked(id)} running={liveRun} />
          )}
          <div className="flex items-end justify-between gap-3">
            <div className="min-w-0 max-w-[420px] grow">
              <SettingRow id="bench-compare-label" label="Compare with" note="a second run next to the picked one">
                <SettingSelect id="bench-compare" labelledBy="bench-compare-label" value={compared || 'none'} width="w-[220px]"
                  onValueChange={(id) => setCompare(id === 'none' ? '' : id)}
                  options={[{ id: 'none', label: 'nothing' }, ...saved.map((run) => ({ id: run.id ?? '', label: runName(run, list) }))]} />
              </SettingRow>
            </div>
            <span className="flex items-center gap-2 shrink-0">
              {/* the CLI's --flush-tests: the kept scenarios go, the next run generates them again */}
              <Button variant="secondary" className={buttonClasses} disabled={forget.isPending}
                onClick={() => { if (confirmForget) { forget.mutate({}); setConfirmForget(false); } else setConfirmForget(true); }}
                onBlur={() => setConfirmForget(false)}>
                {confirmForget ? 'Forget every kept scenario?' : 'Forget scenarios'}
              </Button>
              <Button variant="secondary" className={buttonClasses} disabled={saved.length === 0 || flush.isPending}
                onClick={() => { if (confirmFlush) { flush.mutate({}); setConfirmFlush(false); setPicked(''); setCompare(''); } else setConfirmFlush(true); }}
                onBlur={() => setConfirmFlush(false)}>
                {confirmFlush ? `Delete ${saved.length} run${saved.length === 1 ? '' : 's'}?` : 'Flush'}
              </Button>
            </span>
          </div>
        </div>
      </div>
      </TabsContent>
      </Tabs>
      {failure && <p className="p-4 text-sm text-fg-muted gutter">Not done: {failure.message}</p>}
    </section>
  );
}
