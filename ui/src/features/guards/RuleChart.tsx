// A rule's last two weeks, one bar per day: how often it was shown (the lighter part of the bar is the share
// shown in a turn that never needed it), and for a family rule the days it was missed, as a second series.
// Inline SVG with no chart library: thin bars with rounded ends on the baseline, a 2px gap between fills,
// a legend naming every series, a tooltip per bar, and the same numbers as a table for screen readers.
// Palette (validated for both surfaces with the dataviz validator): teal #0d9488 shown, rose #f43f5e missed.
import { Described } from '@/components/machine/Described';
import { dailySeries, dayLabel } from './chart';
import type { DayNumbers } from './types';

const SHOWN = '#0d9488';
const MISSED = '#f43f5e';
const IDLE_OPACITY = 0.35;
const HEIGHT = 56;
const BAR = 8;
const GAP = 2;
const SLOT = 20;          // one day: the shown bar, the gap, the missed bar, spacing
const CORNER = 2;

function Bar({ x, value, max, color, opacity, label }: { x: number; value: number; max: number; color: string; opacity?: number; label: string }) {
  if (value <= 0) return null;
  const h = Math.max(CORNER, Math.round((value / max) * (HEIGHT - 4)));
  // the day's numbers as the page's tooltip over the bar (the table below carries them for screen readers)
  return (
    <Described hint={label}>
      <rect x={x} y={HEIGHT - h} width={BAR} height={h} rx={CORNER} fill={color} fillOpacity={opacity ?? 1} />
    </Described>
  );
}

export function RuleChart({ daily, days, family, id }: { daily: DayNumbers[]; days: number; family: boolean; id: string }) {
  const series = dailySeries(daily, days);
  const max = Math.max(1, ...series.map((d) => Math.max(d.fires, d.misses)));
  const width = series.length * SLOT;
  const empty = series.every((d) => d.fires === 0 && d.misses === 0);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3 text-[10px] text-fg-subtle uppercase tracking-wide">
        <span className="inline-flex items-center gap-1"><span className="inline-block size-2 rounded-xs" style={{ background: SHOWN }} />shown</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block size-2 rounded-xs" style={{ background: SHOWN, opacity: IDLE_OPACITY }} />of it idle</span>
        {family && <span className="inline-flex items-center gap-1"><span className="inline-block size-2 rounded-xs" style={{ background: MISSED }} />missed</span>}
        <span className="ml-auto normal-case tracking-normal">{empty ? `nothing in ${days} days` : `last ${days} days`}</span>
      </div>
      <svg role="img" aria-labelledby={`chart-${id}`} viewBox={`0 0 ${width} ${HEIGHT + 14}`} width="100%" height={HEIGHT + 14}
        preserveAspectRatio="xMinYMid meet" className="overflow-visible">
        <title id={`chart-${id}`}>{id}: shown and missed per day</title>
        <line x1={0} x2={width} y1={HEIGHT} y2={HEIGHT} className="stroke-line" strokeWidth={1} />
        {series.map((d, i) => {
          const x = i * SLOT;
          const useful = d.fires - d.idle;
          return (
            <g key={d.day}>
              {/* the whole shown bar, then the useful part over it: the lighter remainder is the idle share */}
              <Bar x={x} value={d.fires} max={max} color={SHOWN} opacity={IDLE_OPACITY} label={`${dayLabel(d.day)}: shown ${d.fires}, idle ${d.idle}`} />
              <Bar x={x} value={useful} max={max} color={SHOWN} label={`${dayLabel(d.day)}: shown ${d.fires}, idle ${d.idle}`} />
              {family && <Bar x={x + BAR + GAP} value={d.misses} max={max} color={MISSED} label={`${dayLabel(d.day)}: missed ${d.misses}`} />}
              {(i === 0 || i === series.length - 1) && (
                <text x={i === 0 ? x : x + BAR} y={HEIGHT + 11} textAnchor={i === 0 ? 'start' : 'end'} className="fill-fg-faint text-[9px]">{dayLabel(d.day)}</text>
              )}
            </g>
          );
        })}
      </svg>
      <table className="sr-only">
        <caption>{id} per day</caption>
        <thead><tr><th>day</th><th>shown</th><th>idle</th>{family && <th>missed</th>}</tr></thead>
        <tbody>{series.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.fires}</td><td>{d.idle}</td>{family && <td>{d.misses}</td>}</tr>)}</tbody>
      </table>
    </div>
  );
}
