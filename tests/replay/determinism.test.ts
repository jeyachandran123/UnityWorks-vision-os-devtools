/**
 * Replay tests.
 *
 * The console cannot make Vision OS deterministic; it can only report the
 * platform's verdict faithfully and refuse to soften it. These tests assert that
 * refusal, and that the console never compares two runs that are not comparable.
 */

import { describe, expect, it } from 'vitest';
import { clientWith, get, post } from '../support/harness';
import { replayStream, resetSequence } from '@simulator/simulator';
import { TapBuffer } from '@transport/stream';

describe('determinism reporting', () => {
  it('passes a mismatch through as a failure, not a warning', async () => {
    const client = clientWith([
      post('/replay/verify', {
        available: true,
        session_id: 's-1',
        reports: [{ camera_id: 'cam-1', mismatches: 3 }],
        mismatches: 3,
        deterministic: false,
      }),
    ]);

    const result = await client.verifyReplay('s-1');
    expect(result.deterministic).toBe(false);
    expect(result.mismatches).toBe(3);
  });

  it('reports a clean verification as deterministic', async () => {
    const client = clientWith([
      post('/replay/verify', {
        available: true,
        reports: [{ camera_id: 'cam-1', mismatches: 0 }],
        mismatches: 0,
        deterministic: true,
      }),
    ]);

    const result = await client.verifyReplay('s-1');
    expect(result.deterministic).toBe(true);
  });

  it('states unavailability rather than claiming determinism when there is no session', async () => {
    const client = clientWith([post('/replay/verify', { available: false, reason: 'no session' })]);
    const result = await client.verifyReplay();

    expect(result.available).toBe(false);
    // Absence of a verification is NOT a passing verification.
    expect(result.deterministic).toBeUndefined();
  });
});

describe('regression comparison refuses meaningless diffs', () => {
  it('refuses to compare sessions with different source semantics', async () => {
    const client = clientWith([
      get('/reports/regression', {
        kind: 'regression',
        available: true,
        comparable: false,
        incomparable_reason:
          'different source semantics (archival protects completeness; realtime permits dropping)',
      }),
    ]);

    const report = await client.report('regression', { baselineSessionId: 's-2' });
    expect(report.comparable).toBe(false);
    expect(String(report.incomparable_reason)).toContain('semantics');
  });

  it('produces a diff without a better/worse judgment', async () => {
    const client = clientWith([
      get('/reports/regression', {
        kind: 'regression',
        available: true,
        comparable: true,
        counts: { baseline: 10, current: 12 },
        only_in_current: ['a', 'b'],
        only_in_baseline: [],
        common: 10,
        note: 'This is a diff, not a judgment.',
      }),
    ]);

    const report = await client.report('regression', { baselineSessionId: 's-2' });
    expect(report.only_in_current).toHaveLength(2);
    // No score, grade, or verdict field may appear.
    expect(report).not.toHaveProperty('score');
    expect(report).not.toHaveProperty('grade');
    expect(String(report.note)).toContain('not a judgment');
  });
});

describe('replay stream determinism', () => {
  it('produces byte-identical tap streams across two runs', () => {
    resetSequence();
    const first = JSON.stringify(replayStream(10));
    resetSequence();
    const second = JSON.stringify(replayStream(10));

    // If the fixture itself were non-deterministic, every determinism test
    // above would be testing noise.
    expect(first).toBe(second);
  });

  it('replays into a buffer in pipeline order', () => {
    resetSequence();
    const buffer = new TapBuffer(1000);
    for (const message of replayStream(8)) buffer.push(message);

    const firstAcq = buffer.get('acquisition')[0]!.seq;
    const firstDet = buffer.get('detection')[0]!.seq;
    const firstTrack = buffer.get('tracking')[0]!.seq;
    const firstReg = buffer.get('registry')[0]!.seq;

    expect(firstAcq).toBeLessThan(firstDet);
    expect(firstDet).toBeLessThan(firstTrack);
    expect(firstTrack).toBeLessThan(firstReg);
  });
});
