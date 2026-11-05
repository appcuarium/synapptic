// A rule's benchmark, opened under the rule by its Benchmark button: the CLI's choices (the runner's provider
// and model, the judge's provider and model, how many runs), a Run button,
// then the run as it happens in time order: the scenario, each headless reply with whether the rule reached
// the model, each judge verdict with its reason, the end. Built on the Settings fields (Label, Select,
// fieldClasses, labelClasses), the guards page's buttons and the author timeline's step rows. The choice
// fields and the streaming box are their own pieces: the Benchmark page runs the same over a scope.
import { type ReactNode, useEffect, useRef, useState } from 'react';
import { NumberStepper, SettingRow, SettingSelect, settingsGridClasses } from '@/components/machine/SettingsForm';
import { Button } from '@/components/ui/button';
import type { DashboardConfig, ProviderInfo } from '@/features/settings/types';
import { cn } from '@/lib/cn';
import { useWsQuery } from '@/lib/ws/react';
import type { BenchmarkChoices, BenchmarkEvent } from './types';
import { SCOPE_JOB, type BenchmarkRun } from './useBenchmark';

// The Settings page's button corners (template tokens for the rest).
const buttonClasses = 'rounded-xs shadow-none';
// The runner by default: a headless Claude Code session, the one runner the user's hooks run in. Any other
// provider (Ollama included) gets the rule's text from the benchmark itself (operator 2026-11-05).
export const DEFAULT_RUNNER_PROVIDER = 'claude-cli';
// Any provider is a runner (operator 2026-11-06): Claude Code answers with the user's own hooks; every other
// one has none, so Synapptic runs its prompt hook around it and the rule reaches it the same way.
const OWN_HOOKS_NOTE = 'your hooks';
const WRAPPED_NOTE = 'through Synapptic’s hook';
const MIN_RUNS = 1;
const MAX_RUNS = 9;
const DEFAULT_RUNS = 3;

// The picks made here, kept in the browser the way the theme is (lib/theme.ts): '' for a provider or model
// never picked, so Settings' own apply until the user chooses otherwise.
const STORAGE_KEY = 'synapptic-benchmark';
interface StoredChoices { runner_provider: string; runner_model: string; judge_provider: string; judge_model: string; runs: number }
const NO_CHOICES: StoredChoices = { runner_provider: '', runner_model: '', judge_provider: '', judge_model: '', runs: DEFAULT_RUNS };

function storedChoices(): StoredChoices {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<StoredChoices> | null;
    if (!value || typeof value !== 'object') return NO_CHOICES;
    const runs = Number(value.runs);
    return {
      runner_provider: typeof value.runner_provider === 'string' ? value.runner_provider : '',
      runner_model: typeof value.runner_model === 'string' ? value.runner_model : '',
      judge_provider: typeof value.judge_provider === 'string' ? value.judge_provider : '',
      judge_model: typeof value.judge_model === 'string' ? value.judge_model : '',
      runs: Number.isInteger(runs) && runs >= MIN_RUNS && runs <= MAX_RUNS ? runs : DEFAULT_RUNS,
    };
  } catch {
    return NO_CHOICES;
  }
}

function rememberChoices(choices: StoredChoices): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choices));
  } catch {
    // storage blocked: the picks still hold for this page view
  }
}
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

export function Collapsed({ text }: { text: string }) {
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

// One event of the run, in order: the kind, the run and arm, the content. `named`: the rule's id ahead of
// the kind (the Benchmark page's box carries several rules).
function EventRow({ event, named }: { event: BenchmarkEvent; named?: boolean }) {
  const kind = event.kind ?? 'note';
  // the Benchmark page's own notes (the scenarios, the controls) come under the scope id: shown as "run"
  const head = [named ? (event.rule === SCOPE_JOB ? 'run' : event.rule) : '', STEP_LABELS[kind], event.run ? `run ${event.run}` : '', armName(event),
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

// The run streams inside a fixed box that opens to half the viewport when the run starts and scrolls itself
// to the newest event (operator 2026-11-05: not a window that keeps growing); the stream starts at the top.
// `bordered`: a line above the box while it is open (operator 2026-11-06, the Benchmark page: the stream apart
// from the settings rows), the page's own line, and the rows' padding under it.
export function BenchmarkLog({ events, open, named, bordered }: { events: BenchmarkEvent[]; open: boolean; named?: boolean; bordered?: boolean }) {
  const logRef = useRef<HTMLDivElement>(null);
  // the box follows the newest event, after the event has laid out
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const box = logRef.current;
      if (box) box.scrollTop = box.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [events.length]);
  return (
    <div ref={logRef} className={cn('overflow-y-auto transition-[height] duration-300 ease-out', bordered && open && 'border-t border-line pt-4')} style={{ height: open ? LOG_HEIGHT : 0 }}>
      <div className="flex flex-col gap-3">
        {events.map((event, i) => <EventRow key={i} event={event} named={named} />)}
      </div>
    </div>
  );
}

// The CLI's choices as the Settings fields: the runner's provider and model, the judge's, runs per arm; the
// models follow their provider (the configured model when the provider is the configured one, else the
// provider's default). `onChange` hears the resolved choices, null until the providers are known;
// `children` sit in the same grid (the Benchmark page adds how many rules).
export function BenchmarkChoiceFields({ onChange, children }: { onChange: (choices: BenchmarkChoices | null) => void; children?: ReactNode }) {
  const config = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  const providers = useWsQuery<ProviderInfo[]>('providers.list');
  // the runner and the judge keep what was picked here (the browser remembers it, as it does the theme);
  // what was never picked opens on the provider and model configured in Settings (operator 2026-11-06)
  const [runnerProvider, setRunnerProvider] = useState(() => storedChoices().runner_provider);
  const [runnerModel, setRunnerModel] = useState(() => storedChoices().runner_model);
  const [judgeProvider, setJudgeProvider] = useState(() => storedChoices().judge_provider);
  const [judgeModel, setJudgeModel] = useState(() => storedChoices().judge_model);
  const [runs, setRuns] = useState(() => storedChoices().runs);
  useEffect(() => {
    rememberChoices({ runner_provider: runnerProvider, runner_model: runnerModel, judge_provider: judgeProvider, judge_model: judgeModel, runs });
  }, [runnerProvider, runnerModel, judgeProvider, judgeModel, runs]);

  const providerList = providers.data ?? [];
  const runnerProviderId = runnerProvider || config.data?.provider || '';
  const runner = providerList.find((p) => p.id === runnerProviderId);
  const runnerModelId = runnerModel || (runnerProviderId === config.data?.provider ? config.data?.model : runner?.default_model) || '';
  const runnerModels = runner?.models.some((m) => m.id === runnerModelId)
    ? runner.models
    : [...(runnerModelId ? [{ id: runnerModelId, label: runnerModelId }] : []), ...(runner?.models ?? [])];
  const judgeProviderId = judgeProvider || config.data?.provider || '';
  const judge = providerList.find((p) => p.id === judgeProviderId);
  const judgeModelId = judgeModel || (judgeProviderId === config.data?.provider ? config.data?.model : judge?.default_model) || '';
  const models = judge?.models.some((m) => m.id === judgeModelId)
    ? judge.models
    : [...(judgeModelId ? [{ id: judgeModelId, label: judgeModelId }] : []), ...(judge?.models ?? [])];
  const ready = Boolean(judgeProviderId && runnerProviderId && runnerModelId);
  useEffect(() => {
    onChange(ready ? { runs, runner_provider: runnerProviderId, runner_model: runnerModelId, judge_provider: judgeProviderId, judge_model: judgeModelId } : null);
  }, [onChange, ready, runs, runnerProviderId, runnerModelId, judgeProviderId, judgeModelId]);

  // Machine's settings rows (operator 2026-11-06): the label and its line on the left, the control on the right
  return (
    <div className={settingsGridClasses}>
      <SettingRow id="bench-runner-provider-label" label="Runner provider" note="who answers the scenarios">
        <SettingSelect id="bench-runner-provider" labelledBy="bench-runner-provider-label" value={runnerProviderId} width="w-[220px]"
          onValueChange={(id) => { setRunnerProvider(id); setRunnerModel(''); }}
          options={providerList.map((p) => ({ id: p.id, label: `${p.name} · ${p.id === DEFAULT_RUNNER_PROVIDER ? OWN_HOOKS_NOTE : WRAPPED_NOTE}` }))} />
      </SettingRow>
      <SettingRow id="bench-runner-label" label="Runner model" note="the runner’s model">
        <SettingSelect id="bench-runner" labelledBy="bench-runner-label" value={runnerModelId} width="w-[220px]"
          onValueChange={setRunnerModel} options={runnerModels} />
      </SettingRow>
      <SettingRow id="bench-judge-provider-label" label="Judge provider" note="who scores the answers">
        <SettingSelect id="bench-judge-provider" labelledBy="bench-judge-provider-label" value={judgeProviderId} width="w-[220px]"
          onValueChange={(id) => { setJudgeProvider(id); setJudgeModel(''); }}
          options={providerList.map((p) => ({ id: p.id, label: p.name }))} />
      </SettingRow>
      <SettingRow id="bench-judge-model-label" label="Judge model" note="the judge’s model">
        <SettingSelect id="bench-judge-model" labelledBy="bench-judge-model-label" value={judgeModelId} width="w-[220px]"
          onValueChange={setJudgeModel} options={models} />
      </SettingRow>
      <SettingRow id="bench-runs-label" label="Runs per arm" note="answers per arm, the verdict a majority vote">
        <NumberStepper value={runs} onChange={setRuns} min={MIN_RUNS} max={MAX_RUNS} labelledBy="bench-runs-label" />
      </SettingRow>
      {children}
    </div>
  );
}

// What the run does, under the fields: with the hooks or with the rule's text put in front (operator 2026-11-05).
export function runnerNote(choices: BenchmarkChoices | null): string {
  return !choices || choices.runner_provider === DEFAULT_RUNNER_PROVIDER
    ? 'the rule on against the rule off, in fresh headless sessions with your hooks running; the rule is off for your live sessions while an off answer is awaited'
    : 'the rule on against the rule off; this runner has no hooks of its own, so Synapptic runs its prompt hook around it and the rule reaches it as it would in Claude Code; the rule is off for your live sessions while an off answer is awaited';
}

export function BenchmarkPanel({ run, onRun, onStop }: {
  run: BenchmarkRun; onRun: (choices: BenchmarkChoices) => void; onStop: () => void;
}) {
  const [choices, setChoices] = useState<BenchmarkChoices | null>(null);
  return (
    <div className="flex flex-col gap-3">
      <BenchmarkChoiceFields onChange={setChoices} />
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs text-fg-faint">{runnerNote(choices)}</span>
        <span className="flex items-center gap-2 shrink-0">
          {run.running && (
            <Button variant="secondary" className={buttonClasses} onClick={onStop}>Stop</Button>
          )}
          <Button className={buttonClasses} disabled={run.running || !choices}
            onClick={() => { if (choices) onRun(choices); }}>
            {run.running ? 'Running…' : 'Run'}
          </Button>
        </span>
      </div>
      <BenchmarkLog events={run.events} open={run.running || run.events.length > 0} />
      {run.verdict && (
        <span className="text-xs text-fg-faint">
          done: {run.verdict.classification} · rule on {run.verdict.with.passes}/{run.verdict.with.valid}, rule off {run.verdict.without.passes}/{run.verdict.without.valid}, majority vote over {run.verdict.runs} runs per arm
        </span>
      )}
      {run.error && <p className="text-sm text-fg-muted">Not done: {run.error}</p>}
    </div>
  );
}
