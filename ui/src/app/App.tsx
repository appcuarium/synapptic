import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { NotBuiltYet } from '@/components/layout/PageHeader';
import { TooltipProvider } from '@/components/ui/tooltip';
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

const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <NotBuiltYet title="Overview" /> },
      { path: 'pipeline', element: <NotBuiltYet title="Pipeline" /> },
      { path: 'sessions', element: <SessionsPage /> },
      { path: 'profile', element: <GuardsPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
]);

export function App() {
  return (
    <WsProvider client={wsClient}>
      <QueryClientProvider client={queryClient}>
        {/* descriptions are tooltips on every page: one provider, shown at once like the sidebar's */}
        <TooltipProvider delayDuration={0}>
          <RouterProvider router={router} />
        </TooltipProvider>
      </QueryClientProvider>
    </WsProvider>
  );
}
