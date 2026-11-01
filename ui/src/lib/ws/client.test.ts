import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectionLostError, RequestTimeoutError, WsClient, WsRequestError } from './client';

// A stand-in WebSocket: records what the client sends and lets the test play the server.
class FakeSocket {
  static instances: FakeSocket[] = [];
  sent: unknown[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closedWith: number | null = null;
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close(code = 1000) {
    this.closedWith = code;
    this.onclose?.();
  }
  serverSends(frame: object) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  hello() {
    this.serverSends({ type: 'hello', protocol: 1, server: 'test' });
  }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1]!;

function makeClient(overrides = {}) {
  const client = new WsClient({
    url: 'ws://test/api/ws',
    WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
    random: () => 0.5,
    ...overrides,
  });
  client.connect();
  return client;
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('WsClient', () => {
  it('matches responses to requests by id, in any order', async () => {
    const client = makeClient();
    latest().hello();
    const first = client.request('config.get');
    const second = client.request('providers.list');
    const [a, b] = latest().sent as { id: string }[];
    latest().serverSends({ type: 'response', id: b!.id, result: ['anthropic'] });
    latest().serverSends({ type: 'response', id: a!.id, result: { provider: 'anthropic' } });
    await expect(first).resolves.toEqual({ provider: 'anthropic' });
    await expect(second).resolves.toEqual(['anthropic']);
  });

  it('rejects with the server error code', async () => {
    const client = makeClient();
    latest().hello();
    const result = client.request('config.update', { provider: 'nope' });
    const { id } = latest().sent[0] as { id: string };
    latest().serverSends({ type: 'error', id, code: 'invalid_params', message: 'unknown provider: nope' });
    const failure = await result.catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(WsRequestError);
    expect(failure).toMatchObject({ code: 'invalid_params', message: 'unknown provider: nope' });
  });

  it('times out a request that is never answered', async () => {
    const client = makeClient({ requestTimeoutMs: 1000 });
    latest().hello();
    const result = client.request('config.get');
    vi.advanceTimersByTime(1001);
    await expect(result).rejects.toBeInstanceOf(RequestTimeoutError);
  });

  it('holds requests made while disconnected and sends them after hello', async () => {
    const client = makeClient();
    const result = client.request('config.get');
    expect(latest().sent).toEqual([]);
    latest().hello();
    const { id, method } = latest().sent[0] as { id: string; method: string };
    expect(method).toBe('config.get');
    latest().serverSends({ type: 'response', id, result: 'ok' });
    await expect(result).resolves.toBe('ok');
  });

  it('fails requests already sent when the socket drops', async () => {
    const client = makeClient();
    latest().hello();
    const result = client.request('config.get');
    latest().close(1006);
    await expect(result).rejects.toBeInstanceOf(ConnectionLostError);
  });

  it('reconnects with growing backoff, re-subscribes, and delivers events', () => {
    const client = makeClient({ backoffInitialMs: 1000 });
    const events: unknown[] = [];
    client.subscribe('config', (data) => events.push(data));
    latest().hello();
    expect(latest().sent).toContainEqual({ type: 'subscribe', topic: 'config' });

    latest().close(1006);
    expect(client.state).toBe('connecting');
    vi.advanceTimersByTime(749); // attempt 0: 1000ms, equal jitter with random 0.5 -> 750ms
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);

    latest().close(1006); // no hello yet: attempt 1 -> 2000ms -> 1500ms
    vi.advanceTimersByTime(1499);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(3);

    latest().hello();
    expect(client.state).toBe('open');
    expect(latest().sent).toContainEqual({ type: 'subscribe', topic: 'config' });
    latest().serverSends({ type: 'event', topic: 'config', data: { model: 'claude-opus-5-5' } });
    expect(events).toEqual([{ model: 'claude-opus-5-5' }]);
  });

  it('drops a silent connection after two missed heartbeats and reconnects', () => {
    makeClient({ heartbeatMs: 1000 });
    latest().hello();
    vi.advanceTimersByTime(1000);
    expect(latest().sent).toContainEqual({ type: 'ping' });
    vi.advanceTimersByTime(2000); // no frames at all since hello
    expect(FakeSocket.instances[0]!.closedWith).toBe(4000);
  });
});
