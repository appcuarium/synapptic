// Shapes of the server methods sessions.projects / sessions.list / sessions.turns
// (src/synapptic/dashboard/handlers.py).
export interface ProjectRow {
  project: string;
  sessions: number;
  latest: string;
}

export interface SessionRow {
  session_id: string;
  project: string;
  title: string | null;
  model: string | null;
  first_timestamp: string | null;
  last_timestamp: string | null;
  message_count: number;   // every user/assistant record, tool traffic included
  turn_count: number;      // the turns the conversation shows (you + the agent + compaction markers)
  file_size: number;
  live?: boolean;   // sessions.list only: written in the last 5 minutes and not ended
}

// The relay's browser re-checked live sessions every 5s.
export const LIVE_REFRESH_MS = 5_000;

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_create: number;
}

export type Block =
  | { type: 'text'; text: string }
  | { type: 'tool_use'; name: string; summary: string }
  | { type: 'compaction'; trigger: string; pre_tokens: number };

export interface Turn {
  role: 'user' | 'assistant' | 'system';
  timestamp: string;
  model: string | null;
  usage: Usage | null;
  blocks: Block[];
}

export interface SessionStats {
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens: number;
  total_cache_create_tokens: number;
  estimated_cost_usd: number;
}

export interface TurnsPage {
  session: SessionRow;
  stats: SessionStats;
  total: number;
  offset: number;
  turns: Turn[];
}
