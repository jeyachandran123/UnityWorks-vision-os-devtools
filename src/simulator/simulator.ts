/**
 * A deterministic contract simulator.
 *
 * **Test and development only.** `tests/architecture/no-simulated-facts.test.ts`
 * asserts no module under `src/` outside this folder imports it, and that the
 * production bundle does not contain it. The console must never be able to show
 * a fabricated observation as if it came from Vision OS — a validation tool that
 * could do that is worse than no tool.
 *
 * It exists for three reasons:
 *
 * 1. Every test runs without a Python process, a codec, or a GPU.
 * 2. Contract drift is caught: the simulator emits shapes from
 *    `docs/CONTRACT.md`, so a console change that stops handling a documented
 *    field fails a test rather than a demo.
 * 3. Gap and unavailability paths are reachable on demand. Those are the hardest
 *    states to produce against a healthy platform and the most important ones to
 *    render correctly.
 */

import type {
  Channel,
  CoverageSummary,
  MediaAsset,
  Observation,
  SessionDescription,
  StateResult,
  TapMessage,
} from '@contract/types';

/** A tiny deterministic PRNG. Seeded, so two runs produce identical streams. */
export function rng(seed = 1): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state / 0xffffffff;
  };
}

let sequence = 0;

export function resetSequence(): void {
  sequence = 0;
}

export function tap<P extends Record<string, unknown>>(
  channel: Channel,
  type: TapMessage['type'],
  payload: P,
  frameIndex?: number,
): TapMessage {
  sequence += 1;
  return {
    seq: sequence,
    ts_ns: 1_000_000_000 + sequence * 8_000_000,
    channel,
    type,
    payload,
    ...(frameIndex === undefined ? {} : { frame_index: frameIndex }),
  };
}

export function platformEvent(eventType: string, detail: Record<string, unknown> = {}): TapMessage {
  const channel = eventType.split('.')[0] ?? 'event';
  const mapped: Record<string, Channel> = {
    stream: 'acquisition',
    scheduler: 'acquisition',
    buffer: 'acquisition',
    privacy: 'acquisition',
    detection: 'detection',
    tracking: 'tracking',
    registry: 'registry',
    cropping: 'cropping',
    understanding: 'understanding',
    synthesis: 'synthesis',
    state: 'state',
    health: 'health',
    camera: 'camera',
    runtime: 'transport',
    model: 'transport',
    config: 'transport',
    plugin: 'transport',
    bus: 'transport',
  };
  return tap(mapped[channel] ?? 'event', 'event', { event_type: eventType, ...detail });
}

export function coverage(overrides: Partial<CoverageSummary> = {}): CoverageSummary {
  return {
    observable_fraction: 1,
    cameras_observing: 1,
    cameras_blind: 0,
    cameras_degraded: 0,
    unavailable: [],
    fully_observable: true,
    ...overrides,
  };
}

export function observation(index: number, overrides: Partial<Observation> = {}): Observation {
  return {
    observation_id: `obs-${String(index).padStart(6, '0')}`,
    observation_type: 'object_state',
    camera_id: 'cam-validation',
    object_id: `obj-${String(index % 4).padStart(4, '0')}`,
    class_id: 'person',
    t_capture_ns: 1_000_000_000 + index * 83_000_000,
    confidence: { value: 0.9, calibrated: true },
    attributes: [],
    // Present by default: V4 requires it, so its ABSENCE must be the thing a
    // test opts into rather than the thing it forgets to add.
    provenance: { model_id: 'reference-detector', model_version: '1.0.0' },
    supersedes: null,
    tenant_id: 't-eng',
    ...overrides,
  };
}

export function stateResult(objectCount = 3, overrides: Partial<StateResult> = {}): StateResult {
  return {
    objects: Array.from({ length: objectCount }, (_, i) => ({
      object_id: `obj-${String(i).padStart(4, '0')}`,
      class_id: 'person',
      class_confidence: { value: 0.91, calibrated: true },
      lifecycle: 'active' as const,
      camera_id: 'cam-validation',
      first_seen_ns: 1_000_000_000,
      last_seen_ns: 1_500_000_000,
      last_confirmed_ns: 1_500_000_000,
      is_stale: false,
      attributes: {},
      spatial: { bbox: { x1: 0.3, y1: 0.1, x2: 0.6, y2: 0.9 } },
      trajectory: [],
      provenance: { model_id: 'reference-detector' },
      observation_count: 5,
    })),
    snapshot: {
      partitions: ['cam-validation'],
      consistency: 'strong',
      max_lag_ms: 0,
      incomplete: [],
      taken_at_ns: 1_500_000_000,
    },
    coverage: coverage(),
    complete: true,
    cursor: null,
    ...overrides,
  };
}

export function session(overrides: Partial<SessionDescription> = {}): SessionDescription {
  return {
    session_id: 's-simulated',
    state: 'paused',
    error: null,
    media_id: 'm-synthetic',
    media_name: 'synthetic-moving-target',
    camera_id: 'cam-validation',
    tenant_id: 't-eng',
    semantics: 'archival',
    target_fps: 12,
    deterministic: true,
    rtsp: false,
    frame_count: 60,
    frame_index: 12,
    playing: false,
    speed: 1,
    exhausted: false,
    created_at_ns: 1_000_000_000,
    events_attached: true,
    events_unavailable_reason: null,
    taps: { sequence, dropped: 0, subscribers: 1, by_channel: {} },
    faults: [],
    ...overrides,
  };
}

export function media(overrides: Partial<MediaAsset> = {}): MediaAsset {
  return {
    media_id: 'm-synthetic',
    name: 'synthetic-moving-target',
    kind: 'synthetic',
    path: null,
    usable: true,
    error: null,
    created_at_ns: 1_000_000_000,
    probe: {
      frame_count: 60,
      width: 96,
      height: 96,
      fps: 25,
      duration_ms: 2400,
      backend: 'synthetic',
      seekable: true,
    },
    ...overrides,
  };
}

/**
 * A full replay's worth of tap messages, in pipeline order.
 *
 * Ordered deliberately: acquisition before detection before tracking before
 * registry. The ordering test asserts the console notices when that order is
 * violated, so the happy-path fixture must be genuinely ordered or the test
 * would pass for the wrong reason.
 */
export function replayStream(frames = 12): TapMessage[] {
  resetSequence();
  const messages: TapMessage[] = [];

  messages.push(platformEvent('runtime.pipeline_attached', { camera_id: 'cam-validation' }));
  messages.push(platformEvent('stream.connected', { camera_id: 'cam-validation', stream_epoch: 1 }));

  for (let frame = 0; frame < frames; frame += 1) {
    messages.push(
      tap(
        'acquisition',
        'layer.tap',
        {
          frame_index: frame,
          pts_ms: frame * 83,
          width: 96,
          height: 96,
          bytes: 96 * 96 * 3,
          is_keyframe: true,
          faults: [],
          emitted_at_ns: 1_000_000_000 + frame * 83_000_000,
        },
        frame,
      ),
    );
    messages.push(
      platformEvent('detection.completed', {
        camera_id: 'cam-validation',
        detection_count: 1,
        inference_ms: 2.4,
        batch_size: 1,
        model_id: 'reference-detector',
      }),
    );
    messages.push(
      platformEvent('tracking.track_updated', {
        camera_id: 'cam-validation',
        track_id: 'cam-validation/1/#1',
        state: 'confirmed',
        association_confidence: 0.95,
        measurement_basis: 'measured',
      }),
    );
    if (frame === 2) {
      messages.push(
        platformEvent('registry.object_created', {
          camera_id: 'cam-validation',
          object_id: 'obj-0000',
          track_id: 'cam-validation/1/#1',
          class_id: 'person',
        }),
      );
    }
    if (frame >= 2 && frame % 3 === 0) {
      messages.push(tap('observation', 'observation', observation(frame) as never, frame));
    }
  }

  return messages;
}

/** A stream with a deliberate sequence hole, for gap-detection tests. */
export function streamWithSequenceHole(): TapMessage[] {
  const messages = replayStream(6);
  // Remove one message without renumbering: the hole is the point.
  return messages.filter((_, index) => index !== 8);
}
