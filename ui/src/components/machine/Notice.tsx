// Notices (operator 2026-11-06): a message at the window's bottom right corner, above the status bar, in
// the labels' tones (TileLabel: the dark teal fill with teal-100 text for a success, the rose-800 and
// amber-800 fills with their 100 text for a failure and a refusal, the same darkness in both themes;
// operator: "rose for error, teal for success, both dark"); the page's 14px text; a click on it dismisses
// it, and it goes by itself after a while. Built on the sonner toaster shadcn ships (components/ui/sonner).
import { toast } from 'sonner';
import { TONE_CLASSES, tileLabelStyle } from '@/components/machine/TileLabel';
import { Toaster } from '@/components/ui/sonner';
import { cn } from '@/lib/cn';

export type NoticeKind = 'done' | 'warning' | 'error';

const NOTICE_MS = 5000;
// above the status bar (24px) with the page's gutter
const NOTICE_OFFSET = { bottom: 32, right: 16 };
const TONE: Record<NoticeKind, string> = { done: 'text-teal-100', warning: TONE_CLASSES.amber, error: TONE_CLASSES.rose };

export function notify(kind: NoticeKind, message: string) {
  toast.custom((id) => (
    <button type="button" onClick={() => toast.dismiss(id)} title="click to dismiss"
      style={kind === 'done' ? tileLabelStyle : undefined}
      className={cn('block w-[356px] max-w-[calc(100vw-32px)] px-4 py-3 rounded-sm text-left text-sm cursor-pointer', TONE[kind])}>
      {message}
    </button>
  ), { duration: NOTICE_MS });
}

export function Notices() {
  return <Toaster position="bottom-right" offset={NOTICE_OFFSET} mobileOffset={NOTICE_OFFSET} duration={NOTICE_MS} />;
}
