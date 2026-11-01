// The top bar: dark teal in every theme (data-theme="dark" scopes the dark tokens to it).
import { Link } from 'react-router-dom';
import { LogoIcon, MenuIcon, MonitorIcon, MoonIcon, SunIcon } from '@/components/icons';
import { useSidebar } from '@/components/ui/sidebar';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import type { DashboardConfig } from '@/features/settings/types';
import { type ThemeChoice, useTheme } from '@/lib/theme';
import { useWsQuery } from '@/lib/ws/react';

// Machine's pill on shadcn's ToggleGroupItem (classes from the mockup, Tailwind 4 names). On the teal bar
// the selected one is the only filled, bright chip (brand-active); the others recede into the bar
// (no fill, dim teal icon) and fill in only on hover. Two cues per state: fill and icon brightness.
const themeItemClasses =
  'h-6 touch:h-11 min-w-0 px-2 rounded-sm border border-transparent shadow-none focus-visible:ring-2 ' +
  'focus-visible:ring-fg-2 data-[state=on]:bg-brand-active data-[state=on]:text-brand-active-icon ' +
  'data-[state=off]:bg-transparent data-[state=off]:text-brand-icon hover:data-[state=on]:bg-brand-active ' +
  'hover:data-[state=on]:text-brand-active-icon hover:data-[state=off]:bg-brand-line hover:data-[state=off]:text-brand-icon-hover';

const THEME_OPTIONS: { choice: ThemeChoice; label: string; Icon: typeof SunIcon }[] = [
  { choice: 'light', label: 'Light', Icon: SunIcon },
  { choice: 'system', label: 'System', Icon: MonitorIcon },
  { choice: 'dark', label: 'Dark', Icon: MoonIcon },
];

export function TopBar() {
  const [theme, setTheme] = useTheme();
  const { toggleSidebar } = useSidebar();
  const { data: config } = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  return (
    <header data-theme="dark"
      className="sticky top-0 z-20 h-12 shrink-0 bg-brand border-b border-brand-line flex items-center gap-3 gutter">
      <button type="button" aria-label="Open menu" onClick={toggleSidebar}
        className="md:hidden -ml-2 size-11 grid place-items-center text-fg-2 hover:text-fg">
        <MenuIcon />
      </button>
      <Link to="/" className="flex items-center gap-2.5 md:w-[calc(3.5rem-16px)] lg:w-[calc(14rem-16px)] shrink-0">
        <LogoIcon className="text-primary" />
        <span className="text-[15px] font-semibold text-fg md:hidden lg:inline">Synapptic</span>
        <span className="text-[13px] text-fg-muted hidden lg:inline">0.1.0b6</span>
      </Link>
      <div className="ml-auto flex items-center gap-3 text-[13px] text-fg-2 min-w-0">
        {config && (
          <>
            <span className="hidden sm:inline truncate">
              {config.provider} · <span className="text-fg">{config.model}</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-primary" aria-hidden="true" />
              <span className="hidden sm:inline">Batches</span> {config.batches?.extract ? 'on' : 'off'}
            </span>
          </>
        )}
        <ToggleGroup type="single" spacing={1} aria-label="Theme" value={theme}
          onValueChange={(value) => { if (value) setTheme(value as ThemeChoice); }}>
          {THEME_OPTIONS.map(({ choice, label, Icon }) => (
            <ToggleGroupItem key={choice} value={choice} title={label} aria-label={label} className={themeItemClasses}>
              <Icon />
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
    </header>
  );
}
