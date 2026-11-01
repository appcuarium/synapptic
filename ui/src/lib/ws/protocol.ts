// Wire protocol of the dashboard websocket at /api/ws. Mirrors src/synapptic/dashboard/protocol.py:
// keep both in step and bump PROTOCOL_VERSION on any breaking change.
import { z } from 'zod';

export const PROTOCOL_VERSION = 1;

export const ErrorCode = {
  BadMessage: 'bad_message',
  UnknownMethod: 'unknown_method',
  InvalidParams: 'invalid_params',
  Internal: 'internal',
} as const;

const Hello = z.object({ type: z.literal('hello'), protocol: z.number(), server: z.string() });
const Response = z.object({ type: z.literal('response'), id: z.string(), result: z.unknown() });
const ErrorFrame = z.object({
  type: z.literal('error'),
  id: z.string().nullable(),
  code: z.string(),
  message: z.string(),
});
const Event = z.object({ type: z.literal('event'), topic: z.string(), data: z.unknown() });
const Pong = z.object({ type: z.literal('pong') });

export const ServerMessage = z.discriminatedUnion('type', [Hello, Response, ErrorFrame, Event, Pong]);
export type ServerMessage = z.infer<typeof ServerMessage>;

export type ClientMessage =
  | { type: 'request'; id: string; method: string; params: Record<string, unknown> }
  | { type: 'subscribe'; topic: string }
  | { type: 'unsubscribe'; topic: string }
  | { type: 'ping' };
