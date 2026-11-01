// Machine's getTileBackground (tileStripShell.component.js): hue from the value (>= 0 teal, < 0 rose),
// strength from the position. The strength range and backdrop come from the theme tokens
// (--tile-alpha-min / --tile-alpha-span / --tile-backdrop): translucent over a dark page as in Machine,
// opaque over teal-950 on a light page.
const TEAL = [20, 184, 166];
const ROSE = [244, 63, 94];

function themeNumber(name: string, fallback: number): number {
  const value = parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name));
  return Number.isFinite(value) ? value : fallback;
}

function themeBackdrop(): number[] | null {
  const value = getComputedStyle(document.documentElement).getPropertyValue('--tile-backdrop').trim();
  return value && value !== 'none' ? value.split(/\s+/).map(Number) : null;
}

/** position: 0 = weakest tile in the strip, 1 = strongest. */
export function tileBackground(value: number, position: number): string {
  const alpha = themeNumber('--tile-alpha-min', 0.08) + themeNumber('--tile-alpha-span', 0.72) * position;
  const base = value >= 0 ? TEAL : ROSE;
  const backdrop = themeBackdrop();
  if (!backdrop) return `rgba(${base.join(', ')}, ${alpha})`;
  const mixed = base.map((channel, i) => Math.round(channel * alpha + (backdrop[i] ?? 0) * (1 - alpha)));
  return `rgb(${mixed.join(', ')})`;
}

/** Position of a size between the strip's smallest and largest, on a log scale (longer = stronger). */
export function logPosition(size: number, min: number, max: number): number {
  const low = Math.log(Math.max(1, min));
  const high = Math.log(Math.max(1, max));
  if (high <= low) return 1;
  return (Math.log(Math.max(1, size)) - low) / (high - low);
}
