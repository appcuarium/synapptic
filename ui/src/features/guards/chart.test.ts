import { describe, expect, it } from 'vitest';
import { dailySeries, dayLabel } from './chart';

const TODAY = new Date('2026-11-04T15:00:00Z');

describe('dailySeries', () => {
  it('fills the window with zeros around the days that have numbers, oldest first', () => {
    const series = dailySeries([{ day: '2026-11-04', fires: 3, idle: 1, misses: 0 }, { day: '2026-11-02', fires: 1, idle: 0, misses: 1 }], 4, TODAY);
    expect(series).toEqual([
      { day: '2026-11-01', fires: 0, idle: 0, misses: 0 },
      { day: '2026-11-02', fires: 1, idle: 0, misses: 1 },
      { day: '2026-11-03', fires: 0, idle: 0, misses: 0 },
      { day: '2026-11-04', fires: 3, idle: 1, misses: 0 },
    ]);
  });
  it('leaves out days before the window', () => {
    expect(dailySeries([{ day: '2026-10-01', fires: 9, idle: 9, misses: 9 }], 2, TODAY).map((d) => d.fires)).toEqual([0, 0]);
  });
});

describe('dayLabel', () => {
  it('shows the day and the short month in UTC', () => {
    expect(dayLabel('2026-11-04')).toMatch(/^4 /);
    expect(dayLabel('not a day')).toBe('not a day');
  });
});
