// Sessions: the mockup's session browser. The project comes from the sidebar (?project=), the session from a
// tile (?session=). Sessions slide across the top as Machine's tile strip (shade = conversation length), the
// conversation fills the middle in pages of 100 turns, the stats row and the connection bar sit below.
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/PageHeader';
import { fieldClasses } from '@/components/machine/field';
import { TileStrip } from '@/components/machine/TileStrip';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';
import { logPosition } from '@/lib/tileColor';
import { useConnectionState, useWs, useWsQuery } from '@/lib/ws/react';
import { TurnView } from './Conversation';
import { formatCount, formatDateTime } from './format';
import { LIVE_REFRESH_MS, type SessionRow, type SessionStats, type TurnsPage } from './types';

const PAGE_SIZE = 100;
// A live session's new turns: asked for every 2s (the server reads only the transcript's new bytes), up to the
// server's page limit, followed down while the reader is within FOLLOW_SLACK_PX of the bottom.
const LIVE_TURNS_MS = 2_000;
const LIVE_TURNS_MAX = 500;
const FOLLOW_SLACK_PX = 80;
// Machine's tiles grow to fit names of up to ~55 characters (measured: 120-264px). Session titles run to 120,
// so the tile shows the first 48 and the tooltip has the whole title.
const TILE_NAME_CHARS = 48;

function tileName(title: string | null): string {
  const name = title || '(no title)';
  return name.length > TILE_NAME_CHARS ? `${name.slice(0, TILE_NAME_CHARS - 1)}…` : name;
}
const CONNECTION = {
  open: ['bg-teal-500', 'Connected'],
  connecting: ['bg-amber-500', 'Connecting'],
  closed: ['bg-rose-500', 'Disconnected'],
} as const;

function matches(session: SessionRow, query: string): boolean {
  const haystack = `${session.title ?? ''} ${session.project} ${session.model ?? ''} ${session.session_id}`;
  return haystack.toLowerCase().includes(query);
}

export function StatCell({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="flex min-h-[68px] shrink-0 flex-col items-center justify-start px-1 sm:px-3 py-2">
      <span className="whitespace-nowrap text-center text-[8px] sm:text-[10px] text-fg-subtle uppercase tracking-wide mb-0.5">{label}</span>
      <span className="whitespace-nowrap text-center text-[13px] sm:text-lg font-medium text-primary">{value}</span>
      <span className="whitespace-nowrap text-center text-[8px] sm:text-[10px] text-fg-subtle">{sub}</span>
    </div>
  );
}

function StatsRow({ session, stats }: { session: SessionRow; stats: SessionStats }) {
  const input = stats.total_input_tokens;
  const cache = stats.total_cache_read_tokens;
  const requests = Math.max(1, stats.total_requests);
  return (
    // one row at every width (the mockup's); on phones the cells are smaller so all six fit
    <div className="shrink-0 border-t border-line bg-page flex items-start justify-around px-1 sm:px-2 py-1 min-h-[52px] overflow-x-auto">
      <StatCell label="Requests" value={formatCount(stats.total_requests)} sub={`${formatCount(session.turn_count)} turns`} />
      <StatCell label="Input" value={formatCount(input)} sub="tokens" />
      <StatCell label="Output" value={formatCount(stats.total_output_tokens)} sub="tokens" />
      <StatCell label="Cache" value={formatCount(cache)} sub={`${(cache + input > 0 ? (cache / (cache + input)) * 100 : 0).toFixed(1)}% of input`} />
      <StatCell label="Cost" value={`$${stats.estimated_cost_usd.toFixed(2)}`} sub={`$${(stats.estimated_cost_usd / requests).toFixed(3)} / request`} />
      <StatCell label="Model" value={(session.model ?? 'unknown').replace('claude-', '')} sub={session.live ? 'live' : formatDateTime(session.last_timestamp ?? '')} />
    </div>
  );
}

function ConnectionBar({ session }: { session: SessionRow | undefined }) {
  const [dot, label] = CONNECTION[useConnectionState()];
  return (
    <div className="shrink-0 flex items-center bg-page h-6 border-t border-line">
      {/* one line at every width: on phones the source and the model drop out, as the size already does */}
      <div className="py-1 px-2 text-left h-6 text-[10px] flex items-center justify-between flex-1 whitespace-nowrap">
        <div className="flex items-center">
          <span className="flex items-center"><span className={cn('mr-1 w-1.5 h-1.5 rounded-full inline-block', dot)} /><span className="text-fg-muted">{label}</span></span>
          <span className="hidden sm:flex items-center mx-2 text-fg-subtle">session index</span>
        </div>
        {session && (
          <div className="flex items-center gap-3 text-fg-subtle">
            <span>SESSION <span className="text-fg-2">{session.session_id.slice(0, 8)}</span></span>
            <span className="hidden sm:inline">MODEL <span className="text-fg-2">{session.model}</span></span>
            <span className="hidden sm:inline">SIZE <span className="text-fg-2">{(session.file_size / 1e6).toFixed(1)} MB</span></span>
          </div>
        )}
      </div>
    </div>
  );
}

export function SessionsPage() {
  const [params, setParams] = useSearchParams();
  const project = params.get('project');
  const [query, setQuery] = useState('');
  const [newestFirst, setNewestFirst] = useState(true);
  const sessions = useWsQuery<SessionRow[]>('sessions.list', project ? { project } : {}, undefined, LIVE_REFRESH_MS);
  const client = useWs();

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (sessions.data ?? []).filter((s) => !q || matches(s, q));
    // live sessions first, whichever way the rest are sorted
    return list.sort((a, b) => Number(Boolean(b.live)) - Number(Boolean(a.live))
      || (newestFirst ? -1 : 1) * (a.first_timestamp ?? '').localeCompare(b.first_timestamp ?? ''));
  }, [sessions.data, query, newestFirst]);
  const selected = visible.find((s) => s.session_id === params.get('session')) ?? visible[0];

  const turns = useInfiniteQuery({
    queryKey: ['sessions.turns', selected?.session_id],
    queryFn: ({ pageParam }) => client.request<TurnsPage>('sessions.turns', { session_id: selected!.session_id, offset: pageParam, limit: PAGE_SIZE }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.turns.length < last.total ? last.offset + last.turns.length : undefined),
    enabled: Boolean(selected),
  });

  const counts = visible.map((s) => s.turn_count);
  const tiles = visible.map((s) => ({
    id: s.session_id,
    name: tileName(s.title),
    value: formatCount(s.turn_count),
    sub: s.session_id.slice(0, 8),
    live: s.live,
    strength: logPosition(s.turn_count, Math.min(...counts), Math.max(...counts)),
    title: `${s.title ?? ''} · ${formatDateTime(s.first_timestamp ?? '')} · ${s.model ?? ''}`,
  }));
  const select = (id: string) => setParams((next) => { next.set('session', id); return next; });
  const firstPage = turns.data?.pages[0];
  const loaded = turns.data?.pages.flatMap((page) => page.turns) ?? [];
  // A live session with its whole conversation loaded: ask for everything from the last loaded turn on (it may
  // have grown) every LIVE_TURNS_MS; the server reads only what the transcript added.
  const liveBase = Math.max(0, loaded.length - 1);
  const live = useQuery({
    queryKey: ['sessions.turns.live', selected?.session_id, liveBase],
    queryFn: () => client.request<TurnsPage>('sessions.turns', { session_id: selected!.session_id, offset: liveBase, limit: LIVE_TURNS_MAX }),
    enabled: Boolean(selected?.live) && Boolean(firstPage) && !turns.hasNextPage,
    refetchInterval: LIVE_TURNS_MS,
  });
  const shown = live.data ? [...loaded.slice(0, live.data.offset), ...live.data.turns] : loaded;
  const stats = live.data?.stats ?? firstPage?.stats;

  // Follow new turns down while the reader is at the bottom; stay put once they scroll up to read.
  const chatRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(false);
  const onChatScroll = () => {
    const chat = chatRef.current;
    if (chat) followRef.current = chat.scrollHeight - chat.scrollTop - chat.clientHeight < FOLLOW_SLACK_PX;
  };
  useLayoutEffect(() => {
    const chat = chatRef.current;
    if (chat && followRef.current) chat.scrollTop = chat.scrollHeight;
  }, [live.dataUpdatedAt]);

  return (
    <section className="h-[calc(100dvh-3rem)] flex flex-col">
      <PageHeader title="Sessions">
        <span className="text-sm text-fg-subtle truncate">{project ?? 'All projects'}</span>
        <label htmlFor="session-search" className="sr-only">Search sessions</label>
        <Input id="session-search" type="search" placeholder="Search…" value={query} onChange={(e) => setQuery(e.target.value)}
          className={cn(fieldClasses, 'ml-auto w-56 max-w-[45%]')} />
      </PageHeader>

      <TileStrip label="Sessions" tiles={tiles} selectedId={selected?.session_id ?? null} onSelect={select}
        statusLeft={<span>{visible.length} sessions</span>}
        statusRight={
          <button type="button" onClick={() => setNewestFirst(!newestFirst)}
            className={cn('inline-flex items-center gap-0.5 text-xs font-semibold leading-none cursor-pointer transition-colors',
              newestFirst ? 'text-primary hover:text-primary-hover' : 'text-destructive hover:text-destructive/80')}>
            <span>{newestFirst ? 'Newest' : 'Oldest'}</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"
              style={{ transform: newestFirst ? '' : 'rotate(180deg)' }}><path d="M6 9l6 6 6-6" /></svg>
          </button>
        } />

      <div ref={chatRef} onScroll={onChatScroll} className="flex-1 min-h-0 overflow-y-auto p-5">
        {sessions.isPending && <p className="text-sm text-fg-subtle">Loading sessions…</p>}
        {sessions.error && <p className="text-sm text-fg-subtle">Could not load the sessions: {sessions.error.message}</p>}
        {sessions.data && visible.length === 0 && <p className="text-sm text-fg-subtle">No sessions match.</p>}
        {selected && turns.isPending && <p className="text-sm text-fg-subtle">Reading the transcript ({(selected.file_size / 1e6).toFixed(1)} MB)…</p>}
        {turns.error && <p className="text-sm text-fg-subtle">Could not open this session: {turns.error.message}</p>}
        {shown.map((turn, i) => <TurnView key={i} turn={turn} />)}
        {turns.hasNextPage && (
          <div className="flex justify-center">
            <Button variant="secondary" className="rounded-xs shadow-none" disabled={turns.isFetchingNextPage} onClick={() => turns.fetchNextPage()}>
              {turns.isFetchingNextPage ? 'Loading…' : `Show more (${shown.length} of ${firstPage?.total})`}
            </Button>
          </div>
        )}
      </div>

      {stats && selected && <StatsRow session={selected} stats={stats} />}
      <ConnectionBar session={selected} />
    </section>
  );
}
