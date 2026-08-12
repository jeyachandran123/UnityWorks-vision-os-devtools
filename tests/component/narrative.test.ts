/**
 * Narrative renderer tests.
 *
 * Two categories, and the second matters more than the first:
 *
 * 1. **It renders correctly** — the sentence says what the record says.
 * 2. **It cannot do anything else** — no inference, no thresholds, no merging,
 *    no silent omission, no drift between renders.
 *
 * Prose is persuasive in a way JSON is not. A reader trusts a fluent sentence
 * more than it has earned, so the renderer has to be provably incapable of
 * earning that trust dishonestly.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { formatOffset, renderObservation } from '@/narrative/render';
import { observation } from '@simulator/simulator';
import type { Observation } from '@contract/types';

const SOURCE = readFileSync(join(__dirname, '..', '..', 'src', 'narrative', 'render.ts'), 'utf8');

/**
 * The source with comments removed.
 *
 * The determinism scan must look at code, not at the documentation explaining
 * the rule — `render.ts` says "No `Date.now()`" in its own header, and matching
 * that would fail the file for stating its own contract.
 */
const CODE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

function presence(overrides: Partial<Observation> = {}): Observation {
  return observation(1, {
    observation_type: 'presence',
    camera_id: 'cam-validation',
    class_id: 'person',
    t_capture_ns: 366_666_665,
    lifecycle_state: 'active',
    measurement_basis: 'measured',
    confidence: { value: 0.92, calibrated: true, semantics: 'detection' },
    spatial: { frame_of_reference: 'normalized', bbox: { x1: 0.3, y1: 0.15, x2: 0.62, y2: 0.9 } },
    evidence_ref: 'ev-01HXABCDEF',
    ...overrides,
  } as Partial<Observation>);
}

describe('it renders what the record says', () => {
  it('speaks the capture time, camera and type', () => {
    const { sentence } = renderObservation(presence());
    expect(sentence).toContain('+366.7 ms');
    expect(sentence).toContain('cam-validation');
    expect(sentence).toContain('reported an object present');
  });

  it('speaks the class with correct grammar', () => {
    expect(renderObservation(presence()).sentence).toContain('a person');
    expect(
      renderObservation(presence({ class_id: 'animal' } as Partial<Observation>)).sentence,
    ).toContain('an animal');
  });

  it('renders an attribute with its value and evidence', () => {
    const attributed = observation(2, {
      observation_type: 'attribute',
      class_id: 'person',
      attributes: [
        {
          key: 'posture',
          value: 'standing',
          confidence: { value: 0.95, calibrated: false, semantics: 'self_reported' },
          evidence_ref: 'ev-FK42RQ18WZ',
        },
      ],
    } as Partial<Observation>);

    const { sentence } = renderObservation(attributed);
    expect(sentence).toContain('posture = "standing"');
    expect(sentence).toContain('0.950');
    expect(sentence).toContain('ev-FK42RQ18WZ'.slice(-10));
  });

  it('renders a lifecycle transition in both directions', () => {
    const record = observation(3, {
      observation_type: 'lifecycle',
      lifecycle_transition: { previous: 'provisional', current: 'active', trigger: 'confirmed' },
    } as Partial<Observation>);
    const { sentence } = renderObservation(record);
    expect(sentence).toContain('from provisional to active');
    expect(sentence).toContain('triggered by confirmed');
  });

  it('publishes unresolved identity alternatives rather than hiding them', () => {
    const record = observation(4, {
      observation_type: 'identity',
      identity: { method: 'spatial', confidence: 0.61, alternatives: 2, binding_id: 'b-1' },
    } as Partial<Observation>);
    expect(renderObservation(record).sentence).toContain('2 alternative candidate(s) were not ruled out');
  });
});

describe('it cannot infer', () => {
  it('never turns a confidence into a judgement word', () => {
    for (const value of [0.01, 0.5, 0.99]) {
      const { sentence } = renderObservation(
        presence({ confidence: { value, calibrated: true } } as Partial<Observation>),
      );
      expect(sentence).toContain(value.toFixed(3));
      // "high"/"low" would be a threshold, and the threshold is the consumer's.
      expect(sentence.toLowerCase()).not.toMatch(/\b(high|low|strong|weak|very)\b/);
    }
  });

  it('contains no judgement vocabulary in any template string', () => {
    // Checked at the source, so an interpolated *value* of "high" cannot make
    // this pass or fail. Only the words the renderer itself chose are examined.
    const templates = CODE.match(/'[^']*'|`[^`]*`/g) ?? [];
    const banned =
      /\b(suspicious|loiter|violation|exceeds?|too (long|many|short)|should|likely|probably|appears? to|seems?|dangerous|unusual|abnormal|risk)\b/i;

    const offenders = templates.filter((literal) => banned.test(literal));
    expect(offenders).toEqual([]);
  });

  it('never merges two records', () => {
    const first = renderObservation(presence({ t_capture_ns: 1_000_000 } as Partial<Observation>));
    const second = renderObservation(presence({ t_capture_ns: 2_000_000 } as Partial<Observation>));
    // Each narrative mentions exactly its own capture time and no other.
    expect(first.sentence).toContain('+1.0 ms');
    expect(first.sentence).not.toContain('+2.0 ms');
    expect(second.sentence).toContain('+2.0 ms');
    expect(second.sentence).not.toContain('+1.0 ms');
  });

  it('renders an unknown observation type literally instead of guessing', () => {
    const record = observation(5, {
      observation_type: 'gait_signature',
    } as Partial<Observation>);
    const { sentence } = renderObservation(record);
    expect(sentence).toContain('gait_signature');
    // It must not silently fall back to a type it *does* know.
    expect(sentence).not.toContain('reported an object present');
  });
});

describe('it cannot hide', () => {
  it('lists fields present on the record that it did not speak', () => {
    const record = presence({
      // A field the narrative has no template for.
      demand_ids: ['d-1'],
      identity: { method: 'spatial' },
    } as unknown as Partial<Observation>);

    const { unrenderedFields } = renderObservation(record);
    // `identity` is not spoken on a `presence` record, so it must be declared.
    expect(unrenderedFields).toContain('identity');
  });

  it('every clause names at least one source field', () => {
    const { clauses } = renderObservation(presence());
    expect(clauses.length).toBeGreaterThan(0);
    for (const clause of clauses) {
      expect(clause.fields.length).toBeGreaterThan(0);
    }
  });

  it('says so when no evidence is attached', () => {
    const { caveats } = renderObservation(presence({ evidence_ref: null } as Partial<Observation>));
    expect(caveats.join(' ')).toContain('No evidence reference');
  });

  it('warns that an uncalibrated score is not comparable', () => {
    const { caveats } = renderObservation(
      presence({ confidence: { value: 0.9, calibrated: false } } as Partial<Observation>),
    );
    expect(caveats.join(' ')).toContain('not comparable across models');
  });

  it('flags a predicted position as not directly observed', () => {
    const { caveats } = renderObservation(
      presence({ measurement_basis: 'predicted' } as Partial<Observation>),
    );
    expect(caveats.join(' ')).toContain('predicted, not measured');
  });

  it('attaches the V8 reading to a coverage record', () => {
    const record = observation(6, {
      observation_type: 'coverage',
      coverage: { status: 'blind', reason: 'stream_disconnected', effective_rate: 0 },
      class_id: null,
      object_id: null,
    } as unknown as Partial<Observation>);

    const { sentence, caveats } = renderObservation(record);
    expect(sentence).toContain('status blind');
    expect(sentence).toContain('reason stream_disconnected');
    expect(caveats.join(' ')).toContain('not an observation of absence');
  });

  it('keeps caveats out of the sentence itself', () => {
    const { sentence, caveats } = renderObservation(
      presence({ confidence: { value: 0.9, calibrated: false } } as Partial<Observation>),
    );
    expect(caveats.length).toBeGreaterThan(0);
    for (const caveat of caveats) {
      expect(sentence).not.toContain(caveat);
    }
  });
});

describe('it is deterministic', () => {
  it('renders the same record byte-identically across calls', () => {
    const record = presence();
    const first = renderObservation(record);
    const second = renderObservation(record);
    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
  });

  it('does not depend on wall-clock time', () => {
    // The renderer must never reach for `Date.now()`; a narrative that changed
    // when nothing changed could not go into a regression report.
    expect(CODE).not.toMatch(/Date\.now|performance\.now|new Date\(/);
    expect(CODE).not.toMatch(/Math\.random/);
    expect(CODE).not.toMatch(/toLocaleString|toLocaleDateString|Intl\./);
  });

  it('formats offsets stably across magnitudes', () => {
    expect(formatOffset(500_000)).toBe('+500.0 µs');
    expect(formatOffset(83_000_000)).toBe('+83.0 ms');
    expect(formatOffset(2_500_000_000)).toBe('+2.500 s');
    expect(formatOffset(0, 1_000_000)).toBe('-1.0 ms');
  });

  it('renders relative to the supplied anchor, not an implicit one', () => {
    const record = presence({ t_capture_ns: 5_000_000 } as Partial<Observation>);
    expect(renderObservation(record, { anchorNs: 0 }).sentence).toContain('+5.0 ms');
    expect(renderObservation(record, { anchorNs: 4_000_000 }).sentence).toContain('+1.0 ms');
  });
});
