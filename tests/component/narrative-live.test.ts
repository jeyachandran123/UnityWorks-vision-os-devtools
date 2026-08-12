/**
 * Golden-output test: the narrative for a record shaped exactly like the ones
 * Vision OS actually produced in a live session.
 *
 * Pinned as an exact string rather than as `toContain` assertions. A prose
 * renderer drifts silently — a comma here, a re-ordered qualifier there — and
 * "roughly the same sentence" is not a thing a regression report can rest on.
 * If this test fails, the wording changed, and that should be a decision rather
 * than an accident.
 */

import { describe, expect, it } from 'vitest';
import { renderObservation } from '@/narrative/render';
import type { Observation } from '@contract/types';

/** Copied from a real `presence` record served by the harness. */
const LIVE_PRESENCE = {
  observation_id: '0000000056GJC5859WP0A9B13N',
  observation_type: 'presence',
  tenant_id: 't-eng',
  site_id: 'site-eng',
  camera_id: 'cam-validation',
  frame_ref: { camera_id: 'cam-validation', stream_epoch: 1, frame_seq: 1 },
  t_capture_ns: 83333333,
  t_capture_unc_ms: 5000.0,
  t_published_ns: 166666666,
  provenance: {
    producer_module: 'observation_builder',
    producer_version: '1.0.0',
    adapter_id: null,
    model_id: null,
    deterministic: false,
  },
  object_id: '000000002KSKHYK0CYMV95MX78',
  track_id: null,
  class_id: 'person',
  lifecycle_state: 'provisional',
  confidence: { value: 1.0, semantics: 'identity', calibrated: false, raw_score: 1.0 },
  spatial: {
    frame_of_reference: 'normalized',
    bbox: { x1: 0.3, y1: 0.15, x2: 0.62, y2: 0.9 },
  },
  attributes: [],
  measurement_basis: 'measured',
  evidence_ref: null,
  supersedes: null,
  schema_version: '1.0.0',
} as unknown as Observation;

/** Copied from a real `attribute` record, after the attention path was fixed. */
const LIVE_ATTRIBUTE = {
  observation_id: '00000000FK42RQ18WZ1F2XGKRN',
  observation_type: 'attribute',
  camera_id: 'cam-validation',
  t_capture_ns: 366666665,
  object_id: '000000002KA01W26KJSZBYSHX9',
  class_id: 'person',
  lifecycle_state: 'active',
  measurement_basis: 'measured',
  attributes: [
    {
      key: 'posture',
      value: 'standing',
      confidence: { value: 0.95, semantics: 'self_reported', calibrated: false },
      observed_at_ns: 366666665,
      evidence_ref: '00000000FK8RZHG2F0KQ2DF305',
    },
  ],
  provenance: {
    producer_module: 'understanding_engine',
    adapter_id: 'attr.static_head',
    model_id: null,
  },
  evidence_ref: '00000000FK8RZHG2F0KQ2DF305',
  schema_version: '1.0.0',
} as unknown as Observation;

describe('golden narratives', () => {
  it('renders a live presence record exactly', () => {
    const { sentence } = renderObservation(LIVE_PRESENCE);
    // Object ids are shown as the last 8 characters, record and evidence ids as
    // the last 10 — enough to pick one out of a list, short enough to read in
    // prose. The full value is always one click away in Raw JSON.
    expect(sentence).toBe(
      'At +83.3 ms, camera cam-validation reported an object present; ' +
        'the object is classified a person; object …MV95MX78; ' +
        'occupying x 0.300–0.620, y 0.150–0.900 in normalized coordinates. ' +
        '[capture time is stated ±5.000 s; class confidence 1.000 (identity, uncalibrated); ' +
        'lifecycle provisional; basis measured; frame cam-validation/e1/f1; ' +
        'produced by observation_builder; record …9WP0A9B13N]',
    );
  });

  it('states every caveat a presence record earns', () => {
    const { caveats } = renderObservation(LIVE_PRESENCE);
    expect(caveats).toHaveLength(3);
    expect(caveats.join(' ')).toContain('not comparable across models');
    expect(caveats.join(' ')).toContain('lifecycle is provisional');
    expect(caveats.join(' ')).toContain('No evidence reference');
  });

  it('renders a live attribute record exactly', () => {
    const { sentence } = renderObservation(LIVE_ATTRIBUTE);
    expect(sentence).toBe(
      'At +366.7 ms, camera cam-validation reported an attribute; ' +
        'the object is classified a person; object …SZBYSHX9; ' +
        'posture = "standing", confidence 0.950 (self_reported, uncalibrated), evidence …F0KQ2DF305. ' +
        '[lifecycle active; basis measured; ' +
        'produced by understanding_engine · attr.static_head; ' +
        'evidence …F0KQ2DF305; record …WZ1F2XGKRN]',
    );
  });

  it('names the adapter that produced the attribute', () => {
    // 06_PORTS: a static head and a VLM are indistinguishable downstream
    // *"except by reading the provenance that says so."* The narrative says so.
    expect(renderObservation(LIVE_ATTRIBUTE).sentence).toContain('attr.static_head');
  });

  it('speaks no field it was not given', () => {
    const { renderedFields } = renderObservation(LIVE_PRESENCE);
    const present = new Set(Object.keys(LIVE_PRESENCE as unknown as Record<string, unknown>));
    for (const field of renderedFields) {
      expect(present.has(field)).toBe(true);
    }
  });
});
