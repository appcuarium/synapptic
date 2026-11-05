// A pass ratio's colour (operator 2026-11-05): 4/5 and 3/3 teal, 3/5 and 2/5 and 2/3 amber, 1/5 and 1/3
// and 0 rose. The bands: three quarters and up teal, two fifths and up amber, under that rose.
const TEAL_FROM = 0.75;
const AMBER_FROM = 0.4;

export type Band = 'teal' | 'amber' | 'rose' | 'none';

export const TEAL = 'text-primary';
export const AMBER = 'text-amber-600 dark:text-amber-400';
export const ROSE = 'text-rose-600 dark:text-rose-400';
const NONE = 'text-fg-subtle';
const TEXT: Record<Band, string> = { teal: TEAL, amber: AMBER, rose: ROSE, none: NONE };

export function ratioBand(passes: number, valid: number): Band {
  if (valid === 0) return 'none';
  const ratio = passes / valid;
  if (ratio >= TEAL_FROM) return 'teal';
  if (ratio >= AMBER_FROM) return 'amber';
  return 'rose';
}

// The "rule on" band: rose whenever the rule off did better (the rule made the model worse), else its ratio's band.
export function ruleOnBand(on: { passes: number; valid: number }, off: { passes: number; valid: number }): Band {
  if (on.valid > 0 && off.valid > 0 && off.passes / off.valid > on.passes / on.valid) return 'rose';
  return ratioBand(on.passes, on.valid);
}

export function ratioTone(passes: number, valid: number): string {
  return TEXT[ratioBand(passes, valid)];
}

export function ruleOnTone(on: { passes: number; valid: number }, off: { passes: number; valid: number }): string {
  return TEXT[ruleOnBand(on, off)];
}

// A verdict word's own colour (operator 2026-11-05): effective teal, redundant amber, ineffective and
// backfire rose, stopped (and untestable) a literal slate in both modes, Machine's off-state slate-500
// (the page's subtle text is teal in light mode and the toggle's own colour in dark: "stopped is not slate").
export const SLATE = 'text-slate-500';
const VERDICT_BANDS: Record<string, Band> = { effective: 'teal', redundant: 'amber', ineffective: 'rose', backfire: 'rose' };

export function verdictTone(classification: string): string {
  const band = VERDICT_BANDS[classification];
  return band ? TEXT[band] : SLATE;
}
