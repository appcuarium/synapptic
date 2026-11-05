// The app's navigation on shadcn's Sidebar: a Sheet drawer on phones (close button, Escape, focus trap),
// a 56px icon rail with tooltips at md, the full 224px sidebar from lg. Item classes from the mockup.
import { type MouseEvent, useState } from 'react';
import { Link, NavLink, useLocation, useSearchParams } from 'react-router-dom';
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar,
} from '@/components/ui/sidebar';
import { Described } from '@/components/machine/Described';
import { LIVE_REFRESH_MS, type ProjectRow } from '@/features/sessions/types';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import { cn } from '@/lib/cn';
import { useWsQuery } from '@/lib/ws/react';
import { type NavItem, useNavItems } from './nav';

const itemClasses =
  'h-10 touch:h-12 gap-3 px-4 rounded-none border-l-2 border-transparent text-[15px] font-normal text-fg-muted ' +
  'hover:text-fg hover:bg-nav-active active:bg-nav-active active:text-fg ' +
  'data-[active=true]:border-primary data-[active=true]:bg-nav-active data-[active=true]:text-fg data-[active=true]:font-normal ' +
  'group-data-[collapsible=icon]:size-auto! group-data-[collapsible=icon]:h-10! group-data-[collapsible=icon]:w-full ' +
  'group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0! [&>svg]:size-4 ' +
  // the rail is full width (mockup), so shadcn's 32px overflow clip no longer hides the label: hide it
  'group-data-[collapsible=icon]:[&>span]:hidden';

// Projects under Sessions (mockup renderProjectNav), with the nav items' hover/active colours. Hidden on the rail.
const projectClasses =
  'flex items-center justify-between gap-2 pl-11 pr-4 h-9 touch:h-12 text-[13px] text-left border-l-2 ' +
  'group-data-[collapsible=icon]:hidden';
const projectOn = 'border-primary text-fg bg-nav-active';
const projectOff = 'border-transparent text-fg-subtle hover:text-fg hover:bg-nav-active';

function isActive(pathname: string, to: string, end: boolean): boolean {
  return end ? pathname === to : pathname === to || pathname.startsWith(`${to}/`);
}

// flyout: the rail's project list beside the rail; the rows are the sidebar's, only without the 44px indent
// that lines them up under the Sessions label.
function ProjectNav({ onPick, flyout = false }: { onPick: () => void; flyout?: boolean }) {
  const projects = useWsQuery<ProjectRow[]>('sessions.projects', {}, undefined, LIVE_REFRESH_MS);
  const [params] = useSearchParams();
  const current = params.get('project') ?? '';
  const total = (projects.data ?? []).reduce((sum, p) => sum + p.sessions, 0);
  const items = [{ slug: '', label: 'All projects', count: total },
    ...(projects.data ?? []).map((p) => ({ slug: p.project, label: p.project, count: p.sessions }))];
  return (
    <div className="flex flex-col max-h-[50vh] overflow-y-auto">
      {items.map((item) => (
        <Described key={item.slug} hint={`${item.label} · ${item.count} session${item.count === 1 ? '' : 's'}`} side="right">
          <Link to={item.slug ? `/sessions?project=${encodeURIComponent(item.slug)}` : '/sessions'} onClick={onPick}
            className={cn(projectClasses, flyout && 'pl-4', current === item.slug ? projectOn : projectOff)}>
            <span className="truncate">{item.label}</span>
            <span className="text-[11px] text-fg-faint shrink-0">{item.count}</span>
          </Link>
        </Described>
      ))}
    </div>
  );
}

export function AppSidebar() {
  const NAV = useNavItems();
  const { pathname } = useLocation();
  const { setOpenMobile, isMobile, state } = useSidebar();
  const onSessions = isActive(pathname, '/sessions', false);
  const [projectsOpen, setProjectsOpen] = useState(true);
  // On the icon rail (tablet widths) the project list has no room: Sessions opens it as a flyout instead.
  const onRail = !isMobile && state === 'collapsed';
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  // Mockup: Sessions opens its project list; clicking it again while on Sessions folds the list away.
  // Already on Sessions: only the list (or the rail's flyout) changes; navigating would drop ?project=.
  const onSessionsClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (onSessions) event.preventDefault();
    if (onRail) {
      setFlyoutOpen(true);
      return;
    }
    setProjectsOpen(onSessions ? !projectsOpen : true);
    setOpenMobile(false);
  };
  const entries = (items: NavItem[]) => items.map(({ to, label, Icon, end }) => (
    <SidebarMenuItem key={to}>
      {to === '/sessions' && onRail && (
        <Popover open={flyoutOpen} onOpenChange={setFlyoutOpen}>
          <PopoverAnchor className="absolute inset-0 pointer-events-none" />
          <PopoverContent side="right" align="start" sideOffset={0} aria-label="Projects"
            className="w-56 p-0 py-2 rounded-none border-line bg-sidebar shadow-lg">
            <ProjectNav flyout onPick={() => setFlyoutOpen(false)} />
          </PopoverContent>
        </Popover>
      )}
      <SidebarMenuButton asChild isActive={isActive(pathname, to, end)} tooltip={label} className={itemClasses}>
        {/* aria-label: on the rail the visible label is hidden, the link still needs its name */}
        <NavLink to={to} end={end} onClick={to === '/sessions' ? onSessionsClick : () => setOpenMobile(false)}
          aria-label={label}
          aria-expanded={to === '/sessions' ? (onRail ? flyoutOpen : onSessions && projectsOpen) : undefined}>
          <Icon />
          <span>{label}</span>
          {to === '/sessions' && (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"
              className="ml-auto size-3.5! transition-transform group-data-[collapsible=icon]:hidden"
              style={{ transform: onSessions && projectsOpen ? 'rotate(90deg)' : '' }}><path d="M9 6l6 6-6 6" /></svg>
          )}
        </NavLink>
      </SidebarMenuButton>
      {to === '/sessions' && onSessions && projectsOpen && <ProjectNav onPick={() => setOpenMobile(false)} />}
    </SidebarMenuItem>
  ));
  return (
    // Below the 48px top bar instead of full height (shadcn's sticky-header pattern).
    <Sidebar collapsible="icon" className="top-12 h-[calc(100svh-3rem)]!">
      <SidebarContent className="py-2 gap-0">
        <SidebarMenu className="gap-0">{entries(NAV.filter((item) => !item.bottom))}</SidebarMenu>
      </SidebarContent>
      {/* the items pinned to the foot (Settings, operator 2026-11-05): shadcn's footer, the same menu */}
      <SidebarFooter className="p-0 py-2">
        <SidebarMenu className="gap-0">{entries(NAV.filter((item) => item.bottom))}</SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}
