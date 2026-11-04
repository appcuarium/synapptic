// Machine's TileStripShell: a 36px status bar (count, chevrons) over a drag-scrollable row of tiles with a
// hidden scrollbar. Tiles are at least 120px wide and grow to fit their name, 70px tall (Machine: 56px), square, with 1px
// dividers; the selected tile is raised with a teal top edge and scrolled to the centre. Classes and sizes
// are those measured against Machine for the mockup's session strip.
import { type ReactNode, useCallback, useEffect, useRef } from 'react';
import { Described } from '@/components/machine/Described';
import { cn } from '@/lib/cn';
import { useResolvedTheme } from '@/lib/theme';
import { tileBackground } from '@/lib/tileColor';

export interface Tile {
  id: string;
  name: string;      // 11px line on top (Machine: 8px; raised for longer session titles)
  value: string;     // 18px bold (Machine: 14px)
  sub: string;       // 10px line below
  strength: number;  // 0..1, see tileBackground
  title?: string;
  live?: boolean;    // a pulsing dot in the corner (mockup: a session that is running)
}

const DRAG_THRESHOLD_PX = 3;

// Click-and-drag scrolling (Machine's useDragScroll): listeners on the document, the element resolved at
// event time, and a guard so a drag does not also click the tile under the pointer.
function useDragScroll() {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef({ down: false, startX: 0, scrollLeft: 0, dragged: false });
  useEffect(() => {
    const onDown = (event: MouseEvent) => {
      const el = ref.current;
      if (!el || !el.contains(event.target as Node)) return;
      drag.current = { down: true, startX: event.pageX, scrollLeft: el.scrollLeft, dragged: false };
      el.style.cursor = 'grabbing';
      el.style.userSelect = 'none';
    };
    const onMove = (event: MouseEvent) => {
      const el = ref.current;
      if (!drag.current.down || !el) return;
      const walk = event.pageX - drag.current.startX;
      if (Math.abs(walk) > DRAG_THRESHOLD_PX) drag.current.dragged = true;
      el.scrollLeft = drag.current.scrollLeft - walk;
    };
    const onUp = () => {
      if (!drag.current.down) return;
      drag.current.down = false;
      if (ref.current) {
        ref.current.style.cursor = '';
        ref.current.style.userSelect = '';
      }
    };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('mouseup', onUp, true);
    };
  }, []);
  const wasDragged = useCallback(() => drag.current.dragged, []);
  return { ref, wasDragged };
}

const chevronClasses =
  'flex-shrink-0 h-6 flex items-center justify-center p-0.5 rounded hover:bg-control-hover text-fg-subtle hover:text-fg-2 transition-colors';

export function TileStrip({ tiles, selectedId, onSelect, statusLeft, statusRight, label }: {
  tiles: Tile[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  statusLeft: ReactNode;
  statusRight?: ReactNode;   // before the chevrons (Machine: the sort toggle)
  label: string;
}) {
  useResolvedTheme(); // the tile colours read theme tokens: re-render when the theme on screen changes
  const { ref, wasDragged } = useDragScroll();

  useEffect(() => {
    const strip = ref.current;
    const tile = strip?.querySelector<HTMLElement>(`[data-tile-id="${CSS.escape(selectedId ?? '')}"]`);
    if (!strip || !tile || strip.clientWidth === 0) return;
    strip.scrollTo({ left: tile.offsetLeft + tile.offsetWidth / 2 - strip.clientWidth / 2, behavior: 'smooth' });
  }, [selectedId, tiles.length, ref]);

  const scrollBy = (direction: number) => {
    const strip = ref.current;
    if (strip) strip.scrollBy({ left: direction * strip.clientWidth * 0.6, behavior: 'smooth' });
  };

  return (
    <div className="shrink-0 border-b border-line">
      <div className="flex items-center h-9 px-1.5 border-b border-line bg-page">
        <div className="flex items-center gap-1 min-w-0 shrink text-xs text-fg-subtle px-1">{statusLeft}</div>
        <div className="flex items-center gap-3 ml-auto flex-shrink-0">
          {statusRight}
          <div className="flex items-center gap-0.5">
            <Described hint="Scroll left">
              <button type="button" aria-label="Scroll left" className={chevronClasses} onClick={() => scrollBy(-1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg>
              </button>
            </Described>
            <Described hint="Scroll right">
              <button type="button" aria-label="Scroll right" className={chevronClasses} onClick={() => scrollBy(1)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg>
              </button>
            </Described>
          </div>
        </div>
      </div>
      <div ref={ref} className="strip-scroll overflow-x-auto">
        <div role="radiogroup" aria-label={label} className="flex">
          {tiles.map((tile) => {
            const selected = tile.id === selectedId;
            return (
              <div key={tile.id} data-tile-id={tile.id}
                className="grow shrink-0 min-w-[120px] h-[70px] border-r border-tile-divider p-0 overflow-hidden">
                <Described hint={`${tile.title ?? tile.name}${tile.live ? ' · live' : ''}`} side="bottom">
                <button type="button" role="radio" aria-checked={selected}
                  onClick={() => { if (!wasDragged()) onSelect(tile.id); }}
                  className={cn(
                    'strategy-tile flex flex-col items-center justify-evenly w-full h-full transition-all duration-150 cursor-pointer border-t-2 focus:outline-hidden relative',
                    selected
                      ? 'shadow-lg scale-[1.02] border-t-primary'
                      : 'border-transparent opacity-[var(--tile-idle-opacity)] hover:opacity-100 hover:border-t-primary/60',
                    tile.live && 'bg-lime-600 motion-safe:animate-live',
                    // live: the edge in lime, the same steps as the accent (teal-700 light / teal-400 dark)
                    tile.live && (selected
                      ? 'border-t-lime-700 dark:border-t-lime-400'
                      : 'hover:border-t-lime-700/60 dark:hover:border-t-lime-400/60'),
                  )}
                  // live: the same lime for every live session, pulsing (animate-live), not shaded by length
                  style={tile.live ? undefined : { backgroundColor: tileBackground(0, tile.strength) }}>
                  {tile.live && <span className="absolute top-1.5 left-1.5 size-1.5 rounded-full bg-lime-300" aria-hidden="true" />}
                  {/* text follows the tile's hue (Machine: <hue>-100 value, <hue>-100/80 name and id) */}
                  <span className={cn('relative text-[11px] truncate w-full text-center px-1 leading-tight', tile.live ? 'text-lime-100/80' : 'text-tile-fg/80')}>{tile.name}</span>
                  <span className={cn('relative text-lg font-bold leading-snug', tile.live ? 'text-lime-100' : 'text-tile-fg')}>{tile.value}</span>
                  <span className={cn('relative text-[10px] leading-tight', tile.live ? 'text-lime-100/80' : 'text-tile-fg/80')}>{tile.sub}</span>
                </button>
                </Described>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
