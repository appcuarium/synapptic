// A rule's benchmark, opened under the rule by its Benchmark button: the CLI's choices (the runner's provider
// and model, the judge's provider and model, how many runs), a Run button,
// then the run as it happens in time order: the scenario, each headless reply with whether the rule reached
// the model, each judge verdict with its reason, the end. Built on the Settings fields (Label, Select,
// fieldClasses, labelClasses), the guards page's buttons and the author timeline's step rows.
import { useEffect, useRef, useState } from 'react';
import { fieldClasses, labelClasses } from '@/components/machine/field';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { DashboardConfig, ProviderInfo } from '@/features/settings/types';
import { useWsQuery } from '@/lib/ws/react';
import type { BenchmarkChoices, BenchmarkEvent } from './types';
import type { BenchmarkRun } from './useBenchmark';

// The Settings page's button corners (template tokens for the rest).
const buttonClasses = 'rounded-xs shadow-none';
// The runner by default: a headless Claude Code session, the one runner the user's hooks run in. Any other
// provider (Ollama included) gets the rule's text from the benchmark itself (operator 2026-11-05).
const DEFAULT_RUNNER_PROVIDER = 'claude-cli';
const RUN_COUNTS = [1, 3, 5, 7, 9];
const DEFAULT_RUNS = 3;
// the streaming box: half the viewport, opened when the run starts
const LOG_HEIGHT = '50vh';

const STEP_LABELS: Record<NonNullable<BenchmarkEvent['kind']>, string> = {
  note: 'step', scenario: 'scenario', reply: 'the model said', judge: 'the judge said',
};

function armName(event: BenchmarkEvent): string {
  return event.arm === 'with' ? 'rule on' : event.arm === 'without' ? 'rule off' : '';
}

// A reply streams collapsed (operator 2026-11-05): its first line and a show/hide, the author rows' control.
const COLLAPSED_CHARS = 160;

function Collapsed({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const short = text.length > COLLAPSED_CHARS;
  return (
    <div className="flex flex-col gap-1">
      <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle bg-page p-2 rounded-sm">
        {open || !short ? text : `${text.slice(0, COLLAPSED_CHARS).trimEnd()}…`}
      </pre>
      {short && (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="self-start text-xs text-fg-faint cursor-pointer">
          {open ? 'hide' : 'show'}
        </button>
      )}
    </div>
  );
}

// One event of the run, in order: the kind, the run and arm, the content.
function EventRow({ event }: { event: BenchmarkEvent }) {
  const kind = event.kind ?? 'note';
  const head = [STEP_LABELS[kind], event.run ? `run ${event.run}` : '', armName(event),
    kind === 'reply' ? (event.fired ? 'the rule reached the model' : 'the rule did not reach the model') : ''].filter(Boolean).join(' · ');
  // the judge's line in bold, its verdict in teal (complied) or rose (did not) (operator, 2026-11-05)
  const verdictTone = event.score === 'PASS' ? 'text-primary' : event.score === 'FAIL' ? 'text-rose-600 dark:text-rose-400' : 'text-fg-subtle';
  return (
    <div className="flex flex-col gap-1">
      <span className={`text-[10px] text-fg-subtle uppercase tracking-wide${kind === 'judge' ? ' font-bold' : ''}`}>
        {head}{kind === 'judge' && event.score ? <> · <span className={verdictTone}>{event.score}</span></> : null}
      </span>
      {kind === 'note' && <span className="text-xs text-fg-faint">{event.message}</span>}
      {kind === 'scenario' && (
        <>
          <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle bg-page p-2 rounded-sm">{event.scenario}</pre>
          {event.tension && <span className="text-xs text-fg-faint">tension: {event.tension}</span>}
        </>
      )}
      {kind === 'reply' && <Collapsed text={event.text ?? ''} />}
      {kind === 'judge' && <pre className="whitespace-pre-wrap font-sans text-[13px] text-fg-subtle">{event.reason}</pre>}
    </div>
  );
}

export function BenchmarkPanel({ run, onRun, onStop }: {
  run: BenchmarkRun; onRun: (choices: BenchmarkChoices) => void; onStop: () => void;
}) {
  const config = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  const providers = useWsQuery<ProviderInfo[]>('providers.list');
  const [runnerProvider, setRunnerProvider] = useState(DEFAULT_RUNNER_PROVIDER);
  const [runnerModel, setRunnerModel] = useState('');
  const [judgeProvider, setJudgeProvider] = useState('');
  const [judgeModel, setJudgeModel] = useState('');
  const [runs, setRuns] = useState(DEFAULT_RUNS);
  const logRef = useRef<HTMLDivElement>(null);
  // the box follows the newest event, after the event has laid out
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const box = logRef.current;
      if (box) box.scrollTop = box.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [run.events.length]);

  const providerList = providers.data ?? [];
  // the runner's models follow its provider, as the judge's do: the configured model when the provider is
  // the configured one, else the provider's default
  const runner = providerList.find((p) => p.id === runnerProvider);
  const runnerModelId = runnerModel || (runnerProvider === config.data?.provider ? config.data?.model : runner?.default_model) || '';
  const runnerModels = runner?.models.some((m) => m.id === runnerModelId)
    ? runner.models
    : [...(runnerModelId ? [{ id: runnerModelId, label: runnerModelId }] : []), ...(runner?.models ?? [])];
  const judgeProviderId = judgeProvider || config.data?.provider || '';
  const judge = providerList.find((p) => p.id === judgeProviderId);
  const judgeModelId = judgeModel || (judgeProviderId === config.data?.provider ? config.data?.model : judge?.default_model) || '';
  const models = judge?.models.some((m) => m.id === judgeModelId)
    ? judge.models
    : [...(judgeModelId ? [{ id: judgeModelId, label: judgeModelId }] : []), ...(judge?.models ?? [])];

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3 text-sm">
        <div className="flex flex-col gap-1">
          <Label htmlFor="bench-runner-provider" className={labelClasses}>Runner provider</Label>
          <Select value={runnerProvider} onValueChange={(id) => { setRunnerProvider(id); setRunnerModel(''); }}>
            <SelectTrigger id="bench-runner-provider" className={fieldClasses}><SelectValue /></SelectTrigger>
            <SelectContent className="text-muted-foreground">
              {providerList.map((p) => <SelectItem key={p.id} value={p.id} className="focus:text-muted-foreground">{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="bench-runner" className={labelClasses}>Runner model</Label>
          <Select value={runnerModelId} onValueChange={setRunnerModel}>
            <SelectTrigger id="bench-runner" className={fieldClasses}><SelectValue /></SelectTrigger>
            <SelectContent className="text-muted-foreground">
              {runnerModels.map((m) => <SelectItem key={m.id} value={m.id} className="focus:text-muted-foreground">{m.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="bench-judge-provider" className={labelClasses}>Judge provider</Label>
          <Select value={judgeProviderId} onValueChange={(id) => { setJudgeProvider(id); setJudgeModel(''); }}>
            <SelectTrigger id="bench-judge-provider" className={fieldClasses}><SelectValue /></SelectTrigger>
            <SelectContent className="text-muted-foreground">
              {providerList.map((p) => <SelectItem key={p.id} value={p.id} className="focus:text-muted-foreground">{p.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="bench-judge-model" className={labelClasses}>Judge model</Label>
          <Select value={judgeModelId} onValueChange={setJudgeModel}>
            <SelectTrigger id="bench-judge-model" className={fieldClasses}><SelectValue /></SelectTrigger>
            <SelectContent className="text-muted-foreground">
              {models.map((m) => <SelectItem key={m.id} value={m.id} className="focus:text-muted-foreground">{m.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="bench-runs" className={labelClasses}>Runs per arm</Label>
          <Select value={String(runs)} onValueChange={(n) => setRuns(Number(n))}>
            <SelectTrigger id="bench-runs" className={fieldClasses}><SelectValue /></SelectTrigger>
            <SelectContent className="text-muted-foreground">
              {RUN_COUNTS.map((n) => <SelectItem key={n} value={String(n)} className="focus:text-muted-foreground">{n}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs text-fg-faint">
          {runnerProvider === DEFAULT_RUNNER_PROVIDER
            ? 'the rule on against the rule off, in fresh headless sessions with your hooks running; the rule is off for your live sessions while an off answer is awaited'
            : 'the rule on against the rule off; this runner has no hooks, so the rule’s text goes ahead of the scenario on the on arm and your live sessions are untouched'}
        </span>
        <span className="flex items-center gap-2 shrink-0">
          {run.running && (
            <Button variant="secondary" className={buttonClasses} onClick={onStop}>Stop</Button>
          )}
          <Button variant="secondary" className={buttonClasses} disabled={run.running || !judgeProviderId || !runnerModelId}
            onClick={() => onRun({ runs, runner_provider: runnerProvider, runner_model: runnerModelId, judge_provider: judgeProviderId, judge_model: judgeModelId })}>
            {run.running ? 'Running…' : 'Run'}
          </Button>
        </span>
      </div>
      {/* the run streams inside a fixed box that opens to half the viewport when the run starts and scrolls
          itself to the newest event (operator 2026-11-05: not a window that keeps growing) */}
      <div ref={logRef} className="overflow-y-auto transition-[height] duration-300 ease-out"
        style={{ height: run.running || run.events.length > 0 ? LOG_HEIGHT : 0 }}>
        {/* the stream starts at the top and the box scrolls once it fills (operator, 2026-11-05) */}
        <div className="flex flex-col gap-3">
          {run.events.map((event, i) => <EventRow key={i} event={event} />)}
        </div>
      </div>
      {run.verdict && (
        <span className="text-xs text-fg-faint">
          done: {run.verdict.classification} · rule on {run.verdict.with.passes}/{run.verdict.with.valid}, rule off {run.verdict.without.passes}/{run.verdict.without.valid}, majority vote over {run.verdict.runs} runs per arm
        </span>
      )}
      {run.error && <p className="text-sm text-fg-muted">Not done: {run.error}</p>}
    </div>
  );
}
