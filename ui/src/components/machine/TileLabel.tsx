// A name as a label (operator, 2026-11-04): shadcn's Label in the on/off pill's shape (h-6, px-2, 11px, 2px
// corners), the strip's darker tile colour behind it (LABEL_BACKGROUND, the same in both themes) and the
// text in the 100 shade of the label's own hue (operator 2026-11-05: Machine's chip pair bg-teal-900 text-teal-300
// first, then "lighter shades", "lighter text for the colored background", "even lighter": 400, 300, 200, 100).
// Tooltips take the same colours (components/ui/tooltip.tsx).
import type { CSSProperties, ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/cn';
import { LABEL_BACKGROUND } from '@/lib/tileColor';

export const tileLabelClasses = 'text-[11px] font-normal text-teal-100';
export const tileLabelStyle = { backgroundColor: LABEL_BACKGROUND };

// A label's tone (operator 2026-11-05, the guards page): the default teal fill, an amber or rose fill at the
// same darkness with that hue's text for a rule whose benchmark says so, or the outline alone (the label
// colour as the border and as the text, no fill; operator 2026-11-05) for a rule with no benchmark.
export type TileLabelTone = 'amber' | 'rose' | 'outline';
export const TONE_CLASSES: Record<TileLabelTone, string> = { amber: 'bg-amber-800 text-amber-100', rose: 'bg-rose-800 text-rose-100', outline: 'border bg-transparent' };
const OUTLINE_STYLE: CSSProperties = { borderColor: LABEL_BACKGROUND, color: LABEL_BACKGROUND };

// The chip's height and text size are the benchmark runs toggles' (h-8, 13px; operator 2026-11-05); tooltips
// keep the 11px of tileLabelClasses. `className` sizes the chip to the text beside it (a step header's 10px:
// h-5 text-[10px]); the colours stay.
export function TileLabel({ id, className, tone, children }: { id?: string; className?: string; tone?: TileLabelTone; children: ReactNode }) {
  return (
    // the name is text: the text cursor over it and selectable (shadcn's Label is select-none by default, and
    // Radix's label cancels a double click's default so a word is never selected: the span keeps that mousedown
    // from reaching it, so a double click selects the name and it can be copied)
    <Label id={id} className={cn('inline-flex h-8 min-w-0 px-2 rounded-sm truncate cursor-text select-text', tileLabelClasses, 'text-[13px]', tone && TONE_CLASSES[tone], className)}
      style={tone === undefined ? tileLabelStyle : tone === 'outline' ? OUTLINE_STYLE : undefined}>
      <span className="truncate" onMouseDown={(event) => event.stopPropagation()}>{children}</span>
    </Label>
  );
}
