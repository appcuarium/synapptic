// An item's description, shown as a tooltip over the item instead of a line under it (operator, 2026-11-04:
// every description is a shadcn tooltip). The tooltip takes the Select dropdown's colours in both themes.
import type { ReactNode } from 'react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

export function Described({ hint, side = 'top', children }: {
  hint: string;
  side?: 'top' | 'bottom' | 'left' | 'right';
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side}>{hint}</TooltipContent>
    </Tooltip>
  );
}
