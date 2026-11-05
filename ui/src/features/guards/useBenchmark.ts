// A rule's benchmark as it runs: every event the server pushes on the benchmark topic for the rule (the
// stages, the scenario, each headless reply, each judge verdict), until the end comes with a verdict (the
// rules list is pushed again with it) or an error. On mount the server's state seeds it, so a page opened or
// reloaded mid-run shows the run so far and keeps following it (operator 2026-11-05).
import { useEffect, useState } from 'react';
import { useWs } from '@/lib/ws/react';
import type { BenchmarkEvent, RuleVerdict } from './types';

export const BENCHMARK_TOPIC = 'benchmark';

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
