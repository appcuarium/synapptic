// Formatting for the session browser: counts as the mockup shows them, and the small piece of markdown the
// conversation uses (code fences, inline code, bold), split into parts that React renders as elements.
// Nothing here produces HTML: transcript text is never injected into the page.

export type FencedPart = { kind: 'prose' | 'code'; text: string };
export type InlinePart = { kind: 'text' | 'code' | 'bold'; text: string };

const FENCE = /```[^\n]*\n([\s\S]*?)```/g;
const INLINE = /`([^`]+)`|\*\*(.+?)\*\*/g;
const TOOL_ARGUMENT = /(?:^|, )(\w+): (.*?)(?=, \w+: |$)/g;
const TOOL_CHIP_LENGTH = 60;

/** 940, 1.2k, 212.4k, 1.7M */
export function formatCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

/** "31 Oct 17:21" in the viewer's locale */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} ${formatTime(iso)}`;
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

export function fencedParts(text: string): FencedPart[] {
  const parts: FencedPart[] = [];
  let last = 0;
  for (const match of text.matchAll(FENCE)) {
    if (match.index > last) parts.push({ kind: 'prose', text: text.slice(last, match.index) });
    parts.push({ kind: 'code', text: match[1] ?? '' });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: 'prose', text: text.slice(last) });
  return parts;
}

export function inlineParts(text: string): InlinePart[] {
  const parts: InlinePart[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    if (match.index > last) parts.push({ kind: 'text', text: text.slice(last, match.index) });
    parts.push(match[1] !== undefined ? { kind: 'code', text: match[1] } : { kind: 'bold', text: match[2] ?? '' });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ kind: 'text', text: text.slice(last) });
  return parts;
}

/** The text on a tool chip, from the server's "key: value, key: value" summary: the description, else the first value. */
export function toolChip(summary: string): string {
  const values = new Map<string, string>();
  for (const match of summary.matchAll(TOOL_ARGUMENT)) {
    if (match[1] && !values.has(match[1])) values.set(match[1], match[2] ?? '');
  }
  const chip = values.get('description') ?? values.values().next().value ?? '';
  return chip.slice(0, TOOL_CHIP_LENGTH);
}
