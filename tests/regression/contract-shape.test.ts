/**
 * Regression tests — the contract's shape, pinned.
 *
 * These fail when a future change quietly drops a field the console depends on,
 * or makes a mandatory field optional. They are the reason this repo can remain
 * the permanent validation tool across Vision OS releases: a v1.1 that stops
 * sending `coverage` breaks a test here rather than silently rendering a blank
 * badge in production.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CHANNELS, SCENARIOS } from '@contract/types';
import { MAX_WINDOW_NS, replayWindow } from '@state/queries';
import { coverage, observation, session, stateResult } from '@simulator/simulator';

describe('mandatory fields stay mandatory', () => {
  it('a state result always carries coverage and a snapshot', () => {
    const result = stateResult(1);
    expect(result.coverage).toBeDefined();
    expect(result.snapshot).toBeDefined();
    expect(result.snapshot.partitions.length).toBeGreaterThan(0);
    expect(typeof result.coverage.observable_fraction).toBe('number');
  });

  it('coverage always distinguishes observing, degraded and blind', () => {
    const summary = coverage();
    expect(summary).toHaveProperty('cameras_observing');
    expect(summary).toHaveProperty('cameras_degraded');
    expect(summary).toHaveProperty('cameras_blind');
    // Collapsing degraded into blind would erase the difference between
    // "seeing less" and "not seeing" — V9 and V8 respectively.
    expect(summary.cameras_degraded).not.toBeUndefined();
  });

  it('an object view carries a platform-derived is_stale', () => {
    const view = stateResult(1).objects[0]!;
    expect(typeof view.is_stale).toBe('boolean');
    expect(view).toHaveProperty('last_confirmed_ns');
    expect(view).toHaveProperty('last_seen_ns');
  });

  it('an observation carries provenance by default', () => {
    // V4 makes provenance non-optional in practice; the fixture defaults to
    // present so that its absence must be deliberately opted into.
    expect(observation(1).provenance).toBeDefined();
  });

  it('a session states its semantics and determinism explicitly', () => {
    const described = session();
    expect(['archival', 'realtime']).toContain(described.semantics);
    expect(typeof described.deterministic).toBe('boolean');
    expect(typeof described.events_attached).toBe('boolean');
  });
});

describe('vocabularies stay closed', () => {
  it('declares exactly the fifteen tap channels', () => {
    expect(CHANNELS).toHaveLength(15);
    for (const required of [
      'camera',
      'acquisition',
      'detection',
      'tracking',
      'registry',
      'cropping',
      'understanding',
      'synthesis',
      'state',
      'observation',
      'demand',
      'metrics',
      'health',
      'event',
      'transport',
    ]) {
      expect(CHANNELS).toContain(required);
    }
  });

  it('declares exactly the eleven validation scenarios', () => {
    expect(SCENARIOS).toHaveLength(11);
    for (const required of [
      'blur',
      'low_light',
      'rain',
      'occlusion',
      'camera_disconnect',
      'duplicate_frames',
      'dropped_frames',
      'freeze',
      'slow_camera',
      'restart',
      'network_delay',
    ]) {
      expect(SCENARIOS).toContain(required);
    }
  });
});

describe('query windows match the session clock domain', () => {
  it('stays inside the platform policy bound for a huge replay', () => {
    // The first regression: `0 → Date.now()` is 56 years against a 24-hour
    // bound, so every observation query failed with WINDOW_TOO_LARGE.
    const window = replayWindow(10_000_000, 1);
    expect(window.end_ns - window.start_ns).toBeLessThanOrEqual(MAX_WINDOW_NS);
  });

  it('covers the whole replay for an ordinary session', () => {
    // 600 frames at 12 fps = 50 s of virtual time. The window must contain it.
    const window = replayWindow(600, 12);
    const spanNs = (600 / 12) * 1_000_000_000;
    expect(window.end_ns).toBeGreaterThan(spanNs);
  });

  it('anchors at zero because the session clock starts at zero', () => {
    // The second, worse regression: a wall-clock window returned 200 with zero
    // results forever, because observations are timestamped on a VirtualClock
    // that starts at the epoch. An empty page meaning "wrong century" is
    // indistinguishable from "nothing was observed" — exactly the conflation V8
    // forbids.
    expect(replayWindow(600, 12).start_ns).toBe(0);

    const source = readFileSync(
      join(__dirname, '..', '..', 'src', 'state', 'queries.ts'),
      'utf8',
    );
    expect(source).not.toMatch(/end_ns:\s*Date\.now/);
  });

  it('never produces an inverted or empty window', () => {
    for (const [frames, fps] of [[0, 12], [1, 1], [50_000, 25], [10, 0.1]] as const) {
      const window = replayWindow(frames, fps);
      expect(window.end_ns).toBeGreaterThan(window.start_ns);
    }
  });
});

describe('time is nanoseconds, everywhere', () => {
  it('names every instant field with an _ns suffix', () => {
    const view = stateResult(1).objects[0]!;
    const instantFields = Object.keys(view).filter((key) =>
      ['first_seen', 'last_seen', 'last_confirmed'].some((stem) => key.startsWith(stem)),
    );
    expect(instantFields.length).toBe(3);
    for (const field of instantFields) expect(field.endsWith('_ns')).toBe(true);
  });

  it('holds instants as integers', () => {
    const view = stateResult(1).objects[0]!;
    expect(Number.isInteger(view.first_seen_ns)).toBe(true);
    expect(Number.isInteger(view.last_seen_ns)).toBe(true);
  });
});
