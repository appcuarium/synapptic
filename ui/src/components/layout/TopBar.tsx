// The top bar: dark teal in every theme (data-theme="dark" scopes the dark tokens to it).
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { LogoIcon, MenuIcon, MonitorIcon, MoonIcon, RestartIcon, SunIcon } from '@/components/icons';
import { Described } from '@/components/machine/Described';
import { useSidebar } from '@/components/ui/sidebar';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import type { DashboardConfig } from '@/features/settings/types';
import { type ThemeChoice, useTheme } from '@/lib/theme';
import { useWs, useWsQuery } from '@/lib/ws/react';

// Restart: the server restarts itself where it runs (`synapptic dashboard restart`, or the author's); the page
// waits for the old one to go, polls /api/health until the new one answers, then reloads.
const RESTART_FIRST_WAIT_MS = 1_500;
const RESTART_POLL_MS = 1_000;
const RESTART_GIVE_UP_MS = 60_000;

async function serverIsUp(): Promise<boolean> {
  try {
    const reply = await fetch('/api/health', { cache: 'no-store' });
    return reply.ok;
  } catch {
    return false;
  }
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

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
  const client = useWs();
  const { data: config } = useWsQuery<DashboardConfig>('config.get', {}, 'config');
  const [restarting, setRestarting] = useState<'idle' | 'waiting' | 'failed'>('idle');

  const restart = async () => {
    if (restarting === 'waiting') return;
    setRestarting('waiting');
    try {
      await client.request('server.restart', {});
    } catch {
      setRestarting('failed');
      return;
    }
    await wait(RESTART_FIRST_WAIT_MS);
    const deadline = Date.now() + RESTART_GIVE_UP_MS;
    while (Date.now() < deadline) {
      if (await serverIsUp()) {
        window.location.reload();
        return;
      }
      await wait(RESTART_POLL_MS);
    }
    setRestarting('failed');
  };

  return (
    <header data-theme="dark"
      className="sticky top-0 z-20 h-12 shrink-0 bg-brand border-b border-brand-line flex items-center gap-3 gutter">
      <Described hint="Menu" side="bottom">
        <button type="button" aria-label="Open menu" onClick={toggleSidebar}
          className="md:hidden -ml-2 size-11 grid place-items-center text-fg-2 hover:text-fg">
          <MenuIcon />
        </button>
      </Described>
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
            <Described key={choice} hint={`${label} mode`} side="bottom">
              {/* the chip's own state, stated here: the tooltip trigger writes its open state to data-state too */}
              <ToggleGroupItem value={choice} aria-label={label} className={themeItemClasses}
                data-state={theme === choice ? 'on' : 'off'}>
                <Icon />
              </ToggleGroupItem>
            </Described>
          ))}
        </ToggleGroup>
        {/* Machine's restart arrow, as a theme chip in its off state: it never stays selected */}
        <Described side="bottom"
          hint={restarting === 'failed' ? 'The server did not come back; restart it where it runs' : 'Restart the dashboard server'}>
          <button type="button" onClick={restart} disabled={restarting === 'waiting'}
            aria-label="Restart the dashboard server"
            className={`${themeItemClasses} inline-flex items-center justify-center ${restarting === 'waiting' ? 'animate-spin' : ''}`}
            data-state="off">
            <RestartIcon />
          </button>
        </Described>
      </div>
    </header>
  );
}
