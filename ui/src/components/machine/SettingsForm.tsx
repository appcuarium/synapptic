// Machine's settings form, from strategyEditorForm.component.js (OptimizerSettingsSection, the
// Optimizer*Control rows) and strategySelector.component.js (NumberStepper), 2026-11-06: a section bar
// (a chevron, the title in small caps, a count) over a three-column grid of rows; each row has the label and
// its line on the left and the control on the right: a select, a text field, a stepper or the on/off pill.
// Machine's slate classes are mapped to the theme tokens; the light theme gets solid colours of its own.
import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { OnOffToggle } from '@/components/machine/OnOffToggle';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { tileLabelStyle } from '@/components/machine/TileLabel';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ROSE } from '@/features/guards/tone';
import { cn } from '@/lib/cn';

// The section bar wears the labels' dark teal (TileLabel: the same fill in both themes, teal-100 text;
// operator 2026-11-06, in place of Machine's tone per section) and sits 20px under the strip, as the
// Benchmark tabs do.
const SECTION_BAR = 'flex items-center gap-2 px-3 py-2 text-teal-100';

// The rows' grid (Machine: grid-cols-3 gap-x-8 gap-y-4 px-4 py-4), one column on a phone, two on a tablet.
export const settingsGridClasses = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4 px-4 py-4';

export function SettingsSection({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <div className="mt-5 rounded-none overflow-hidden border-b border-line">
      <div className={SECTION_BAR} style={tileLabelStyle}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
        <span className="text-xs font-semibold uppercase tracking-wider">{title}</span>
        {count !== undefined && <span className="text-xs opacity-70">{count}</span>}
      </div>
      <div className={cn(settingsGridClasses, 'bg-page')}>{children}</div>
    </div>
  );
}

// One row: the label (13px, medium) and its line (12px, faint) on the left, the control on the right.
// `error` (operator 2026-11-06: validation on Save) takes the line's place in rose while the field fails.
export function SettingRow({ id, label, note, error, children }: { id: string; label: ReactNode; note: string; error?: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div id={id} className="text-[13px] text-fg-subtle font-medium">{label}</div>
        <div className={cn('text-xs truncate', error ? ROSE : 'text-fg-faint')} title={error ?? note}>{error ?? note}</div>
      </div>
      {children}
    </div>
  );
}

// Machine: `h-7 w-[120px] text-xs bg-slate-800 border-slate-700 text-slate-300`.
// Machine's select is 28px tall (h-7); the size variant of the base trigger would make it 36, so it is set here too
export const settingSelectClasses = 'h-7 data-[size=default]:h-7 text-xs rounded-md bg-control border border-line text-fg-muted shadow-none focus:ring-0 focus:ring-offset-0';
// Machine: `h-7 w-[220px] rounded-none border-slate-700 bg-slate-800 text-xs text-slate-300`.
// 12px text at every width: the base input grows its text at md, Machine's does not
export const settingInputClasses = 'h-7 w-[220px] rounded-none border border-line bg-control text-xs md:text-xs text-fg-muted shadow-none focus-visible:ring-0 focus-visible:ring-offset-0';
const SELECT_ITEM_CLASSES = 'text-xs text-fg-muted focus:bg-control-hover focus:text-fg-2';

export function SettingSelect({ id, value, onValueChange, options, width = 'w-[140px]', labelledBy }: {
  id: string; value: string; onValueChange: (value: string) => void;
  options: { id: string; label: string }[]; width?: string; labelledBy?: string;
}) {
  return (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger id={id} aria-labelledby={labelledBy} className={cn(settingSelectClasses, width, 'shrink-0')}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent className="bg-control border-line">
        {options.map((option) => (
          <SelectItem key={option.id} value={option.id} className={SELECT_ITEM_CLASSES}>{option.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// Settings fields are not logins: password managers keep their inline button out (operator 2026-11-06: the
// fields wearing the manager's badge sat 66px left of their siblings, its button taking room in the row).
// The field sits in a box of its own width, so whatever a manager still injects beside it overflows the box
// instead of pushing the field left of its siblings.
const SETTING_INPUT_BOX = 'shrink-0 w-[220px] inline-flex';

export function SettingInput({ className, ...props }: React.ComponentProps<'input'>) {
  return (
    <span className={SETTING_INPUT_BOX}>
      <Input data-1p-ignore data-lpignore="true" data-bwignore {...props} className={cn(settingInputClasses, 'shrink-0', className)} />
    </span>
  );
}

// Machine's stepper: a minus, the number typed in the middle, a plus; typing never commits, the value is
// committed on blur, Enter or a step; +/- step from what the field shows.
const STEPPER_BUTTON = 'h-6 w-6 rounded-[5px] bg-control border-0 shadow-none text-fg-faint hover:bg-control-hover hover:text-fg-muted';
const STEPPER_INPUT = 'h-6 w-14 text-xs bg-transparent border-0 text-fg-muted text-center focus-visible:ring-0 focus-visible:ring-offset-0 px-0 py-0 rounded-md';

export function NumberStepper({ value, onChange, min = 0, max = 999999, step = 1, decimals, labelledBy }: {
  value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; decimals?: number; labelledBy?: string;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => { setDraft(String(value)); }, [value]);
  // `decimals` is how many a typed value keeps (the temperature: two, so 0.25 stays 0.25 and a step from it
  // gives 0.35); by default the step's own, so a count stays whole; the fix keeps 0.1 + 0.2 reading 0.3
  const kept = decimals ?? (String(step).split('.')[1] ?? '').length;
  const clamp = (n: number) => Number(Math.min(max, Math.max(min, n)).toFixed(kept));
  const shown = Number(draft);
  const current = Number.isFinite(shown) && draft.trim() !== '' ? shown : value;
  const commit = (raw: string) => {
    const n = Number(raw);
    const next = clamp(Number.isFinite(n) && raw.trim() !== '' ? n : value);
    setDraft(String(next));
    onChange(next);
  };
  return (
    <div className="flex items-center shrink-0">
      <Button type="button" variant="outline" size="icon" className={STEPPER_BUTTON} aria-label="Decrement"
        disabled={current <= min} onClick={() => commit(String(current - step))}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M5 12h14" /></svg>
      </Button>
      <input type="text" inputMode="decimal" value={draft} aria-labelledby={labelledBy} className={STEPPER_INPUT}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit(e.currentTarget.value); e.currentTarget.blur(); }
          if (e.key === 'Escape') { e.preventDefault(); setDraft(String(value)); e.currentTarget.blur(); }
        }} />
      <Button type="button" variant="outline" size="icon" className={STEPPER_BUTTON} aria-label="Increment"
        disabled={current >= max} onClick={() => commit(String(current + step))}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      </Button>
    </div>
  );
}

// A row with the on/off pill at the right (Machine's toggle rows in the same grid).
export function SettingToggle({ id, label, note, on, onChange, disabled }: {
  id: string; label: ReactNode; note: string; on: boolean; onChange: (on: boolean) => void; disabled?: boolean;
}) {
  return (
    <SettingRow id={id} label={label} note={note}>
      <OnOffToggle on={on} onChange={onChange} labelledBy={id} disabled={disabled} />
    </SettingRow>
  );
}
