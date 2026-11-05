// The status bar at the foot of every page (operator 2026-11-06: a global footer, not the session browser's
// own): the socket's state with its dot, then the notes the page sets (the Sessions page names its source
// and the picked session's id, model and size). Built on the sessions page's old connection bar; the page's
// notes come through a context the way the sidebar's items and the brand do (nav.tsx).
import { createContext, type ReactNode, useContext, useEffect, useState } from 'react';
import { cn } from '@/lib/cn';
import { useConnectionState } from '@/lib/ws/react';

const CONNECTION = {
  open: ['bg-teal-500', 'Connected'],
  connecting: ['bg-amber-500', 'Connecting'],
  closed: ['bg-rose-500', 'Disconnected'],
} as const;

// What a page adds to the bar: a source word after the state, items on the right.
export interface StatusNotes {
  source?: string;
  right?: ReactNode;
}

type SetNotes = (notes: StatusNotes | null) => void;
const StatusNotesContext = createContext<{ notes: StatusNotes | null; setNotes: SetNotes }>({ notes: null, setNotes: () => undefined });

export function StatusNotesProvider({ children }: { children: ReactNode }) {
  const [notes, setNotes] = useState<StatusNotes | null>(null);
  return <StatusNotesContext.Provider value={{ notes, setNotes }}>{children}</StatusNotesContext.Provider>;
}

// A page's notes for as long as it is mounted; `notes` is memoized by the page so the bar changes with it.
export function useStatusNotes(notes: StatusNotes | null) {
  const { setNotes } = useContext(StatusNotesContext);
  useEffect(() => {
    setNotes(notes);
    return () => setNotes(null);
  }, [notes, setNotes]);
}

// The frame rate at the bar's right end (operator 2026-11-06), Machine's FpsCounter (machine-fe-live
// src/components/ui/fps-counter.js) verbatim: frames counted in the animation loop, and once a second
// or more has elapsed on performance.now the rate is frames * 1000 / elapsed, rounded (a timer tick's
// window drifts and reads 61); teal from 55, amber from 30, rose below; bold, in the dashboard's teal
// (operator: "barely visible").
const FPS_SAMPLE_MS = 1000;
const FPS_FINE = 55;
const FPS_SLOW = 30;

function useFrameRate(): number {
  const [fps, setFps] = useState(0);
  useEffect(() => {
    let frames = 0;
    let last = performance.now();
    let handle = 0;
    const measure = () => {
      frames += 1;
      const now = performance.now();
      const elapsed = now - last;
      if (elapsed >= FPS_SAMPLE_MS) {
        setFps(Math.round((frames * 1000) / elapsed));
        frames = 0;
        last = now;
      }
      handle = requestAnimationFrame(measure);
    };
    handle = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(handle);
  }, []);
  return fps;
}

function FrameRate() {
  const fps = useFrameRate();
  const tone = fps >= FPS_FINE ? 'text-primary' : fps >= FPS_SLOW ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400';
  return <span className={cn('flex items-center font-bold', tone)}>{fps} fps</span>;
}

export function StatusBar() {
  const [dot, label] = CONNECTION[useConnectionState()];
  const { notes } = useContext(StatusNotesContext);
  return (
    <div className="shrink-0 flex items-center bg-page h-6 border-t border-line sticky bottom-0 mt-auto">
      {/* one line at every width: on phones the source and the page's details drop out */}
      <div className="py-1 px-2 text-left h-6 text-[10px] flex items-center justify-between flex-1 whitespace-nowrap">
        <div className="flex items-center">
          <span className="flex items-center"><span className={cn('mr-1 w-1.5 h-1.5 rounded-full inline-block', dot)} /><span className="text-fg-muted">{label}</span></span>
          {notes?.source && <span className="hidden sm:flex items-center mx-2 text-fg-subtle">{notes.source}</span>}
        </div>
        <div className="flex items-center gap-3 text-fg-subtle">
          {notes?.right}
          <FrameRate />
        </div>
      </div>
    </div>
  );
}
