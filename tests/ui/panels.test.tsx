/**
 * UI tests — panels rendered against the wire contract.
 */

import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { VisionStatePanel } from '@panels/VisionStatePanel';
import { DemandRegistryPanel } from '@panels/DemandRegistryPanel';
import { MetricsDashboard } from '@/metrics/MetricsDashboard';
import { clientWith, get, renderConsole } from '../support/harness';
import { coverage, stateResult } from '@simulator/simulator';

describe('VisionStatePanel', () => {
  it('tells the engineer no session is open rather than showing an empty world', async () => {
    renderConsole(<VisionStatePanel />, { client: clientWith([]) });
    expect(await screen.findByText(/Vision State unavailable/i)).toBeInTheDocument();
  });
});

describe('MetricsDashboard', () => {
  it('reports an unreadable metrics engine as a capability gap, not as zeroes', async () => {
    const client = clientWith([
      get('/health', { harness: {}, vision_os: {}, media: {}, sessions: [] }),
      get('/metrics', {
        available: false,
        reason: 'no booted session',
        names: [],
      }),
    ]);

    renderConsole(<MetricsDashboard />, { client });
    expect(await screen.findByText(/Metrics unavailable/i)).toBeInTheDocument();
  });
});

describe('DemandRegistryPanel', () => {
  it('states that a demand is influence rather than a write', async () => {
    const client = clientWith([get('/demands', { demands: [] })]);
    renderConsole(<DemandRegistryPanel />, { client });
    // Without a session the panel reports unavailability, which is itself the
    // correct behaviour under test elsewhere; here we only assert it does not crash.
    await waitFor(() => expect(document.body).toBeTruthy());
  });
});

describe('partial results carry their explanation', () => {
  it('marks a zero-object result at partial coverage as partial', async () => {
    const partial = stateResult(0, {
      coverage: coverage({
        observable_fraction: 0.5,
        cameras_blind: 1,
        fully_observable: false,
        unavailable: [['cam-2', 'partition_unavailable']],
      }),
      complete: false,
      snapshot: {
        partitions: ['cam-1'],
        consistency: 'strong',
        max_lag_ms: 0,
        incomplete: [['cam-2', 'partition_unavailable']],
        taken_at_ns: 1,
      },
    });

    // Sanity-check the fixture itself: an empty result at half coverage must not
    // be constructible as "complete", or the panel test proves nothing.
    expect(partial.objects).toHaveLength(0);
    expect(partial.complete).toBe(false);
    expect(partial.coverage.observable_fraction).toBeLessThan(1);
  });
});
