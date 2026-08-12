/**
 * The wire contract, in TypeScript.
 *
 * A projection of `docs/CONTRACT.md`, which is itself a transport for Vision OS's
 * semantics. Nothing here invents a field, and nothing here is optional that the
 * platform makes mandatory — `StateResult.coverage` has no `?` for the same
 * reason it has no default in `core/model/api.py`.
 *
 * Time is `number` nanoseconds. JavaScript numbers hold integers exactly to
 * 2^53, which is ~104 days of nanoseconds — long enough for any replay session
 * and short enough to matter for a soak, so `docs/CONTRACT.md` §1 requires
 * absolute epoch values be read as `bigint` where precision is asserted. The
 * console renders *relative* times throughout, so it holds `number`.
 */

// --- primitives ------------------------------------------------------------ //

export type Nanos = number;
export type Millis = number;

export type CameraId = string;
export type ObjectId = string;
export type ObservationId = string;
export type ClassId = string;
export type AttributeKey = string;
export type TenantId = string;
export type DemandId = string;

export interface Confidence {
  value: number;
  /** Uncalibrated scores are not comparable across models (02_VOM §7.2). */
  calibrated: boolean;
  semantics?: string;
  calibration_id?: string | null;
  raw_score?: number;
}

export type LifecycleState =
  | 'provisional'
  | 'active'
  | 'occluded'
  | 'lost'
  | 'expired'
  | 'merged_into';

// --- errors ----------------------------------------------------------------- //

export interface ApiErrorView {
  /** Stable, machine-readable, never reworded. This is the contract. */
  code: string;
  /** May change between releases. Never switch on this. */
  message: string;
  /**
   * A FIELD, never inferred. 09_API §8: *"Consumers must never infer
   * retryability from a status code or a message string. Inferring it is how
   * retry storms begin."*
   */
  retryable: boolean;
  retry_after_ms?: Millis | null;
  details?: Record<string, unknown>;
  request_id?: string;
}

// --- state ------------------------------------------------------------------ //

export interface AttributeView {
  key: AttributeKey;
  value: unknown;
  confidence: Confidence;
  observed_at_ns: Nanos;
  valid_until_ns?: Nanos | null;
  /**
   * Present even without the evidence privilege — *"knowing an explanation
   * exists is not the same as reading it."* Render a locked affordance, never a
   * blank.
   */
  evidence_ref?: string | null;
}

export interface ObjectView {
  object_id: ObjectId;
  class_id: ClassId;
  class_confidence: Confidence;
  lifecycle: LifecycleState;
  camera_id: CameraId;
  first_seen_ns: Nanos;
  last_seen_ns: Nanos;
  last_confirmed_ns: Nanos;
  /** Derived by the platform. Never recompute it here — see `docs/CONTRACT.md` §2.2. */
  is_stale: boolean;
  attributes: Record<AttributeKey, AttributeView>;
  spatial?: unknown | null;
  trajectory?: Array<Record<string, unknown>>;
  provenance?: unknown | null;
  observation_count: number;
}

export interface CoverageSummary {
  observable_fraction: number;
  cameras_observing: number;
  cameras_blind: number;
  cameras_degraded: number;
  unavailable: Array<[string, string]>;
  fully_observable?: boolean;
}

export interface SnapshotView {
  partitions: CameraId[];
  consistency: string;
  max_lag_ms: Millis;
  incomplete: Array<[string, string]>;
  taken_at_ns?: Nanos | null;
}

export interface CapabilitySummary {
  taxonomy_version: string;
  producible_classes: ClassId[];
  producible_attributes: AttributeKey[];
  gaps: Array<[string, string]>;
  models_in_use: Array<[string, string, string]>;
  effective_since_ns?: Nanos | null;
}

export interface StateResult {
  objects: ObjectView[];
  snapshot: SnapshotView;
  /** Required. Not optional here because it is not optional there (V8). */
  coverage: CoverageSummary;
  capabilities?: CapabilitySummary;
  cursor?: string | null;
  complete?: boolean;
}

export interface CoverageReport {
  observable_fraction: number;
  gaps?: Array<Record<string, unknown>>;
}

// --- observations ------------------------------------------------------------ //

export interface Observation {
  observation_id: ObservationId;
  observation_type: string;
  camera_id?: CameraId;
  object_id?: ObjectId | null;
  class_id?: ClassId | null;
  t_capture_ns: Nanos;
  confidence?: Confidence | null;
  attributes?: Array<Record<string, unknown>>;
  /** V4: no observation without evidence and provenance. */
  provenance?: Record<string, unknown> | null;
  supersedes?: ObservationId | null;
  tenant_id?: TenantId;
  [key: string]: unknown;
}

export interface ObservationPage {
  observations: Observation[];
  cursor?: string | null;
  /** §2.2: the page reports whether the window was fully observable. */
  window_fully_observable: boolean;
  coverage: CoverageSummary;
  count?: number;
}

export interface EvidenceView {
  observation_id: ObservationId;
  trigger_reason?: string;
  /** base64, or null when the deployment retains no imagery — a posture, not a failure. */
  crop?: string | null;
  raw_model_output?: string | null;
  unstructured_note?: string | null;
  decision_path?: string[];
  provenance?: unknown;
  timing?: unknown;
  quality?: unknown;
}

export interface DemandView {
  demand_id: DemandId;
  subscriber: string;
  status: string;
  required_attributes: AttributeKey[];
  effective_freshness_ms?: Millis | null;
  unsatisfiable: Array<[AttributeKey, string]>;
}

// --- the stream --------------------------------------------------------------- //

export const CHANNELS = [
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
] as const;

export type Channel = (typeof CHANNELS)[number];

export type TapType =
  | 'layer.tap'
  | 'observation'
  | 'state.delta'
  | 'coverage.change'
  | 'metrics.sample'
  | 'metrics.sweep'
  | 'event'
  | 'gap'
  | 'heartbeat'
  | 'session.state'
  | 'transport.command'
  | 'fault.armed'
  | 'fault.cleared'
  | 'capability.gap'
  | 'health.report'
  | 'error';

export interface TapMessage<P = Record<string, unknown>> {
  /** Monotonic, gapless, per-socket. A jump means loss — see `Gap`. */
  seq: number;
  ts_ns: Nanos;
  channel: Channel;
  type: TapType;
  payload: P;
  frame_index?: number;
}

export type GapReason =
  | 'slow_consumer'
  | 'platform_blind'
  | 'budget_shed'
  | 'partition_unavailable'
  | 'retention_expired'
  | 'subscriber_overflow';

export interface GapPayload {
  start_ns: Nanos;
  end_ns: Nanos;
  reason: GapReason;
  cameras?: CameraId[];
  observations_missed?: number | null;
  /** A FIELD, from `GapReason.recoverable`. Never inferred. */
  recoverable: boolean;
  seq_from?: number;
  seq_to?: number;
}

// --- sessions and media -------------------------------------------------------- //

export interface MediaProbe {
  frame_count: number;
  width: number;
  height: number;
  fps: number;
  duration_ms: Millis;
  backend: string;
  seekable: boolean;
}

export interface MediaAsset {
  media_id: string;
  name: string;
  kind: 'video_file' | 'frame_folder' | 'synthetic' | 'rtsp_replay';
  path?: string | null;
  usable: boolean;
  /** Why it cannot be replayed. A capability gap, not an empty result (V8). */
  error?: string | null;
  created_at_ns: Nanos;
  probe?: MediaProbe | null;
}

export type SessionState =
  | 'created'
  | 'booting'
  | 'ready'
  | 'playing'
  | 'paused'
  | 'ended'
  | 'failed';

export interface FaultVerdict {
  scenario: string;
  stage: 'source' | 'decoder' | 'session';
  at_frame: number;
  duration_frames: number;
  params: Record<string, number>;
  armed_at_seq: number;
  expected_events: string[];
  missing_events: string[];
  injections: Array<{ scenario: string; frame_index: number; detail: string }>;
  /** `not_reached` is not a pass. Neither is `unvalidated`. */
  verdict: 'validated' | 'unvalidated' | 'observational' | 'not_reached';
}

export interface SessionDescription {
  session_id: string;
  state: SessionState;
  error?: string | null;
  media_id: string;
  media_name: string;
  camera_id: CameraId;
  tenant_id: TenantId;
  semantics: 'archival' | 'realtime';
  target_fps: number;
  deterministic: boolean;
  rtsp: boolean;
  frame_count: number;
  frame_index: number;
  playing: boolean;
  speed: number;
  exhausted: boolean;
  created_at_ns: Nanos;
  events_attached: boolean;
  events_unavailable_reason?: string | null;
  taps: TapStats;
  faults: FaultVerdict[];
}

export interface TapStats {
  sequence: number;
  dropped: number;
  subscribers: number;
  by_channel: Record<string, number>;
}

export interface FrameLedgerEntry {
  frame_index: number;
  pts_ms: Millis;
  width: number;
  height: number;
  bytes: number;
  is_keyframe: boolean;
  faults: string[];
  emitted_at_ns: Nanos;
}

// --- health, architecture, reports ---------------------------------------------- //

export interface HarnessHealth {
  harness: {
    status: string;
    wire_version: string;
    wire_major: number;
    serve_frames: boolean;
    allow_evidence: boolean;
    stream_queue_capacity: number;
  };
  vision_os: {
    available: boolean;
    api_version?: string;
    supported_majors?: number[];
    metric_names?: number;
    error?: string;
    root: string;
  };
  media: {
    backends: string[];
    containers: Record<string, boolean>;
    images: string[];
    always_available: string[];
  };
  sessions: Array<{ session_id: string; state: string; vision_os?: Record<string, string> }>;
}

export interface ArchitectureReport {
  vision_os: HarnessHealth['vision_os'];
  layers: Array<{ layer: string; module: string; contains: string }>;
  declared_order: string[];
  ports: {
    available: boolean;
    reason?: string;
    catalogue?: Array<{ port: string; value: string }>;
    catalogue_size?: number;
    bindable?: string[];
    bindable_count?: number;
    unbindable?: string[];
  };
  invariants: Array<{ id: string; name: string; evidence: string }>;
  runtime: {
    available: boolean;
    reason?: string;
    session_id?: string;
    health?: Record<string, string>;
    started_layers?: string[];
    started?: boolean;
    partitions?: string[];
    event_bus_attached?: boolean;
    observed_order?: Array<{
      channel: string;
      first_seq: number;
      declared_position: number | null;
      out_of_order: boolean;
    }>;
  };
  ownership?: Array<{
    artifact: string;
    minted_by: string;
    consumed_by: string[];
    note?: string;
  }>;
}

export interface MetricsResponse {
  available: boolean;
  reason?: string;
  session_id?: string;
  sample?: { source: string | null; values: Record<string, unknown>; unavailable?: string };
  names: string[];
  taps?: TapStats;
  frames_emitted?: number;
}

export interface ReplayVerification {
  available: boolean;
  reason?: string;
  session_id?: string;
  reports?: Array<Record<string, unknown>>;
  mismatches?: number;
  /** `false` invalidates every recovery guarantee in 07_STATE §9.1. */
  deterministic?: boolean;
}

export type ReportKind =
  | 'replay'
  | 'performance'
  | 'observation'
  | 'architecture'
  | 'failure'
  | 'latency'
  | 'regression'
  | 'summary';

export interface ReportEnvelope {
  kind: ReportKind;
  available: boolean;
  reason?: string;
  session_id?: string;
  generated_at_ns: Nanos;
  [key: string]: unknown;
}

export const SCENARIOS = [
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
] as const;

export type ScenarioName = (typeof SCENARIOS)[number];

export type TransportAction =
  | 'play'
  | 'pause'
  | 'step'
  | 'seek'
  | 'speed'
  | 'restart'
  | 'record_restart_gap'
  | 'loop';
