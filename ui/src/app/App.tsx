import type { ReactElement, ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider, type RouteObject } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { NotBuiltYet } from '@/components/layout/PageHeader';
import { Notices } from '@/components/machine/Notice';
import { TooltipProvider } from '@/components/ui/tooltip';
import { BenchmarkPage } from '@/features/guards/BenchmarkPage';
import { GuardsPage } from '@/features/guards/GuardsPage';
import { SessionsPage } from '@/features/sessions/SessionsPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { WsClient } from '@/lib/ws/client';
import { dashboardSocketUrl, WsProvider } from '@/lib/ws/react';

// One socket and one query cache for the whole app. Queries are kept fresh by server pushes
// (topics), so they never go stale on a timer and are not refetched on window focus.
// 60s per request: the first page of a long session parses its transcript (11s for 1.7 GB, measured).
const wsClient = new WsClient({ url: dashboardSocketUrl(), requestTimeoutMs: 60_000 });
const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: Infinity, refetchOnWindowFocus: false, retry: 2 } },
});

// The pages, by the path the sidebar links ('' is the index). Another app built on this one (the author
// dashboard) takes this list as it is and adds its own on top: a page it replaces, routes it adds.
const PAGES: { path: string; element: ReactElement }[] = [
  { path: '', element: <NotBuiltYet title="Overview" /> },
  { path: 'pipeline', element: <NotBuiltYet title="Pipeline" /> },
  { path: 'sessions', element: <SessionsPage /> },
  { path: 'profile', element: <GuardsPage /> },
  { path: 'benchmark', element: <BenchmarkPage /> },
  { path: 'settings', element: <SettingsPage /> },
];

export interface RouteAddons {
  replace?: Record<string, ReactElement>;   // a public page by path, shown instead of the public one
  add?: RouteObject[];                       // routes the public app does not have
}

export function appRouter(addons: RouteAddons = {}) {
  const children: RouteObject[] = PAGES.map(({ path, element }) => ({
    ...(path ? { path } : { index: true }),
    element: addons.replace?.[path] ?? element,
  }));
  return createBrowserRouter([{ element: <AppShell />, children: [...children, ...(addons.add ?? [])] }]);
}

// The socket, the query cache and the tooltips around any router built from the pages above.
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <WsProvider client={wsClient}>
      <QueryClientProvider client={queryClient}>
        {/* descriptions are tooltips on every page: one provider, shown at once like the sidebar's */}
        <TooltipProvider delayDuration={0}>{children}</TooltipProvider>
        {/* notices at the bottom right on every page (operator 2026-11-06) */}
        <Notices />
      </QueryClientProvider>
    </WsProvider>
  );
}

const router = appRouter();

export function App() {
  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
