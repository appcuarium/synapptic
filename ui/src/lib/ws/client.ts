// One websocket to the dashboard server, shared by the whole app.
//
// - request(method, params): resolves with the matching {type:"response", id} or rejects with WsRequestError
//   ({type:"error"}), RequestTimeoutError, or ConnectionLostError when the socket drops before the reply.
// - Requests made while disconnected wait in an outbox and are sent after the next "hello".
// - subscribe(topic, handler): the topic is re-subscribed after every reconnect; returns an unsubscribe.
// - Reconnects with exponential backoff and jitter; the attempt counter resets on "hello".
// - Heartbeat: a ping every heartbeatMs; if nothing arrives for 2 heartbeats the socket is dropped and
//   reconnected (a half-open TCP connection never fires "close" on its own).
import { type ClientMessage, PROTOCOL_VERSION, ServerMessage } from './protocol';

export type ConnectionState = 'connecting' | 'open' | 'closed';

export class WsRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'WsRequestError';
  }
}
export class RequestTimeoutError extends Error {
  constructor(method: string) {
    super(`${method} timed out`);
    this.name = 'RequestTimeoutError';
  }
}
export class ConnectionLostError extends Error {
  constructor(method: string) {
    super(`connection lost before ${method} was answered`);
    this.name = 'ConnectionLostError';
  }
}

export interface WsClientOptions {
  url: string;
  requestTimeoutMs?: number;
  heartbeatMs?: number;
  backoffInitialMs?: number;
  backoffMaxMs?: number;
  WebSocketImpl?: typeof WebSocket;
  random?: () => number;
}

interface Pending {
  method: string;
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

type EventHandler = (data: unknown) => void;
type StateListener = (state: ConnectionState) => void;

export class WsClient {
  private socket: WebSocket | null = null;
  private stateValue: ConnectionState = 'closed';
  private stopped = true;
  private attempt = 0;
  private nextId = 1;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private lastFrameAt = 0;
  private readonly pending = new Map<string, Pending>();
  private readonly outbox: ClientMessage[] = [];
  private readonly topics = new Map<string, Set<EventHandler>>();
  private readonly stateListeners = new Set<StateListener>();
  private readonly options: Required<WsClientOptions>;

  constructor(options: WsClientOptions) {
    this.options = {
      requestTimeoutMs: 10_000,
      heartbeatMs: 15_000,
      backoffInitialMs: 500,
      backoffMaxMs: 15_000,
      WebSocketImpl: WebSocket,
      random: Math.random,
      ...options,
    };
  }

  get state(): ConnectionState {
    return this.stateValue;
  }

  connect(): void {
    this.stopped = false;
    this.open();
  }

  close(): void {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1000);
    this.socket = null;
    this.failPending();
    this.setState('closed');
  }

  request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    const id = String(this.nextId++);
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new RequestTimeoutError(method));
      }, this.options.requestTimeoutMs);
      this.pending.set(id, { method, resolve: resolve as (value: unknown) => void, reject, timer });
      this.send({ type: 'request', id, method, params });
    });
  }

  subscribe(topic: string, handler: EventHandler): () => void {
    let handlers = this.topics.get(topic);
    if (!handlers) {
      handlers = new Set();
      this.topics.set(topic, handlers);
      if (this.stateValue === 'open') this.send({ type: 'subscribe', topic });
    }
    handlers.add(handler);
    return () => this.unsubscribe(topic, handler);
  }

  onState(listener: StateListener): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  private unsubscribe(topic: string, handler: EventHandler): void {
    const handlers = this.topics.get(topic);
    if (!handlers) return;
    handlers.delete(handler);
    if (handlers.size === 0) {
      this.topics.delete(topic);
      if (this.stateValue === 'open') this.send({ type: 'unsubscribe', topic });
    }
  }

  private open(): void {
    this.setState('connecting');
    const socket = new this.options.WebSocketImpl(this.options.url);
    this.socket = socket;
    socket.onmessage = (event: MessageEvent) => this.handleFrame(event.data);
    socket.onclose = () => this.handleClose(socket);
    socket.onerror = () => socket.close();
  }

  private send(message: ClientMessage): void {
    if (this.stateValue === 'open' && this.socket) {
      this.socket.send(JSON.stringify(message));
    } else if (message.type === 'request') {
      this.outbox.push(message);
    }
  }

  private handleFrame(raw: unknown): void {
    this.lastFrameAt = Date.now();
    let parsed: ServerMessage;
    try {
      parsed = ServerMessage.parse(JSON.parse(String(raw)));
    } catch {
      return; // not a protocol frame: ignore it rather than tear the connection down
    }
    switch (parsed.type) {
      case 'hello':
        this.handleHello(parsed.protocol);
        break;
      case 'response':
        this.settle(parsed.id, (pending) => pending.resolve(parsed.result));
        break;
      case 'error':
        if (parsed.id !== null) {
          this.settle(parsed.id, (pending) => pending.reject(new WsRequestError(parsed.code, parsed.message)));
        }
        break;
      case 'event':
        this.topics.get(parsed.topic)?.forEach((handler) => handler(parsed.data));
        break;
      case 'pong':
        break;
    }
  }

  private handleHello(protocol: number): void {
    if (protocol !== PROTOCOL_VERSION) {
      console.warn(`dashboard server speaks protocol ${protocol}, this app expects ${PROTOCOL_VERSION}`);
    }
    this.attempt = 0;
    this.setState('open');
    this.topics.forEach((_handlers, topic) => this.send({ type: 'subscribe', topic }));
    this.outbox.splice(0).forEach((message) => this.send(message));
    this.startHeartbeat();
  }

  private settle(id: string, finish: (pending: Pending) => void): void {
    const pending = this.pending.get(id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(id);
    finish(pending);
  }

  private handleClose(socket: WebSocket): void {
    if (socket !== this.socket) return; // a socket we already replaced
    this.socket = null;
    this.clearTimers();
    this.failSent();
    if (this.stopped) {
      this.setState('closed');
      return;
    }
    this.setState('connecting');
    this.reconnectTimer = setTimeout(() => this.open(), this.backoffDelay());
    this.attempt += 1;
  }

  // Equal jitter: half the exponential delay fixed, half random, capped.
  private backoffDelay(): number {
    const exponential = Math.min(this.options.backoffMaxMs, this.options.backoffInitialMs * 2 ** this.attempt);
    return exponential / 2 + this.options.random() * (exponential / 2);
  }

  private startHeartbeat(): void {
    this.lastFrameAt = Date.now();
    this.heartbeatTimer = setInterval(() => {
      if (Date.now() - this.lastFrameAt > 2 * this.options.heartbeatMs) {
        this.socket?.close(4000); // silent connection: drop it and reconnect
        return;
      }
      this.send({ type: 'ping' });
    }, this.options.heartbeatMs);
  }

  // Requests already on the wire die with the socket; requests still in the outbox wait for the next one.
  private failSent(): void {
    const waiting = new Set(this.outbox.map((message) => (message.type === 'request' ? message.id : '')));
    this.pending.forEach((pending, id) => {
      if (waiting.has(id)) return;
      clearTimeout(pending.timer);
      this.pending.delete(id);
      pending.reject(new ConnectionLostError(pending.method));
    });
  }

  private failPending(): void {
    this.outbox.length = 0;
    this.failSent();
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
  }

  private setState(state: ConnectionState): void {
    if (state === this.stateValue) return;
    this.stateValue = state;
    this.stateListeners.forEach((listener) => listener(state));
  }
}
