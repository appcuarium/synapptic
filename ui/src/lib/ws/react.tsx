// React bindings for the shared WsClient: one connection for the app, TanStack Query on top.
// - useWsQuery: query data fetched over the socket; an optional topic keeps it live (server "event"
//   frames replace the cached value, so every open tab updates at once).
// - useWsMutation: a request with side effects.
// - useConnectionState: 'connecting' | 'open' | 'closed', for the connection indicator.
import {
  type QueryKey,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { createContext, type ReactNode, useContext, useEffect, useSyncExternalStore } from 'react';
import { type ConnectionState, WsClient } from './client';

const WsContext = createContext<WsClient | null>(null);

export function dashboardSocketUrl(location: Location = window.location): string {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`;
}

export function WsProvider({ client, children }: { client: WsClient; children: ReactNode }) {
  useEffect(() => {
    client.connect();
    return () => client.close();
  }, [client]);
  return <WsContext.Provider value={client}>{children}</WsContext.Provider>;
}

export function useWs(): WsClient {
  const client = useContext(WsContext);
  if (!client) throw new Error('useWs must be used inside <WsProvider>');
  return client;
}

export function useConnectionState(): ConnectionState {
  const client = useWs();
  return useSyncExternalStore(
    (listener) => client.onState(listener),
    () => client.state,
  );
}

export function useWsQuery<T>(
  method: string,
  params: Record<string, unknown> = {},
  topic?: string,
  refetchIntervalMs?: number,   // for data the server does not push (e.g. which sessions are live)
) {
  const client = useWs();
  const queryClient = useQueryClient();
  const queryKey: QueryKey = [method, params];
  useEffect(() => {
    if (!topic) return undefined;
    // The server pushes the full new value of the topic, already in the method's result shape.
    return client.subscribe(topic, (data) => queryClient.setQueryData<T>(queryKey, data as T));
    // queryKey is rebuilt every render; method + params identify it
  }, [client, queryClient, topic, method, JSON.stringify(params)]);
  return useQuery({ queryKey, queryFn: () => client.request<T>(method, params), refetchInterval: refetchIntervalMs });
}

export function useWsMutation<TResult, TParams extends Record<string, unknown>>(
  method: string,
  options: Omit<UseMutationOptions<TResult, Error, TParams>, 'mutationFn'> = {},
) {
  const client = useWs();
  return useMutation<TResult, Error, TParams>({
    ...options,
    mutationFn: (params) => client.request<TResult>(method, params),
  });
}
