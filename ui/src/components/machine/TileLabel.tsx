// A name as a label (operator, 2026-11-04): shadcn's Label in the on/off pill's shape (h-6, px-2, 11px, 2px
// corners), the strip's darker tile colour behind it (LABEL_BACKGROUND, the same in both themes) and the
// tile's text colour on it. Tooltips take the same colours (components/ui/tooltip.tsx).
import type { ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/cn';
import { LABEL_BACKGROUND } from '@/lib/tileColor';

export const tileLabelClasses = 'text-[11px] font-normal text-tile-fg';
export const tileLabelStyle = { backgroundColor: LABEL_BACKGROUND };

// `className` sizes the chip to the text beside it (a step header's 10px: h-5 text-[10px]); the colours stay.
export function TileLabel({ id, className, children }: { id?: string; className?: string; children: ReactNode }) {
  return (
    // the name is text: the text cursor over it and selectable (shadcn's Label is select-none by default, and
    // Radix's label cancels a double click's default so a word is never selected: the span keeps that mousedown
    // from reaching it, so a double click selects the name and it can be copied)
    <Label id={id} className={cn('inline-flex h-6 min-w-0 px-2 rounded-sm truncate cursor-text select-text', tileLabelClasses, className)}
      style={tileLabelStyle}>
      <span className="truncate" onMouseDown={(event) => event.stopPropagation()}>{children}</span>
    </Label>
  );
}
