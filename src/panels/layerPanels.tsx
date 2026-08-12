/**
 * The per-layer inspection panels.
 *
 * Each names the columns that matter for its layer and inherits everything else
 * from `ChannelPanel`. The `note` on each is not decoration: it states what the
 * layer is architecturally allowed to say, which is how an engineer spots a
 * platform that has started saying more.
 */

import { ChannelPanel, field } from './ChannelPanel';

export function FrameInformationPanel() {
  return (
    <ChannelPanel
      title="Frame Information"
      channel="acquisition"
      emptyWhat="frames admitted"
      emptyNote="If the session is playing and this stays empty, acquisition is not admitting frames."
      note="Frame descriptors and stream lifecycle. Carries no pixels — V12 keeps those local."
      columns={[
        field('pts_ms', 'pts', 80),
        field('width', 'w', 60),
        field('height', 'h', 60),
        field('bytes', 'bytes', 90),
        field('is_keyframe', 'key', 60),
        field('faults', 'injected'),
      ]}
    />
  );
}

export function DetectionsPanel() {
  return (
    <ChannelPanel
      title="Detections"
      channel="detection"
      emptyWhat="detections"
      emptyNote="A completed detection with zero results is different from a failure to look — check for detection.failed."
      note="A detection is a per-frame assertion, not an identity (V10). detection.failed means the platform could not look; zero results means it looked and saw nothing."
      columns={[
        field('detection_count', 'count', 70),
        field('inference_ms', 'inference', 90),
        field('batch_size', 'batch', 60),
        field('model_id', 'model'),
        field('reason', 'reason'),
      ]}
    />
  );
}

export function TracksPanel() {
  return (
    <ChannelPanel
      title="Tracks"
      channel="tracking"
      emptyWhat="track activity"
      note="A track is camera- and epoch-scoped and is never an identity (V10). An association_failure is the tracker REFUSING to bind, not a wrong binding — the platform cannot know a binding was wrong without ground truth."
      columns={[
        field('track_id', 'track', 200),
        field('state', 'state', 90),
        field('association_confidence', 'assoc', 80),
        field('measurement_basis', 'basis', 90),
        field('break_reason', 'break'),
        field('coasted_frames', 'coasted', 70),
      ]}
    />
  );
}

export function VisualObjectsPanel() {
  return (
    <ChannelPanel
      title="Visual Objects"
      channel="registry"
      emptyWhat="registry activity"
      note="The Object Registry is the only module that may mint an object id (01_LAYERED §8). identity_asserted with ambiguous=true means candidates existed but none was decisive, so a NEW object was minted and the alternatives published rather than one being guessed."
      columns={[
        field('object_id', 'object', 200),
        field('track_id', 'track', 180),
        field('class_id', 'class', 90),
        field('previous', 'from', 90),
        field('current', 'to', 90),
        field('method', 'method'),
        field('ambiguous', 'ambig', 60),
        field('alternatives', 'alts', 60),
      ]}
    />
  );
}

export function CanonicalCropsPanel() {
  return (
    <ChannelPanel
      title="Canonical Crops"
      channel="cropping"
      emptyWhat="crop-manager events"
      emptyNote="The Crop Manager publishes alarms, not individual crops — a crop is data-plane evidence and the Event Bus is a lossy control-plane notifier."
      note="No CropProduced event exists by design: announcing every crop would put megabyte-scale traffic on a bus built for kilobytes. gate_rejection_spike is almost always physical — a camera nudged, a lens fouled, a light failed."
      columns={[
        field('reason', 'reason'),
        field('rejection_rate', 'reject rate', 90),
        field('sample_size', 'n', 60),
        field('attribute_key', 'attribute'),
        field('consecutive_failures', 'fails', 60),
        field('pressure', 'pressure', 80),
      ]}
    />
  );
}

export function UnderstandingPanel() {
  return (
    <ChannelPanel
      title="Understanding Results"
      channel="understanding"
      emptyWhat="understanding events"
      emptyNote="Attribute values reach consumers through observations, not through this bus. Silence here is normal on a healthy run."
      note="There is deliberately no UnderstandingSucceeded event and no event carrying an attribute value. A fallback_engaged that nobody notices becomes permanent, and the platform quietly runs on its worst model forever — that is what this panel is for."
      columns={[
        field('outcome', 'outcome', 120),
        field('primary_model', 'primary'),
        field('fallback_model', 'fallback'),
        field('rejection_rate', 'reject rate', 90),
        field('detail', 'detail'),
      ]}
    />
  );
}

export function ObservationLogPanel() {
  return (
    <ChannelPanel
      title="Observation Log"
      channel="synthesis"
      emptyWhat="synthesis events"
      emptyNote="No ObservationPublished event exists — observations reach consumers through the log and the projection. Silence here is a healthy builder."
      note="observation_rejected is published because a silently refused observation is a fact that vanished, and the producer would never learn its output was being discarded. An unregistered_attribute is the Semantic Ceiling (V1) doing its job."
      columns={[
        field('observation_type', 'type', 140),
        field('kind', 'kind', 140),
        field('violation_rate', 'rate', 80),
        field('sample_size', 'n', 60),
        field('detail', 'detail'),
      ]}
    />
  );
}

export function VisionStateEventsPanel() {
  return (
    <ChannelPanel
      title="Vision State Events"
      channel="state"
      emptyWhat="state events"
      note="partition_degraded halts a partition LOUDLY — losing observations invisibly is a V8 violation of the worst kind. quarantined means the observation IS in the log and is still part of the record; only the projection could not absorb it, which is a projection bug rather than a producer one."
      columns={[
        field('camera_id', 'camera', 130),
        field('reason', 'reason'),
        field('buffered', 'buffered', 80),
        field('drained', 'drained', 80),
        field('gap_ms', 'gap', 80),
        field('observations_replayed', 'replayed', 90),
      ]}
    />
  );
}

export function ArchitectureEventsPanel() {
  return (
    <ChannelPanel
      title="Architecture Events"
      channel="event"
      emptyWhat="platform events"
      note="The raw Event Bus firehose. Information travels upward through the bus without an upward dependency, which is why watching a lower layer from here is the intended mechanism rather than a workaround."
      columns={[field('camera_id', 'camera', 130), field('reason', 'reason'), field('detail', 'detail')]}
    />
  );
}

export function HealthPanel() {
  return (
    <ChannelPanel
      title="Health"
      channel="health"
      emptyWhat="health reports"
      note="silent_failure_suspected is a SUSPICION, not a verdict: it degrades coverage confidence and alerts an operator, but never blinds a camera automatically — a false positive that blinded a working camera would itself be an outage."
      columns={[
        field('component_id', 'component', 150),
        field('state', 'state', 100),
        field('camera_id', 'camera', 130),
        field('status', 'status', 100),
        field('reason', 'reason'),
        field('effective_rate', 'rate', 80),
        field('detector', 'detector'),
      ]}
    />
  );
}

export function CameraPanel() {
  return (
    <ChannelPanel
      title="Camera"
      channel="camera"
      emptyWhat="camera events"
      emptyNote="A stable camera emits nothing here. Silence is the healthy case."
      note="viewpoint_drift_suspected publishes a suspicion, never an automatic invalidation — a false positive must not blind a working site."
      columns={[field('camera_id', 'camera', 150), field('change', 'change'), field('evidence', 'evidence')]}
    />
  );
}
