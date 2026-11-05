// Profile & guards: a strip of projects with a Global card first (the rules that apply everywhere); for the
// selected card, the user's twin (the archetype `synapptic archetype` shows) and the rules that apply there,
// each with its numbers over the window as a chart and an on/off switch; then the changes to the rules'
// words (`synapptic diff`) with the undo (`synapptic rollback`). The words themselves never reach this page.
// A dashboard built on this one passes its own rule list method, more to show under a rule (`extras`) and
// what opens under a rule on request (`details`), and gets the same page.
// Built on the Settings page's pieces (TileStrip, ToggleRow, the buttons), the session browser's stat cells.
import { useQueryClient } from '@tanstack/react-query';
import { type ReactNode, useCallback, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { ToggleRow } from '@/components/machine/OnOffToggle';
import { TileLabel } from '@/components/machine/TileLabel';
import { TileStrip } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { StatCell } from '@/features/sessions/SessionsPage';
import { formatCount } from '@/features/sessions/format';
import { useWsMutation, useWsQuery } from '@/lib/ws/react';
import { changeSummary, formatWhen } from './format';
import { RuleChart } from './RuleChart';
import { BenchmarkPanel } from './BenchmarkPanel';
import { type Band, ratioTone, ruleOnBand, ruleOnTone, verdictTone } from './tone';
import type { BenchmarkChoices, GuardChange, GuardNumbers, GuardNumbersList, GuardRow, GuardsList, ProfileView, RuleVerdict } from './types';
import { useBenchmarkRun } from './useBenchmark';

// The Settings page's button corners (template tokens for the rest).
const buttonClasses = 'rounded-xs shadow-none';
const CHANGES_QUERY = 'guards.changes';
const DEFAULT_WINDOW_DAYS = 14;
// a rule the numbers did not mention: nothing happened in the window
const EMPTY_NUMBERS: GuardNumbers = { fires: 0, idle: 0, misses: 0, chars: 0, sessions: 0, daily: [] };
// Providers on Settings: the chosen tile at full strength, the others low.
const SELECTED_TILE_STRENGTH = 1;
const UNSELECTED_TILE_STRENGTH = 0.15;
export const GLOBAL = '';
export const ALL = '__all__';   // the strip's All card: every rule on one page (operator, 2026-11-04)

export interface GuardsPageProps<R extends GuardRow> {
  rulesMethod?: string;                     // a method answering in guards.list's shape (default guards.list)
  rulesTopic?: string;                      // the topic that pushes that list (default guards)
  numbersMethod?: string;                   // the rules' numbers by id (default guards.numbers), asked for after the list
  extras?: (rule: R) => ReactNode;          // more about a rule, shown under its text
  details?: (rule: R) => ReactNode;         // what opens under a rule when asked for
  detailsLabel?: string;                    // the button that opens it (default "Events")
}

export function appliesTo(rule: GuardRow, project: string): boolean {
  if (project === ALL) return true;
  return project === GLOBAL ? rule.projects.length === 0 && !rule.where : rule.projects.includes(project);
}

// The scope under a guard's name as the strip names it (operator, 2026-11-05): the project's card label,
// "Global" for a rule everywhere, "Core" for synapptic's own; a project the index does not know keeps its pattern.
export function scopeOf(rule: GuardRow): string {
  if (rule.projects.length > 0) return rule.projects.map((p) => (p === '__core__' ? 'Core' : p)).join(', ');
  return rule.where ? `under ${rule.where}` : 'Global';
}

// A rule's label wears its effectiveness (operator 2026-11-05): the rule-on colour of its latest benchmark
// run (teal keeps the label's own fill), the outline alone for a rule never benchmarked or whose latest run
// measured nothing.
export function labelTone(verdicts: RuleVerdict[]): 'amber' | 'rose' | 'outline' | undefined {
  const latest = verdicts[0];
  const band: Band = latest ? ruleOnBand(latest.with, latest.without) : 'none';
  return band === 'teal' ? undefined : band === 'none' ? 'outline' : band;
}

// The strip of a rule's benchmark runs, Machine's autoperf iterations: one toggle per run, newest first,
// the picked one in the accent; the picked run's numbers sit in the stats row and its scenario under it.
export const runToggleClasses =
  'h-8 shrink-0 px-3 rounded-md border border-line bg-transparent text-[13px] font-normal text-fg-subtle shadow-none ' +
  'data-[state=on]:border-primary data-[state=on]:text-primary data-[state=on]:bg-transparent hover:bg-control-hover';

function RunsStrip({ verdicts, picked, onPick }: { verdicts: RuleVerdict[]; picked: number; onPick: (index: number) => void }) {
  return (
    <ToggleGroup type="single" spacing={2} aria-label="Benchmark runs" value={String(picked)}
      onValueChange={(value) => { if (value) onPick(Number(value)); }} className="flex justify-start overflow-x-auto strip-scroll">
      {verdicts.map((verdict, index) => (
        <ToggleGroupItem key={verdict.timestamp} value={String(index)} className={runToggleClasses}>
          {/* the verdict word in its own colour, the panel's judge line's way (operator 2026-11-05);
              one span for the line, or the toggle's flex gap opens around the word */}
          <span>run {verdicts.length - index} · <span className={verdictTone(verdict.classification)}>{verdict.classification}</span> · {formatWhen(Date.parse(verdict.timestamp) / 1000)}</span>
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function RuleBlock<R extends GuardRow>({ rule, days, onSwitch, onBenchmark, onBenchmarkStop, onBenchmarkDone, disabled, extras, details, detailsLabel }: {
  rule: R; days: number; onSwitch: (off: boolean) => void; onBenchmark: (choices: BenchmarkChoices) => void;
  onBenchmarkStop: () => void; onBenchmarkDone: () => void; disabled: boolean;
  extras?: (rule: R) => ReactNode; details?: (rule: R) => ReactNode; detailsLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [benchOpen, setBenchOpen] = useState(Boolean(rule.benchmark_running));   // a run in progress opens its box
  const [bench, startBench] = useBenchmarkRun(rule.id, onBenchmarkDone);
  const blockRef = useRef<HTMLDivElement>(null);   // Run brings the rule to the top of the page (under the bar)
  const n = rule.numbers;
  // the rule's benchmark runs, newest first; the picked one's numbers sit beside the rule's own
  const verdicts = rule.benchmark ?? [];
  const [picked, setPicked] = useState(0);
  const verdict = verdicts[Math.min(picked, Math.max(0, verdicts.length - 1))];
  return (
    <div ref={blockRef} className="bg-page flex flex-col gap-3 px-4 py-4 scroll-mt-12">
      <ToggleRow id={`guard-${rule.id}`} label={<TileLabel tone={labelTone(verdicts)}>{rule.id}</TileLabel>} note={scopeOf(rule)} description={scopeOf(rule)}
        on={!rule.off} onChange={(on) => onSwitch(!on)} disabled={disabled || Boolean(rule.core)} />
      {(rule.context || rule.message) && (
        <p className="text-[13px] text-fg-subtle">{rule.context || rule.message}</p>
      )}
      {extras?.(rule)}
      {n ? (
        <>
          <RuleChart id={rule.id} daily={n.daily} days={days} family={rule.family} />
          {/* the session browser's stats row: one row at every width, smaller cells on phones */}
          <div className="flex items-start justify-around px-1 sm:px-2 min-h-[52px] overflow-x-auto">
            <StatCell label="shown" value={formatCount(n.fires)} sub={`${n.sessions} session${n.sessions === 1 ? '' : 's'}`} />
            <StatCell label="idle" value={formatCount(n.idle)} sub="shown, not needed" />
            {rule.family && <StatCell label="missed" value={formatCount(n.misses)} sub={rule.anchor ? `not shown for ${rule.anchor}` : 'not shown'} />}
            <StatCell label="characters" value={formatCount(n.chars)} sub="read by the agent" />
            {/* the picked benchmark run, in the same row (operator 2026-11-05): the two arms and the verdict */}
            {verdict && (
              <>
                <StatCell label="rule on" value={`${verdict.with.passes}/${verdict.with.valid}`}
                  valueClassName={ruleOnTone(verdict.with, verdict.without)}
                  sub={`complied with the rule on; the rule reached the model in ${verdict.fired} of ${verdict.runs} runs`} />
                <StatCell label="rule off" value={`${verdict.without.passes}/${verdict.without.valid}`}
                  valueClassName={ratioTone(verdict.without.passes, verdict.without.valid)} sub="complied with the rule off" />
                <StatCell label="verdict" value={verdict.classification} valueClassName={verdictTone(verdict.classification)}
                  sub={`majority vote over ${verdict.runs} runs per arm · judge ${verdict.judge.model}, runner ${verdict.respondent.model}`} />
              </>
            )}
          </div>
        </>
      ) : (
        <p className="text-xs text-fg-faint">numbers on their way…</p>
      )}
      {verdicts.length > 0 && verdict && (
        <>
          <RunsStrip verdicts={verdicts} picked={picked} onPick={setPicked} />
          <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle">{verdict.scenario}</pre>
          {verdict.tension && <p className="text-xs text-fg-faint">tension: {verdict.tension}</p>}
        </>
      )}
      {/* the rule's actions: its benchmark (the rule on against the rule off, through the harness; not for
          synapptic's own rules), and what a dashboard built on this one opens under it */}
      {(details || !rule.core) && (
        <div className="flex items-start justify-between gap-3">
          <span className="text-xs text-fg-faint">
            {bench.running ? 'benchmark running' : open ? `${detailsLabel.toLowerCase()} of ${rule.id}, newest first` : ''}
          </span>
          <span className="flex items-center gap-2 shrink-0">
            {!rule.core && (
              <Button variant="secondary" className={buttonClasses} onClick={() => setBenchOpen(!benchOpen)} aria-expanded={benchOpen}>
                {benchOpen ? 'Hide benchmark' : 'Benchmark'}
              </Button>
            )}
            {details && (
              <Button variant="secondary" className={buttonClasses} onClick={() => setOpen(!open)} aria-expanded={open}>
                {open ? `Hide ${detailsLabel.toLowerCase()}` : detailsLabel}
              </Button>
            )}
          </span>
        </div>
      )}
      {benchOpen && !rule.core && (
        <BenchmarkPanel run={bench} onStop={onBenchmarkStop}
          onRun={(choices) => { startBench(); onBenchmark(choices); blockRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }); }} />
      )}
      {details && open && details(rule)}
    </div>
  );
}

function ChangeRow({ change, onUndo, disabled }: { change: GuardChange; onUndo: () => void; disabled: boolean }) {
  const before = change.numbers
    ? ` · before: ${change.numbers.fires} shown, ${change.numbers.idle} idle, ${change.numbers.misses} missed` : '';
  return (
    <div className="bg-page flex items-start justify-between gap-3 px-4 py-4">
      <div className="min-w-0">
        <div className="text-[13px] text-fg-muted font-medium">
          {change.kind} · {change.rule_id} · {changeSummary(change.added, change.removed)}{change.undone ? ' (undone)' : ''}
        </div>
        <div className="text-xs text-fg-faint truncate">{change.id} · {formatWhen(change.ts)}{before}</div>
      </div>
      {!change.undone && change.kind !== 'undo' && (
        <Button variant="secondary" className={buttonClasses} disabled={disabled} onClick={onUndo}>Undo</Button>
      )}
    </div>
  );
}

function ProfileBlock({ profile }: { profile: ProfileView | undefined }) {
  if (!profile) return <p className="bg-page p-4 text-sm text-fg-subtle gutter">Loading…</p>;
  if (!profile.archetype) {
    return <p className="bg-page p-4 text-sm text-fg-subtle gutter">No twin yet for this project. `synapptic ingest` builds it from your sessions.</p>;
  }
  return (
    <div className="bg-page flex flex-col gap-3 px-4 py-4">
      <div className="text-xs text-fg-faint">
        profile v{profile.version} · {profile.dimensions.map((d) => `${d.name} ${d.count}`).join(' · ')}
      </div>
      <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle">{profile.archetype}</pre>
    </div>
  );
}

export function GuardsPage<R extends GuardRow = GuardRow>({
  rulesMethod = 'guards.list', rulesTopic = 'guards', numbersMethod = 'guards.numbers', extras, details, detailsLabel = 'Events',
}: GuardsPageProps<R>) {
  const [params, setParams] = useSearchParams();
  const project = params.get('project') ?? GLOBAL;
  // the list first (instant), the numbers after it (the window's transcripts): the page never waits for them
  const guards = useWsQuery<Omit<GuardsList, 'rules'> & { rules: R[] }>(rulesMethod, {}, rulesTopic);
  const numbers = useWsQuery<GuardNumbersList>(numbersMethod);
  const profile = useWsQuery<ProfileView>('profile.get', { project });
  const changes = useWsQuery<GuardChange[]>(CHANGES_QUERY);
  const queryClient = useQueryClient();
  // stable: each rule block's benchmark subscription takes it as its "done" callback
  const refresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: [CHANGES_QUERY] });
    queryClient.invalidateQueries({ queryKey: [rulesMethod] });
    queryClient.invalidateQueries({ queryKey: [numbersMethod] });
  }, [queryClient, rulesMethod, numbersMethod]);
  const switchRule = useWsMutation<GuardsList, { id: string; off: boolean }>('guards.switch', { onSuccess: refresh });
  const undo = useWsMutation<Record<string, unknown>, { id: string }>('guards.undo', { onSuccess: refresh });
  // the job runs on the server; its steps arrive on the benchmark topic and the list is pushed when it ends
  const benchmark = useWsMutation<Record<string, unknown>, BenchmarkChoices & { id: string }>('guards.benchmark');
  const stopBenchmark = useWsMutation<Record<string, unknown>, { id: string }>('guards.benchmark.stop');

  if (guards.error) {
    return (
      <PageHeader title="Profile & Guards">
        <span className="text-[13px] text-fg-subtle">Could not load the guards: {guards.error.message}</span>
      </PageHeader>
    );
  }
  if (!guards.data) {
    return <PageHeader title="Profile & Guards"><span className="text-[13px] text-fg-subtle">Loading…</span></PageHeader>;
  }

  const list = guards.data;
  const counted = numbers.data;
  const days = counted?.days ?? DEFAULT_WINDOW_DAYS;
  const shown = list.rules
    .filter((rule) => appliesTo(rule, project))
    .map((rule) => ({ ...rule, numbers: counted?.numbers[rule.id] ?? (counted ? EMPTY_NUMBERS : undefined) }));
  const busy = switchRule.isPending || undo.isPending;
  const failure = switchRule.error ?? undo.error ?? changes.error ?? profile.error ?? numbers.error;
  const card = list.projects.find((p) => p.project === project);
  const label = card?.label ?? project;
  return (
    <section className="flex flex-col">
      <PageHeader title="Profile & Guards">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          {list.rules.length} rule{list.rules.length === 1 ? '' : 's'}, last {days} days:{' '}
          <span className="text-fg-muted">{counted ? `${counted.events} shown across ${counted.transcripts} sessions` : 'counting…'}</span>
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

      {project !== ALL && card?.twin !== false && (
        <>
          <PageHeader title={project === GLOBAL ? 'Your twin' : `Your twin in ${label}`}>
            <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">how you work, built from your sessions</span>
          </PageHeader>
          <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
            <ProfileBlock profile={profile.data} />
          </div>
        </>
      )}

      <PageHeader title={project === ALL ? 'All guards' : project === GLOBAL ? 'Guards everywhere' : `Guards in ${label}`}>
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          {shown.length} rule{shown.length === 1 ? '' : 's'}
        </span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        {shown.length === 0 && (
          <p className="bg-page p-4 text-sm text-fg-subtle gutter">No rules here yet. `synapptic guards add` writes one; `synapptic ingest` learns them.</p>
        )}
        {shown.map((rule) => (
          <RuleBlock key={rule.id} rule={rule} days={days} disabled={busy}
            extras={extras} details={details} detailsLabel={detailsLabel}
            onSwitch={(off) => switchRule.mutate({ id: rule.id, off })}
            onBenchmark={(choices) => benchmark.mutate({ id: rule.id, ...choices })}
            onBenchmarkStop={() => stopBenchmark.mutate({ id: rule.id })} onBenchmarkDone={refresh} />
        ))}
      </div>

      <PageHeader title="Changes">
        <span className="text-[13px] text-fg-subtle truncate hidden sm:inline">
          to the rules' words, newest first; an undo puts the words back
        </span>
      </PageHeader>
      <div className="grid grid-cols-1 gap-px bg-line border-b border-line">
        {changes.data?.length === 0 && <p className="bg-page p-4 text-sm text-fg-subtle gutter">None yet.</p>}
        {(changes.data ?? []).map((change) => (
          <ChangeRow key={change.id} change={change} disabled={busy} onUndo={() => undo.mutate({ id: change.id })} />
        ))}
      </div>
      {failure && <p className="p-4 text-sm text-fg-muted gutter">Not done: {failure.message}</p>}
    </section>
  );
}
