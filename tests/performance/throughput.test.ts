/**
 * Performance tests.
 *
 * A validation console for thousands of cameras must not itself become the
 * bottleneck. These bound the two structures every message passes through.
 *
 * The thresholds are generous on purpose: they exist to catch an accidental
 * O(n²) — a `.find()` inside a loop, an array copied per message — not to
 * benchmark a machine. A tight bound here would fail on a loaded CI box and
 * teach everyone to ignore the suite.
 */

import { describe, expect, it } from 'vitest';
import { TapBuffer, TapStream } from '@transport/stream';
import { resetSequence, tap } from '@simulator/simulator';

describe('TapBuffer scales linearly', () => {
  it('ingests 100k messages within a bounded time and memory footprint', () => {
    const buffer = new TapBuffer(5000);
    resetSequence();

    const started = performance.now();
    for (let i = 0; i < 100_000; i += 1) {
      buffer.push(tap('detection', 'layer.tap', { i }));
    }
    const elapsed = performance.now() - started;

    expect(buffer.get('detection')).toHaveLength(5000);
    expect(buffer.stats().total).toBe(100_000);
    // Linear ingest of 100k should be well under a second. Quadratic will not be.
    expect(elapsed).toBeLessThan(4000);
  });

  it('does not degrade as the ring fills', () => {
    const buffer = new TapBuffer(2000);
    resetSequence();

    const time = (count: number) => {
      const started = performance.now();
      for (let i = 0; i < count; i += 1) buffer.push(tap('tracking', 'layer.tap', { i }));
      return performance.now() - started;
    };

    const cold = time(20_000);
    const warm = time(20_000);

    // Once the ring is full every push evicts; if eviction were O(n) the second
    // batch would be dramatically slower than the first.
    expect(warm).toBeLessThan(Math.max(cold * 8, 500));
  });
});

describe('TapStream sequencing is O(1) per message', () => {
  it('processes 50k in-order messages without accumulating gap state', () => {
    const stream = new TapStream('s-1', {});
    resetSequence();

    const started = performance.now();
    for (let i = 1; i <= 50_000; i += 1) {
      stream.ingest({
        seq: i,
        ts_ns: i * 1000,
        channel: 'detection',
        type: 'layer.tap',
        payload: {},
      });
    }
    const elapsed = performance.now() - started;

    expect(stream.gaps).toHaveLength(0);
    expect(elapsed).toBeLessThan(2000);
  });

  it('bounds gap records even under sustained loss', () => {
    const stream = new TapStream('s-1', {});
    let seq = 1;
    for (let i = 0; i < 5000; i += 1) {
      stream.ingest({
        seq,
        ts_ns: seq * 1000,
        channel: 'detection',
        type: 'layer.tap',
        payload: {},
      });
      seq += 3; // every ingest skips two
    }

    // Each hole is one gap record, not one per lost message — otherwise a lossy
    // socket would exhaust memory faster than a healthy one fills the buffer.
    expect(stream.gaps.length).toBeLessThanOrEqual(5000);
    expect(stream.gaps.length).toBeGreaterThan(0);
  });
});
