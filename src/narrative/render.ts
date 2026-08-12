/**
 * Narrative rendering — observation records as English sentences.
 *
 * **This is a presentation layer and nothing else.** Every word it emits is
 * either a fixed template string or a value copied verbatim out of the
 * observation it was given. It performs no inference, joins no two records, and
 * reaches no conclusion.
 *
 * That restraint is not stylistic. A validation console that could write
 * *"a person loitered near the door"* would be doing exactly what the Semantic
 * Ceiling forbids the platform from doing — and an engineer reading it would
 * have no way to tell which words came from Vision OS and which the console
 * invented. So five rules hold, each enforced by a test:
 *
 * 1. **One record, one narrative.** Never merge observations. Merging is
 *    temporal reasoning, and *"they walked from A to B"* is a claim no single
 *    observation makes.
 * 2. **Every clause names its source field.** `Clause.fields` is not
 *    decoration — the UI shows it, so any word can be traced to the field that
 *    produced it. A clause with no source field cannot be constructed.
 * 3. **No thresholds, ever.** `0.95` renders as `0.950`, never as "high
 *    confidence". Deciding what counts as high is a judgment, and it is the
 *    consumer's.
 * 4. **Unknown values render verbatim.** A future `observation_type` this file
 *    has never heard of produces a literal, honest sentence rather than a guess
 *    or a silent omission.
 * 5. **Deterministic.** No `Date.now()`, no locale formatting, no randomness.
 *    The same record renders byte-identically forever, which is what lets a
 *    narrative go into a regression report.
 */

import type { Observation } from '@contract/types';

// --- shape ------------------------------------------------------------------ //

export type ClauseKind = 'lead' | 'body' | 'qualifier' | 'caveat';

export interface Clause {
  text: string;
  /** Field paths this clause was built from. Never empty. */
  fields: string[];
  kind: ClauseKind;
}

export interface Narrative {
  /** The clauses joined into readable prose. */
  sentence: string;
  clauses: Clause[];
  /** Field paths that contributed to the sentence. */
  renderedFields: string[];
  /**
   * Top-level fields present on the record that the narrative did **not** use.
   *
   * Surfaced rather than swallowed. A prose view that quietly dropped a field
   * would let an engineer read a complete-sounding sentence about an incomplete
   * record — which is the console telling a comfortable lie.
   */
  unrenderedFields: string[];
  /** Statements about how the sentence must be read. Not claims about the world. */
  caveats: string[];
}

export interface RenderOptions {
  /**
   * Nanosecond origin for relative times. Defaults to 0, which is where a
   * validation session's `VirtualClock` starts.
   *
   * Explicit and required-by-default because rendering against "now" would make
   * the same record produce different prose on every repaint, and a narrative
   * that changes when nothing changed is not evidence.
   */
  anchorNs?: number;
}

// --- vocabulary -------------------------------------------------------------- //

/**
 * The seven closed observation types (02_VOM §11.2), each with a fixed lead.
 *
 * The platform may add a type; it may never repurpose one. So an unrecognised
 * type falls through to a literal rendering rather than to a nearby guess.
 */
const TYPE_LEAD: Record<string, string> = {
  presence: 'reported an object present',
  spatial: 'reported a spatial change',
  attribute: 'reported an attribute',
  identity: 'reported an identity assertion',
  lifecycle: 'reported a lifecycle change',
  quality: 'reported an input-quality change',
  coverage: 'reported its own observability',
};

/** Fixed notes attached to a *type* or a *field state*, quoting the architecture. */
const CAVEAT = {
  uncalibrated:
    'This confidence is uncalibrated. Uncalibrated scores are not comparable across models, so it must not be thresholded against another model’s score.',
  predicted:
    'This position was predicted, not measured — the object was not directly observed in this frame.',
  interpolated:
    'This position was interpolated between two measurements and is only valid in retrospect.',
  coverage:
    'This is a statement about what the platform could see, not about what was there. An absence of observations across this window is not an observation of absence.',
  superseded:
    'This record supersedes an earlier one. The earlier record still exists and remains resolvable — history is corrected by addition, never by edit.',
  noEvidence:
    'No evidence reference is attached to this record.',
  provisional:
    'The object’s lifecycle is provisional: the platform has not yet confirmed it.',
} as const;

// --- formatting -------------------------------------------------------------- //

/** Integer nanoseconds → a stable, unit-suffixed string. Never locale-aware. */
export function formatOffset(ns: number, anchorNs = 0): string {
  const delta = ns - anchorNs;
  const sign = delta < 0 ? '-' : '+';
  const abs = Math.abs(delta);
  if (abs < 1_000_000) return `${sign}${(abs / 1000).toFixed(1)} µs`;
  if (abs < 1_000_000_000) return `${sign}${(abs / 1_000_000).toFixed(1)} ms`;
  return `${sign}${(abs / 1_000_000_000).toFixed(3)} s`;
}

export function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return `${(ms / 1000).toFixed(3)} s`;
}

/** A confidence, with its calibration state made unmissable. */
function formatConfidence(confidence: Record<string, unknown> | null | undefined): string | null {
  if (!confidence || typeof confidence.value !== 'number') return null;
  const semantics = typeof confidence.semantics === 'string' ? confidence.semantics : null;
  const calibrated = confidence.calibrated === true;
  const parts = [confidence.value.toFixed(3)];
  const notes: string[] = [];
  if (semantics) notes.push(semantics);
  notes.push(calibrated ? 'calibrated' : 'uncalibrated');
  return `${parts[0]} (${notes.join(', ')})`;
}

/** `a` / `an`, chosen from the first letter. Grammar, not judgment. */
function article(word: string): string {
  return /^[aeiou]/i.test(word) ? 'an' : 'a';
}

function shortId(value: unknown, keep = 8): string {
  const text = String(value ?? '');
  return text.length <= keep ? text : `…${text.slice(-keep)}`;
}

function box(spatial: unknown): string | null {
  if (!spatial || typeof spatial !== 'object') return null;
  const bbox = (spatial as Record<string, unknown>).bbox;
  if (!bbox || typeof bbox !== 'object') return null;

  // Each corner is read and checked individually. A partial bbox renders as
  // nothing rather than as a box with an invented edge — a plausible-looking
  // rectangle the platform never reported would be the renderer inventing
  // geometry.
  const corners = bbox as Record<string, unknown>;
  const values: number[] = [];
  for (const key of ['x1', 'x2', 'y1', 'y2']) {
    const value = corners[key];
    if (typeof value !== 'number' || !Number.isFinite(value)) return null;
    values.push(value);
  }

  const [x1, x2, y1, y2] = values as [number, number, number, number];
  return `x ${x1.toFixed(3)}–${x2.toFixed(3)}, y ${y1.toFixed(3)}–${y2.toFixed(3)}`;
}

// --- the renderer ------------------------------------------------------------- //

/** Fields the narrative deliberately never speaks. Bookkeeping, not content. */
const NEVER_RENDERED = new Set([
  'schema_version',
  'taxonomy_version',
  'tenant_id',
  'site_id',
  'labels',
  'demand_ids',
  'lineage',
  't_published_ns',
  'timing',
  'clock_quality',
]);

export function renderObservation(
  observation: Observation,
  options: RenderOptions = {},
): Narrative {
  const anchorNs = options.anchorNs ?? 0;
  const clauses: Clause[] = [];
  const used = new Set<string>();
  const caveats: string[] = [];

  const add = (text: string, fields: string[], kind: ClauseKind = 'body') => {
    if (!fields.length) throw new Error('a narrative clause must name its source fields');
    for (const field of fields) used.add(field);
    clauses.push({ text, fields, kind });
  };

  const record = observation as unknown as Record<string, unknown>;
  const type = String(record.observation_type ?? '');

  // --- lead: when, which camera, what kind of statement --------------------- //

  const timeText =
    typeof record.t_capture_ns === 'number'
      ? `At ${formatOffset(record.t_capture_ns, anchorNs)}`
      : 'At an unstated capture time';
  const cameraText = record.camera_id ? `camera ${record.camera_id}` : 'an unstated camera';
  const leadVerb = TYPE_LEAD[type] ?? `reported an observation of type “${type || 'unstated'}”`;

  add(`${timeText}, ${cameraText} ${leadVerb}`, ['t_capture_ns', 'camera_id', 'observation_type'], 'lead');

  if (typeof record.t_capture_unc_ms === 'number' && record.t_capture_unc_ms > 0) {
    add(
      `capture time is stated ±${formatDurationMs(record.t_capture_unc_ms)}`,
      ['t_capture_unc_ms'],
      'qualifier',
    );
  }

  // --- body: the subject ----------------------------------------------------- //

  if (record.class_id) {
    const cls = String(record.class_id);
    add(`the object is classified ${article(cls)} ${cls}`, ['class_id']);
  }

  if (record.object_id) {
    add(`object ${shortId(record.object_id)}`, ['object_id']);
  }
  if (record.track_id) {
    add(`track ${shortId(record.track_id)}`, ['track_id']);
  }

  // --- body: type-specific --------------------------------------------------- //

  switch (type) {
    case 'attribute':
      renderAttributes(record, add, caveats);
      break;
    case 'spatial':
    case 'presence':
      renderSpatial(record, add);
      break;
    case 'lifecycle':
      renderLifecycle(record, add);
      break;
    case 'identity':
      renderIdentity(record, add);
      break;
    case 'coverage':
      renderCoverage(record, add);
      caveats.push(CAVEAT.coverage);
      break;
    case 'quality':
      renderQuality(record, add);
      break;
    default:
      // An unknown type still gets its payload spoken, literally.
      renderSpatial(record, add);
      renderAttributes(record, add, caveats);
      break;
  }

  // --- qualifiers ------------------------------------------------------------- //

  const confidence = formatConfidence(record.confidence as Record<string, unknown>);
  if (confidence) {
    add(`class confidence ${confidence}`, ['confidence'], 'qualifier');
    if ((record.confidence as Record<string, unknown>)?.calibrated !== true) {
      caveats.push(CAVEAT.uncalibrated);
    }
  }

  if (record.lifecycle_state) {
    add(`lifecycle ${record.lifecycle_state}`, ['lifecycle_state'], 'qualifier');
    if (record.lifecycle_state === 'provisional') caveats.push(CAVEAT.provisional);
  }

  if (record.measurement_basis) {
    const basis = String(record.measurement_basis);
    add(`basis ${basis}`, ['measurement_basis'], 'qualifier');
    if (basis === 'predicted') caveats.push(CAVEAT.predicted);
    if (basis === 'interpolated') caveats.push(CAVEAT.interpolated);
  }

  if (record.frame_ref) {
    add(`frame ${frameRefText(record.frame_ref)}`, ['frame_ref'], 'qualifier');
  }

  // --- provenance and evidence — V4 ------------------------------------------- //

  const provenance = record.provenance as Record<string, unknown> | null | undefined;
  if (provenance) {
    const producer = [provenance.producer_module, provenance.adapter_id, provenance.model_id]
      .filter(Boolean)
      .join(' · ');
    add(
      `produced by ${producer || 'an unstated producer'}`,
      ['provenance'],
      'qualifier',
    );
  }

  if (record.evidence_ref) {
    add(`evidence ${shortId(record.evidence_ref, 10)}`, ['evidence_ref'], 'qualifier');
  } else {
    caveats.push(CAVEAT.noEvidence);
    used.add('evidence_ref');
  }

  if (record.supersedes) {
    add(`supersedes ${shortId(record.supersedes)}`, ['supersedes'], 'qualifier');
    caveats.push(CAVEAT.superseded);
  }

  if (record.observation_id) {
    add(`record ${shortId(record.observation_id, 10)}`, ['observation_id'], 'qualifier');
  }

  // --- assemble ---------------------------------------------------------------- //

  const lead = clauses.filter((c) => c.kind === 'lead').map((c) => c.text);
  const body = clauses.filter((c) => c.kind === 'body').map((c) => c.text);
  const qualifiers = clauses.filter((c) => c.kind === 'qualifier').map((c) => c.text);

  const head = [...lead, ...body].join('; ');
  const tail = qualifiers.length ? ` [${qualifiers.join('; ')}]` : '';
  const sentence = `${head}.${tail}`;

  const present = Object.keys(record).filter((key) => {
    const value = record[key];
    if (value === null || value === undefined) return false;
    if (Array.isArray(value) && value.length === 0) return false;
    if (typeof value === 'object' && Object.keys(value as object).length === 0) return false;
    return true;
  });

  return {
    sentence,
    clauses,
    renderedFields: Array.from(used).sort(),
    unrenderedFields: present
      .filter((key) => !used.has(key) && !NEVER_RENDERED.has(key))
      .sort(),
    caveats: Array.from(new Set(caveats)),
  };
}

// --- per-type bodies ---------------------------------------------------------- //

type Add = (text: string, fields: string[], kind?: ClauseKind) => void;

function renderSpatial(record: Record<string, unknown>, add: Add): void {
  const geometry = box(record.spatial);
  if (geometry) {
    const frame = (record.spatial as Record<string, unknown>).frame_of_reference;
    add(
      `occupying ${geometry}${frame ? ` in ${frame} coordinates` : ''}`,
      ['spatial'],
    );
  }
}

function renderAttributes(
  record: Record<string, unknown>,
  add: Add,
  caveats: string[],
): void {
  const attributes = record.attributes;
  if (!Array.isArray(attributes) || attributes.length === 0) return;

  for (const raw of attributes) {
    const attribute = raw as Record<string, unknown>;
    const key = String(attribute.key ?? 'unstated');
    const value = JSON.stringify(attribute.value);
    const confidence = formatConfidence(attribute.confidence as Record<string, unknown>);
    const parts = [`${key} = ${value}`];
    if (confidence) parts.push(`confidence ${confidence}`);
    if (attribute.evidence_ref) parts.push(`evidence ${shortId(attribute.evidence_ref, 10)}`);
    add(parts.join(', '), ['attributes']);

    if (confidence && (attribute.confidence as Record<string, unknown>)?.calibrated !== true) {
      caveats.push(CAVEAT.uncalibrated);
    }
  }
}

function renderLifecycle(record: Record<string, unknown>, add: Add): void {
  const transition = record.lifecycle_transition as Record<string, unknown> | null | undefined;
  if (!transition) return;
  const trigger = transition.trigger ? `, triggered by ${transition.trigger}` : '';
  add(
    `state moved from ${transition.previous} to ${transition.current}${trigger}`,
    ['lifecycle_transition'],
  );
}

function renderIdentity(record: Record<string, unknown>, add: Add): void {
  const identity = record.identity as Record<string, unknown> | null | undefined;
  if (!identity) return;
  const parts: string[] = [];
  if (identity.method) parts.push(`method ${identity.method}`);
  if (typeof identity.confidence === 'number') parts.push(`confidence ${identity.confidence.toFixed(3)}`);
  if (identity.binding_id) parts.push(`binding ${shortId(identity.binding_id)}`);
  if (typeof identity.alternatives === 'number' && identity.alternatives > 0) {
    // Published rather than resolved: candidates existed and none was decisive.
    parts.push(`${identity.alternatives} alternative candidate(s) were not ruled out`);
  }
  add(`identity asserted (${parts.join(', ') || 'no detail stated'})`, ['identity']);
}

function renderCoverage(record: Record<string, unknown>, add: Add): void {
  const coverage = record.coverage as Record<string, unknown> | null | undefined;
  if (!coverage) return;
  const parts: string[] = [];
  if (coverage.status) parts.push(`status ${coverage.status}`);
  if (coverage.reason) parts.push(`reason ${coverage.reason}`);
  if (typeof coverage.effective_rate === 'number') {
    parts.push(`effective rate ${coverage.effective_rate.toFixed(3)}`);
  }
  add(parts.join(', ') || 'no coverage detail stated', ['coverage']);
}

function renderQuality(record: Record<string, unknown>, add: Add): void {
  const quality = record.quality as Record<string, unknown> | null | undefined;
  if (!quality) return;
  const parts = Object.entries(quality)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => `${key} ${typeof value === 'number' ? value.toFixed(3) : String(value)}`);
  add(parts.join(', ') || 'no quality detail stated', ['quality']);
}

function frameRefText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const ref = value as Record<string, unknown>;
    return `${ref.camera_id}/e${ref.stream_epoch}/f${ref.frame_seq}`;
  }
  return String(value);
}
