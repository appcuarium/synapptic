import { describe, expect, it } from 'vitest';
import { changeSummary, formatWhen } from './format';

describe('changeSummary', () => {
  it('counts what came and went without naming the words', () => {
    expect(changeSummary(1, 0)).toBe('+1 word');
    expect(changeSummary(0, 2)).toBe('-2 words');
    expect(changeSummary(1, 1)).toBe('+1, -1 words');
    expect(changeSummary(0, 0)).toBe('no words changed');
  });
});

describe('formatWhen', () => {
  it('is empty for a time that is not one', () => {
    expect(formatWhen(Number.NaN)).toBe('');
    expect(formatWhen(1793817378)).not.toBe('');
  });
});
