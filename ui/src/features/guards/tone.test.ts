import { describe, expect, it } from 'vitest';
import { AMBER, ROSE, SLATE, TEAL, ratioBand, ratioTone, ruleOnBand, ruleOnTone, verdictTone } from './tone';

// The verdict word's own colour (operator 2026-11-05): "redundant should be amber, stopped should be slate".
describe('verdictTone', () => {
  it('colours a verdict by what it says, not by the rule-on ratio', () => {
    expect(verdictTone('effective')).toBe(TEAL);
    expect(verdictTone('redundant')).toBe(AMBER);
    expect(verdictTone('ineffective')).toBe(ROSE);
    expect(verdictTone('backfire')).toBe(ROSE);
  });
  it('colours a stopped or untestable run a literal slate in both modes, never the page text (operator: "stopped is not slate")', () => {
    expect(verdictTone('stopped')).toBe(SLATE);
    expect(verdictTone('untestable')).toBe(SLATE);
    expect(SLATE).not.toBe('text-fg-subtle');
  });
});

// The rule's label wears the band of its latest run's rule-on cell (operator 2026-11-05); no run, no band.
describe('ruleOnBand', () => {
  it('names the band the rule-on cell is coloured in', () => {
    expect(ruleOnBand({ passes: 4, valid: 5 }, { passes: 2, valid: 5 })).toBe('teal');
    expect(ruleOnBand({ passes: 3, valid: 5 }, { passes: 3, valid: 5 })).toBe('amber');
    expect(ruleOnBand({ passes: 1, valid: 5 }, { passes: 0, valid: 5 })).toBe('rose');
    expect(ruleOnBand({ passes: 3, valid: 5 }, { passes: 4, valid: 5 })).toBe('rose');
  });
  it('has no band for a run that measured nothing', () => {
    expect(ruleOnBand({ passes: 0, valid: 0 }, { passes: 0, valid: 0 })).toBe('none');
    expect(ratioBand(0, 0)).toBe('none');
  });
});

describe('ratioTone', () => {
  it('colours a ratio as the operator ruled: 4/5 teal, 3/5 and 2/5 amber, 1/5 and 0/5 rose', () => {
    expect(ratioTone(4, 5)).toBe(TEAL);
    expect(ratioTone(3, 5)).toBe(AMBER);
    expect(ratioTone(2, 5)).toBe(AMBER);
    expect(ratioTone(1, 5)).toBe(ROSE);
    expect(ratioTone(0, 5)).toBe(ROSE);
  });
  it('keeps the three-run reading: 3/3 teal, 2/3 amber, 1/3 rose', () => {
    expect(ratioTone(3, 3)).toBe(TEAL);
    expect(ratioTone(2, 3)).toBe(AMBER);
    expect(ratioTone(1, 3)).toBe(ROSE);
  });
  it('has no colour for a ratio with nothing valid', () => {
    expect(ratioTone(0, 0)).toBe('text-fg-subtle');
  });
});

describe('ruleOnTone', () => {
  it('is rose whenever the rule off did better than the rule on', () => {
    expect(ruleOnTone({ passes: 3, valid: 5 }, { passes: 4, valid: 5 })).toBe(ROSE);
    expect(ruleOnTone({ passes: 4, valid: 5 }, { passes: 5, valid: 5 })).toBe(ROSE);
  });
  it('is the ratio colour otherwise', () => {
    expect(ruleOnTone({ passes: 4, valid: 5 }, { passes: 2, valid: 5 })).toBe(TEAL);
    expect(ruleOnTone({ passes: 3, valid: 5 }, { passes: 3, valid: 5 })).toBe(AMBER);
    expect(ruleOnTone({ passes: 1, valid: 1 }, { passes: 0, valid: 0 })).toBe(TEAL);
  });
});
