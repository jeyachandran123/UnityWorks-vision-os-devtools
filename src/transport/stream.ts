/**
 * The tap stream client.
 *
 * Two responsibilities, and it must not acquire a third:
 *
 * 1. Deliver messages in order.
 * 2. **Detect and announce loss.** `seq` is monotonic and gapless per socket, so
 *    receiving 44 after 42 proves 43 was lost. The client synthesizes a local
 *    gap for that — it does not quietly renumber, interpolate, or wait to see
 *    whether 43 shows up late.
 *
 * > 09_API §3.3: *"A subscriber is never silently skipped… This is V8 applied to
 * > delivery."*
 *
 * The console renders every gap as a visible band on the timeline. A validation
 * tool that smoothed over its own message loss could not be trusted to report
 * the platform's.
 */

import type { Channel, GapPayload, TapMessage } from '@contract/types';

export type StreamStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

export interface StreamEvents {
  onMessage?: (message: TapMessage) => void;
  onGap?: (gap: GapPayload & { local: boolean }) => void;
  onStatus?: (status: StreamStatus, detail?: string) => void;
}

export interface StreamOptions extends StreamEvents {
  baseUrl?: string;
  channels?: Channel[];
  sinceSeq?: number;
  /** Bounded. There is no value meaning "unlimited" (09_API §3.4). */
  maxReconnectDelayMs?: number;
  socketFactory?: (url: string) => WebSocket;
}

export class TapStream {
  private socket: WebSocket | null = null;
  private closedByCaller = false;
  private reconnectAttempt = 0;
  private lastSeq: number;
  private timer: ReturnType<typeof setTimeout> | null = null;

  readonly sessionId: string;
  private readonly options: StreamOptions;

  /** Every gap this client has seen — remote and locally detected. */
  readonly gaps: Array<GapPayload & { local: boolean }> = [];

  constructor(sessionId: string, options: StreamOptions = {}) {
    this.sessionId = sessionId;
    this.options = options;
    this.lastSeq = options.sinceSeq ?? 0;
  }

  get status(): StreamStatus {
    if (!this.socket) return 'idle';
    switch (this.socket.readyState) {
      case WebSocket.CONNECTING:
        return 'connecting';
      case WebSocket.OPEN:
        return 'open';
      default:
        return 'closed';
    }
  }

  connect(): void {
    this.closedByCaller = false;
    this.open();
  }

  close(): void {
    this.closedByCaller = true;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.socket?.close();
    this.socket = null;
    this.options.onStatus?.('closed');
  }

  private url(): string {
    const base = this.options.baseUrl ?? '';
    const origin = base
      ? base.replace(/^http/, 'ws')
      : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`;
    const params = new URLSearchParams();
    if (this.options.channels?.length) params.set('channels', this.options.channels.join(','));
    if (this.lastSeq) params.set('since_seq', String(this.lastSeq));
    const suffix = params.toString() ? `?${params}` : '';
    return `${origin}/ws/v1/session/${encodeURIComponent(this.sessionId)}${suffix}`;
  }

  private open(): void {
    this.options.onStatus?.('connecting');
    try {
      const factory = this.options.socketFactory ?? ((u: string) => new WebSocket(u));
      this.socket = factory(this.url());
    } catch (error) {
      this.options.onStatus?.('error', String(error));
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.reconnectAttempt = 0;
      this.options.onStatus?.('open');
    };

    this.socket.onmessage = (event: MessageEvent) => {
      let message: TapMessage;
      try {
        message = JSON.parse(String(event.data)) as TapMessage;
      } catch {
        // A frame we cannot parse is loss, and loss is announced.
        this.recordGap({
          start_ns: 0,
          end_ns: 0,
          reason: 'slow_consumer',
          recoverable: true,
          observations_missed: 1,
          seq_from: this.lastSeq + 1,
          seq_to: this.lastSeq + 1,
        });
        return;
      }
      this.ingest(message);
    };

    this.socket.onerror = () => this.options.onStatus?.('error');

    this.socket.onclose = () => {
      this.options.onStatus?.('closed');
      if (!this.closedByCaller) this.scheduleReconnect();
    };
  }

  /** Exposed for tests: the same path a socket message takes. */
  ingest(message: TapMessage): void {
    if (message.type === 'gap') {
      this.recordGap({ ...(message.payload as unknown as GapPayload) });
      this.lastSeq = Math.max(this.lastSeq, message.seq);
      this.options.onMessage?.(message);
      return;
    }

    // Heartbeats carry the current cursor without advancing it; they are the
    // liveness signal that lets a quiet scene be told from a dead connection.
    if (message.type !== 'heartbeat' && message.seq > 0) {
      const expected = this.lastSeq + 1;
      if (this.lastSeq > 0 && message.seq > expected) {
        this.recordGap({
          start_ns: 0,
          end_ns: message.ts_ns,
          reason: 'slow_consumer',
          recoverable: true,
          observations_missed: message.seq - expected,
          seq_from: expected,
          seq_to: message.seq - 1,
        });
      }
      this.lastSeq = Math.max(this.lastSeq, message.seq);
    }

    this.options.onMessage?.(message);
  }

  private recordGap(gap: GapPayload, local = true): void {
    const entry = { ...gap, local };
    this.gaps.push(entry);
    this.options.onGap?.(entry);
  }

  private scheduleReconnect(): void {
    if (this.closedByCaller) return;
    const ceiling = this.options.maxReconnectDelayMs ?? 8000;
    const delay = Math.min(500 * 2 ** this.reconnectAttempt, ceiling);
    this.reconnectAttempt += 1;
    this.timer = setTimeout(() => this.open(), delay);
  }
}

/**
 * A bounded ring of tap messages per channel.
 *
 * Bounded because a soak session produces millions of messages and a console
 * that kept them all would die at exactly the moment the engineer needed it.
 * The bound is stated in the UI rather than hidden — an inspector showing the
 * last 5,000 of 900,000 messages must say so.
 */
export class TapBuffer {
  private readonly byChannel = new Map<Channel, TapMessage[]>();
  private total = 0;
  private evicted = 0;

  constructor(readonly capacityPerChannel = 5000) {}

  push(message: TapMessage): void {
    const held = this.byChannel.get(message.channel) ?? [];
    held.push(message);
    if (held.length > this.capacityPerChannel) {
      held.splice(0, held.length - this.capacityPerChannel);
      this.evicted += 1;
    }
    this.byChannel.set(message.channel, held);
    this.total += 1;
  }

  get(channel: Channel): TapMessage[] {
    return this.byChannel.get(channel) ?? [];
  }

  latest(channel: Channel): TapMessage | undefined {
    const held = this.get(channel);
    return held[held.length - 1];
  }

  counts(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [channel, held] of this.byChannel) out[channel] = held.length;
    return out;
  }

  stats(): { total: number; evicted: number; capacityPerChannel: number } {
    return { total: this.total, evicted: this.evicted, capacityPerChannel: this.capacityPerChannel };
  }

  clear(): void {
    this.byChannel.clear();
    this.total = 0;
    this.evicted = 0;
  }
}
