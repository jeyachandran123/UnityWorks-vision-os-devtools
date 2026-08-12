/**
 * Integration tests over the wire contract.
 *
 * These drive the real `ValidationClient` against a fake that speaks
 * `docs/CONTRACT.md`. They exist to catch the failure mode a unit test cannot:
 * the console handling a *documented* response shape incorrectly.
 */

import { describe, expect, it } from 'vitest';
import { ValidationClient } from '@transport/client';
import { TransportError } from '@transport/errors';
import { clientWith, get, post } from '../support/harness';
import { coverage, media, session, stateResult } from '@simulator/simulator';

describe('ValidationClient', () => {
  it('sends the accepted major on every request', async () => {
    const seen: RequestInit[] = [];
    const client = new ValidationClient({
      fetchImpl: (async (_url: string, init?: RequestInit) => {
        seen.push(init!);
        return new Response(JSON.stringify({ sessions: [] }), { status: 200 });
      }) as unknown as typeof fetch,
    });

    await client.listSessions();
    const headers = seen[0]!.headers as Record<string, string>;
    expect(headers['X-VOS-Accept-Major']).toBe('1');
  });

  it('surfaces the platform error envelope with its stable code', async () => {
    const client = clientWith([
      post('/state/query', { code: 'FORBIDDEN', message: 'nope', retryable: false }, 403),
    ]);

    await expect(client.queryState()).rejects.toThrow(TransportError);
    await client.queryState().catch((error: TransportError) => {
      expect(error.code).toBe('FORBIDDEN');
      expect(error.retryable).toBe(false);
    });
  });

  it('retries only when the platform says the error is retryable', async () => {
    let calls = 0;
    const client = new ValidationClient({
      fetchImpl: (async () => {
        calls += 1;
        if (calls < 3) {
          return new Response(
            JSON.stringify({ code: 'OVERLOADED', message: 'slow down', retryable: true, retry_after_ms: 1 }),
            { status: 429 },
          );
        }
        return new Response(JSON.stringify({ sessions: [] }), { status: 200 });
      }) as unknown as typeof fetch,
    });

    await client.listSessions();
    expect(calls).toBe(3);
  });

  it('does not retry a non-retryable error however many times it is offered', async () => {
    let calls = 0;
    const client = new ValidationClient({
      fetchImpl: (async () => {
        calls += 1;
        return new Response(
          JSON.stringify({ code: 'FORBIDDEN', message: 'no', retryable: false }),
          { status: 403 },
        );
      }) as unknown as typeof fetch,
    });

    await expect(client.listSessions()).rejects.toThrow();
    expect(calls).toBe(1);
  });

  it('never retries a demand registration', async () => {
    let calls = 0;
    const client = new ValidationClient({
      fetchImpl: (async () => {
        calls += 1;
        return new Response(
          JSON.stringify({ code: 'OVERLOADED', message: 'busy', retryable: true }),
          { status: 429 },
        );
      }) as unknown as typeof fetch,
    });

    // Registering a demand spends budget and causes computation; retrying one
    // automatically would multiply that spend behind the engineer's back.
    await expect(client.registerDemand({ class_id: 'person' })).rejects.toThrow();
    expect(calls).toBe(1);
  });

  it('treats a 200 carrying an error envelope as an error, not a result', async () => {
    const client = clientWith([
      post('/state/query', { code: 'NOT_FOUND', message: 'no session', retryable: false }),
    ]);

    // "No session yet" must not render as "zero objects". That conflation is
    // exactly what V8 forbids.
    await expect(client.queryState()).rejects.toThrow(TransportError);
  });

  it('round-trips a state result with its coverage intact', async () => {
    const client = clientWith([post('/state/query', stateResult(2))]);
    const result = await client.queryState();

    expect(result.objects).toHaveLength(2);
    expect(result.coverage).toBeDefined();
    expect(result.coverage.observable_fraction).toBe(1);
  });

  it('requires a purpose in the evidence URL', async () => {
    let captured = '';
    const client = new ValidationClient({
      fetchImpl: (async (url: string) => {
        captured = url;
        return new Response(JSON.stringify({ observation_id: 'o1' }), { status: 200 });
      }) as unknown as typeof fetch,
    });

    await client.getEvidence('blob-1', 'validating detector regression');
    expect(captured).toContain('purpose=');
    expect(captured).toContain('validating');
  });

  it('builds a frame URL that carries the declared purpose', () => {
    const client = new ValidationClient({ baseUrl: 'http://h' });
    const url = client.frameUrl('s-1', 42, 'visual inspection');
    expect(url).toContain('/sessions/s-1/frames/42');
    expect(url).toContain('purpose=visual%20inspection');
  });

  it('lists media with its capability gaps attached', async () => {
    const client = clientWith([
      get('/media', {
        media: [media(), media({ media_id: 'm-bad', usable: false, error: 'no backend for .mkv' })],
        capabilities: { backends: ['raw'], containers: {}, images: [], always_available: [] },
      }),
    ]);

    const result = await client.listMedia();
    const unusable = result.media.find((m) => !m.usable);
    // The reason must survive the trip: "unusable" without a why is unactionable.
    expect(unusable?.error).toContain('no backend');
  });

  it('drives transport commands through the documented action vocabulary', async () => {
    const bodies: string[] = [];
    const client = new ValidationClient({
      fetchImpl: (async (_url: string, init?: RequestInit) => {
        bodies.push(String(init?.body));
        return new Response(JSON.stringify(session()), { status: 200 });
      }) as unknown as typeof fetch,
    });

    await client.transport('s-1', 'step', { count: 3 });
    expect(JSON.parse(bodies[0]!)).toEqual({ action: 'step', count: 3 });
  });
});

describe('a missing harness is diagnosed, not mistaken for a platform failure', () => {
  it('names an empty-bodied proxy 500 as HARNESS_UNREACHABLE', async () => {
    // Vite's dev proxy answers exactly this when nothing is listening upstream.
    const client = new ValidationClient({
      fetchImpl: (async () => new Response('', { status: 500 })) as unknown as typeof fetch,
    });

    await client.health().catch((error: TransportError) => {
      expect(error.code).toBe('HARNESS_UNREACHABLE');
      expect(error.retryable).toBe(true);
      expect(error.message).toContain('python -m vosvc_harness');
    });
    expect.assertions(3);
  });

  it('names a refused socket the same way', async () => {
    const client = new ValidationClient({
      fetchImpl: (async () => {
        throw new TypeError('Failed to fetch');
      }) as unknown as typeof fetch,
    });

    await client.health().catch((error: TransportError) => {
      expect(error.code).toBe('HARNESS_UNREACHABLE');
    });
    expect.assertions(1);
  });

  it('does NOT claim unreachable when the platform actually answered', async () => {
    // A 500 carrying a real envelope came from the platform. Relabelling it
    // would send an engineer restarting a harness that is running fine.
    const client = clientWith([
      get('/health', { code: 'INTERNAL', message: 'projection blew up', retryable: false }, 500),
    ]);

    await client.health().catch((error: TransportError) => {
      expect(error.code).toBe('INTERNAL');
      expect(error.message).toContain('projection blew up');
    });
    expect.assertions(2);
  });

  it('reports a non-empty 500 as INTERNAL with the body attached', async () => {
    const client = new ValidationClient({
      fetchImpl: (async () =>
        new Response('<html>gateway exploded</html>', { status: 502 })) as unknown as typeof fetch,
    });

    await client.health().catch((error: TransportError) => {
      expect(error.code).toBe('INTERNAL');
      expect(error.message).toContain('gateway exploded');
    });
    expect.assertions(2);
  });
});

describe('coverage is never fabricated', () => {
  it('passes through a partial-coverage result unchanged', async () => {
    const client = clientWith([
      post(
        '/state/query',
        stateResult(0, {
          coverage: coverage({
            observable_fraction: 0.5,
            cameras_blind: 1,
            fully_observable: false,
            unavailable: [['cam-2', 'partition_unavailable']],
          }),
          complete: false,
        }),
      ),
    ]);

    const result = await client.queryState();
    expect(result.objects).toHaveLength(0);
    // Zero objects at half coverage is NOT "nothing happened".
    expect(result.coverage.observable_fraction).toBe(0.5);
    expect(result.complete).toBe(false);
    expect(result.coverage.unavailable).toHaveLength(1);
  });
});
