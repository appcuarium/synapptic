// A rule's benchmark as it runs: every event the server pushes on the benchmark topic for the rule (the
// stages, the scenario, each headless reply, each judge verdict), until the end comes with a verdict (the
// rules list is pushed again with it) or an error. On mount the server's state seeds it, so a page opened or
// reloaded mid-run shows the run so far and keeps following it (operator 2026-11-05).
import { useEffect, useState } from 'react';
import { useWs } from '@/lib/ws/react';
import type { BenchmarkEvent, RuleVerdict, ScopeRun } from './types';

export const BENCHMARK_TOPIC = 'benchmark';
// the Benchmark page's job on the server: its events come under each rule's id, its end under this one
export const SCOPE_JOB = '__scope__';

export interface BenchmarkRun {
  running: boolean;
  events: BenchmarkEvent[];   // in the order they came
  verdict: RuleVerdict | null;
  error: string;
}

interface BenchmarkState {
  rule: string;
  running: boolean;
  events: BenchmarkEvent[];
}

const IDLE: BenchmarkRun = { running: false, events: [], verdict: null, error: '' };

export function useBenchmarkRun(ruleId: string, onDone?: () => void): [BenchmarkRun, () => void] {
  const client = useWs();
  const [run, setRun] = useState<BenchmarkRun>(IDLE);
  useEffect(() => {
    // subscribe first, then ask for the state: an event arriving between the two is merged below
    const unsubscribe = client.subscribe(BENCHMARK_TOPIC, (data) => {
      const event = data as BenchmarkEvent;
      if (event.rule !== ruleId) return;
      if (event.error) {
        setRun((before) => ({ ...before, running: false, error: event.error ?? '' }));
      } else if (event.done) {
        setRun((before) => ({ ...before, running: false, verdict: event.verdict ?? null }));
        onDone?.();
      } else {
        setRun((before) => ({ ...before, running: true, events: [...before.events, event] }));
      }
    });
    let alive = true;
    client.request<BenchmarkState>('guards.benchmark.state', { id: ruleId }).then((state) => {
      if (!alive || state.events.length === 0) return;
      setRun((before) => ({
        ...before,
        running: state.running || before.running,
        // the server's list is complete up to its reply; the topic's own list wins only if it got longer since
        events: state.events.length >= before.events.length ? state.events : before.events,
      }));
    }).catch(() => undefined);
    return () => { alive = false; unsubscribe(); };
  }, [client, ruleId, onDone]);
  const start = () => setRun({ running: true, events: [], verdict: null, error: '' });
  return [run, start];
}

// The Benchmark page's run over a scope: every event of every rule while it runs, each rule's end, and the
// end with the whole run; seeded from the server's state like a rule's run.
export interface ScopeBenchmarkRun {
  running: boolean;
  events: BenchmarkEvent[];
  verdicts: RuleVerdict[];   // the rules done so far, in order
  run: ScopeRun | null;
  error: string;
}

const SCOPE_IDLE: ScopeBenchmarkRun = { running: false, events: [], verdicts: [], run: null, error: '' };

export function useScopeBenchmarkRun(onDone?: () => void): [ScopeBenchmarkRun, () => void] {
  const client = useWs();
  const [run, setRun] = useState<ScopeBenchmarkRun>(SCOPE_IDLE);
  useEffect(() => {
    const unsubscribe = client.subscribe(BENCHMARK_TOPIC, (data) => {
      const event = data as BenchmarkEvent & { run?: ScopeRun };
      if (event.rule === SCOPE_JOB) {
        if (event.error) {
          setRun((before) => ({ ...before, running: false, error: event.error ?? '' }));
        } else if (event.done) {
          setRun((before) => ({ ...before, running: false, run: event.run ?? null }));
          onDone?.();
        } else {
          // the run's own notes: the scenarios kept or generated, the controls
          setRun((before) => (before.running ? { ...before, events: [...before.events, event] } : before));
        }
        return;
      }
      // a rule's event belongs to this run only while it runs (a rule's own button runs on the same topic)
      setRun((before) => {
        if (!before.running) return before;
        if (event.done) return { ...before, verdicts: event.verdict ? [...before.verdicts, event.verdict] : before.verdicts };
        if (event.error) return before;
        return { ...before, events: [...before.events, event] };
      });
    });
    let alive = true;
    client.request<BenchmarkState>('guards.benchmark.state', { id: SCOPE_JOB }).then((state) => {
      if (!alive || !state.running) return;
      setRun((before) => ({
        ...before,
        running: true,
        events: state.events.length >= before.events.length ? state.events.filter((e) => !e.done) : before.events,
        verdicts: state.events.filter((e) => e.done && e.verdict).map((e) => e.verdict as RuleVerdict),
      }));
    }).catch(() => undefined);
    return () => { alive = false; unsubscribe(); };
  }, [client, onDone]);
  const start = () => setRun({ running: true, events: [], verdicts: [], run: null, error: '' });
  return [run, start];
}
