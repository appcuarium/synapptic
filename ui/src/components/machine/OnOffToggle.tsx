// Machine's on/off pill (Strategy Analytics settings): shadcn's Toggle (Radix) with Machine's classes on
// top, exactly as machine-fe styles it. Translated to Tailwind 4 (rounded -> rounded-sm) and our roles.
import { Toggle } from '@/components/ui/toggle';
import { cn } from '@/lib/cn';

// State colours are theme tokens (toggle-on / toggle-off): Machine's translucent pill in dark, solid colours
// in light (translucency washes out over a light page and on/off become unreadable).
export const onOffClasses =
  'h-6 min-w-0 px-2 text-[11px] font-normal rounded-sm border border-transparent shadow-none ' +
  'focus-visible:ring-2 focus-visible:ring-fg-2 disabled:opacity-60 ' +
  'data-[state=on]:bg-toggle-on data-[state=on]:text-toggle-on-fg hover:data-[state=on]:bg-toggle-on-hover ' +
  'hover:data-[state=on]:text-toggle-on-fg data-[state=off]:bg-toggle-off data-[state=off]:text-toggle-off-fg ' +
  'hover:data-[state=off]:bg-toggle-off-hover hover:data-[state=off]:text-toggle-off-fg';

export function OnOffToggle({ on, onChange, labelledBy, disabled, className }: {
  on: boolean;
  onChange: (on: boolean) => void;
  labelledBy: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <Toggle pressed={on} onPressedChange={onChange} aria-labelledby={labelledBy} disabled={disabled}
      className={cn(onOffClasses, className)}>
      {on ? 'on' : 'off'}
    </Toggle>
  );
}

// A labelled setting with its toggle at the right (the mockup's Message Batches row).
export function ToggleRow({ id, label, description, on, onChange, disabled }: {
  id: string;
  label: string;
  description: string;
  on: boolean;
  onChange: (on: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-[13px] text-fg-muted font-medium" id={id}>{label}</div>
        <div className="text-xs text-fg-faint truncate">{description}</div>
      </div>
      <OnOffToggle on={on} onChange={onChange} labelledBy={id} disabled={disabled} />
    </div>
  );
}
