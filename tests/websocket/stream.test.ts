/**
 * WebSocket tests.
 *
 * The load-bearing assertion is gap detection. A console that silently absorbs a
 * sequence hole cannot certify a platform whose central delivery guarantee is
 * that loss is always announced (V8).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TapBuffer, TapStream } from '@transport/stream';
import { replayStream, streamWithSequenceHole, tap, resetSequence } from '@simulator/simulator';
import { FakeSocket } from '../support/harness';

describe('TapStream sequencing', () => {
  beforeEach(() => {
    FakeSocket.reset();
    resetSequence();
  });

  it('delivers an in-order stream without reporting a gap', () => {
    const seen: number[] = [];
    const gaps: unknown[] = [];
    const stream = new TapStream('s-1', {
      onMessage: (m) => seen.push(m.seq),
      onGap: (g) => gaps.push(g),
    });

    for (const message of replayStream(6)) stream.ingest(message);

    expect(seen.length).toBeGreaterThan(0);
    expect(gaps).toHaveLength(0);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  });

  it('detects a sequence hole and announces it as a gap', () => {
    const gaps: Array<{ seq_from?: number; seq_to?: number; recoverable: boolean }> = [];
    const stream = new TapStream('s-1', { onGap: (g) => gaps.push(g) });

    for (const message of streamWithSequenceHole()) stream.ingest(message);

    expect(gaps.length).toBeGreaterThan(0);
    const gap = gaps[0]!;
    // The console must name WHICH messages were lost, not merely that some were.
    expect(gap.seq_from).toBeDefined();
    expect(gap.seq_to).toBeDefined();
    expect(gap.recoverable).toBe(true);
  });

  it('reports recoverability from the field, never from the reason string', () => {
    const gaps: Array<{ recoverable: boolean; reason: string }> = [];
    const stream = new TapStream('s-1', { onGap: (g) => gaps.push(g) });

    stream.ingest(
      tap('observation', 'gap', {
        start_ns: 0,
        end_ns: 1,
        // A reason whose NAME suggests recoverable, with the field saying otherwise.
        // The client must believe the field.
        reason: 'slow_consumer',
        recoverable: false,
        observations_missed: 4,
      }),
    );

    expect(gaps[0]!.recoverable).toBe(false);
  });

  it('does not treat a heartbeat as advancing the sequence', () => {
    const gaps: unknown[] = [];
    const stream = new TapStream('s-1', { onGap: (g) => gaps.push(g) });

    stream.ingest(tap('transport', 'session.state', { state: 'playing' }));
    stream.ingest({
      seq: 999,
      ts_ns: 1,
      channel: 'transport',
      type: 'heartbeat',
      payload: { cursor: '999' },
    });
    stream.ingest(tap('transport', 'session.state', { state: 'paused' }));

    // A heartbeat carrying a far-future cursor must not manufacture a gap.
    expect(gaps).toHaveLength(0);
  });

  it('treats an unparseable frame as loss rather than ignoring it', () => {
    const gaps: unknown[] = [];
    const stream = new TapStream('s-1', {
      onGap: (g) => gaps.push(g),
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    });
    stream.connect();
    const socket = FakeSocket.instances[0]!;
    socket.open();
    socket.emitRaw('{not json');

    expect(gaps).toHaveLength(1);
  });

  it('reconnects after an unexpected close but not after an explicit one', () => {
    vi.useFakeTimers();
    const statuses: string[] = [];
    const stream = new TapStream('s-1', {
      onStatus: (s) => statuses.push(s),
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    });

    stream.connect();
    FakeSocket.instances[0]!.open();
    FakeSocket.instances[0]!.close();
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances.length).toBeGreaterThan(1);

    const before = FakeSocket.instances.length;
    stream.close();
    vi.advanceTimersByTime(10_000);
    expect(FakeSocket.instances.length).toBe(before);

    vi.useRealTimers();
  });

  it('carries since_seq on reconnect so the console resumes rather than restarting blind', () => {
    const stream = new TapStream('s-1', {
      sinceSeq: 42,
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
    });
    stream.connect();
    expect(FakeSocket.instances[0]!.url).toContain('since_seq=42');
  });
});

describe('TapBuffer', () => {
  it('is bounded and reports what it evicted', () => {
    const buffer = new TapBuffer(10);
    resetSequence();
    for (let i = 0; i < 25; i += 1) {
      buffer.push(tap('detection', 'layer.tap', { i }));
    }

    expect(buffer.get('detection')).toHaveLength(10);
    // The bound must be visible: a panel showing 10 of 25 has to be able to say so.
    expect(buffer.stats().evicted).toBeGreaterThan(0);
    expect(buffer.stats().total).toBe(25);
  });

  it('keeps channels separate', () => {
    const buffer = new TapBuffer(100);
    resetSequence();
    buffer.push(tap('detection', 'layer.tap', {}));
    buffer.push(tap('tracking', 'layer.tap', {}));
    buffer.push(tap('tracking', 'layer.tap', {}));

    expect(buffer.get('detection')).toHaveLength(1);
    expect(buffer.get('tracking')).toHaveLength(2);
    expect(buffer.get('registry')).toHaveLength(0);
  });
});
