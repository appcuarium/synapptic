// The one look for text fields and selects on every page (agreed on Settings): the control background,
// 2px corners, and the text in the placeholder's colour, typed or not.
export const fieldClasses =
  'h-9 touch:h-11 w-full rounded-xs border-0 bg-control dark:bg-control shadow-none px-2 text-sm text-muted-foreground ' +
  'focus-visible:ring-0';
// The label over a field: the theme's primary (teal) token, which the two themes set for their own surfaces
// (teal-700 on light, teal-400 on dark).
export const labelClasses = 'text-sm leading-5 font-normal text-primary';
