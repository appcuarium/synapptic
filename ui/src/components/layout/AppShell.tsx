// Top bar across the full width, sidebar + page below (structure from the mockup).
// The sidebar's state follows the width, as in the mockup: full from lg, icon rail below it
// (phones get the drawer, opened from the top bar's menu button).
import type { CSSProperties } from 'react';
import { Outlet } from 'react-router-dom';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';
import { useMediaQuery } from '@/hooks/use-media-query';
import { AppSidebar } from './Sidebar';
import { TopBar } from './TopBar';

const SIDEBAR_SIZES = { '--sidebar-width': '14rem', '--sidebar-width-icon': '3.5rem' } as CSSProperties;

export function AppShell() {
  const wide = useMediaQuery('(min-width: 1024px)');
  return (
    <SidebarProvider open={wide} onOpenChange={() => undefined} style={SIDEBAR_SIZES} className="flex-col">
      <TopBar />
      <div className="flex flex-1 min-h-0">
        <AppSidebar />
        <SidebarInset className="min-w-0 bg-page">
          <Outlet />
        </SidebarInset>
      </div>
    </SidebarProvider>
  );
}
