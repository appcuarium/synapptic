// The daily series of a rule over the window, one entry per calendar day with zeros where nothing happened,
// so every rule's chart has the same bars in the same places.
import type { DayNumbers } from './types';

const DAY_MS = 86_400_000;

function dayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The last `days` UTC days ending today, each with the rule's numbers or zeros. */
export function dailySeries(daily: DayNumbers[], days: number, today: Date = new Date()): DayNumbers[] {
  const known = new Map(daily.map((d) => [d.day, d]));
  const end = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const series: DayNumbers[] = [];
  for (let i = days - 1; i >= 0; i -= 1) {
    const day = dayKey(new Date(end - i * DAY_MS));
    series.push(known.get(day) ?? { day, fires: 0, idle: 0, misses: 0 });
  }
  return series;
}

/** "4 Nov" for the axis, in UTC like the day keys. */
export function dayLabel(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return day;
  return `${date.getUTCDate()} ${date.toLocaleDateString(undefined, { month: 'short', timeZone: 'UTC' })}`;
}
