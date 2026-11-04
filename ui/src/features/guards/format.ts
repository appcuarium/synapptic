// Formatting for the guards page.

/** "4 Nov 17:21" in the viewer's locale, from seconds since the epoch. */
export function formatWhen(seconds: number): string {
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${
    date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
}

/** "+1 word", "-2 words", "+1, -1 words": what a change did to a rule, without the words themselves. */
export function changeSummary(added: number, removed: number): string {
  const parts = [];
  if (added) parts.push(`+${added}`);
  if (removed) parts.push(`-${removed}`);
  if (parts.length === 0) return 'no words changed';
  const total = added + removed;
  return `${parts.join(', ')} word${total === 1 ? '' : 's'}`;
}
