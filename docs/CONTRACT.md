# The Validation Wire Contract — v1

**Status:** frozen with Vision OS v1. Additive changes only.

This document is the single source of truth shared by the Python harness and the
TypeScript console. Neither side may invent a field. `src/contract/*.ts` and
`harness/vosvc_harness/contract.py` are both projections of this file, and
`tests/architecture/contract-parity.test.ts` fails if they diverge.

---

## 0. What this contract is, and what it deliberately is not

Vision OS specifies **semantics, not a wire protocol**. `09_API_CONTRACTS.md`
describes query, subscribe, demand, coverage and evidence as meanings; the P32
`TransportPort` docstring states the intent plainly:

> *"The contract is transport-independent by design — this is why
> `09_API_CONTRACTS.md` specifies semantics rather than a wire protocol, and why
> adopting a new transport in 2031 will not be a platform change."*

This document is therefore **a transport, not an API**. It adds no meaning. Every
field in §2 is a serialization of a value the in-process `ObservationApi` already
returns, and the harness route that produces it does nothing but shape-translate —
P32 obligation **T2, never interpret**.

Two consequences follow, and they are the reason this file exists at all:

1. **There is no mutate contract.** Not disabled, not permission-gated — absent.
   `core/model/api.py` says of the sixth contract: *"~~Mutate~~ — Does not exist
   (V6). There is no endpoint, no field, no admin override."* Search this document
   for a write into Vision State and there is nothing to find. §4's control plane
   writes to the **harness**, never to Vision OS.
2. **Coverage is not optional anywhere.** Every state answer carries it, because
   `StateResult.coverage` has no default in the platform and this contract does not
   introduce one (V8).

---

## 1. Transport, framing, versioning

| | |
|---|---|
| Base | `http://<harness>/api/v1` |
| Streams | `ws://<harness>/ws/v1` |
| Encoding | JSON, UTF-8. `Content-Type: application/json` |
| Time | **always** integer nanoseconds, field suffix `_ns`. Never a formatted string, never a float |
| Version | request header `X-VOS-Accept-Major: 1`; response header `X-VOS-Major: 1` |
| Identity | `Authorization: Bearer <token>` → resolved to a Vision OS `Principal` |

Nanoseconds are integers end to end because `Instant.ns` is an integer and a
float would lose precision above 2^53 ns (≈104 days of uptime). A console that
rendered a drifting timestamp would be a console that cannot validate V11.

### 1.1 Error envelope

Every non-2xx response is exactly `ApiErrorView` (`core/model/api.py`), rendered
by the platform's own `error_view()`:

```jsonc
{
  "code": "FORBIDDEN",        // stable, machine-readable, never reworded
  "message": "…",             // may change between releases
  "retryable": false,         // a FIELD, never inferred by the client
  "retry_after_ms": null,
  "details": {},
  "request_id": "req-000123"
}
```

> 09_API §8: *"Consumers must never infer retryability from a status code or a
> message string. Inferring it is how retry storms begin."*

The console's transport layer reads `retryable` and nothing else when deciding to
retry. `src/transport/errors.ts` has no status-code table, by construction.

HTTP status is a courtesy for proxies and logs; `code` is the contract.

| `code` | HTTP | `retryable` |
|---|---|---|
| `FORBIDDEN`, `TENANT_SCOPE_VIOLATION` | 403 | false |
| `NOT_FOUND`, `STATE_NOT_FOUND` | 404 | false |
| `INVALID_SCOPE`, `WINDOW_TOO_LARGE`, `UNSUPPORTED_VERSION` | 400 | false |
| `EVIDENCE_EXPIRED` | 410 | false |
| `OVERLOADED` | 429 | **true** |
| `INTERNAL` | 500 | false |

---

## 2. The Observation API projection (read-only, M14)

These nine routes are a one-to-one image of the route table Vision OS itself
builds in `adapters/exposure/transport.py::routes_for()`. The harness holds that
table and dispatches into it; it does not reach past it.

| Method | Path | Vision OS operation | Returns |
|---|---|---|---|
| `POST` | `/state/query` | `query_state` | `StateResult` |
| `POST` | `/observations/query` | `query_observations` | `Page` |
| `GET` | `/objects/{object_id}` | `get_object` | `ObjectView` |
| `POST` | `/coverage` | `coverage` | `CoverageReport` |
| `GET` | `/capabilities` | `capabilities` | `CapabilitySummary` |
| `GET` | `/evidence/{blob_ref}` | `get_evidence` | `EvidenceView` |
| `GET` | `/demands` | `list_demands` | `DemandView[]` |
| `POST` | `/demands` | `register_demand` | `DemandView` |
| `DELETE` | `/demands/{demand_id}` | `revoke_demand` | `204` |

`POST /demands` is the **only** route in this contract that changes Vision OS
behaviour, and it changes what the platform chooses to compute — never a
published fact. §M14: *"Influence, not a write."*

### 2.1 `POST /state/query`

```jsonc
// request
{
  "scope":   { "tenant_id": "t-eng", "camera_ids": ["cam-1"] },
  "filter":  { "class_ids": ["person"], "lifecycle": ["active", "occluded"],
               "min_confidence": null, "object_ids": [], "attributes": [] },
  "options": { "include_attributes": null, "include_spatial": true,
               "include_trajectory": false, "include_provenance": true,
               "limit": 100, "cursor": null }
}
```

```jsonc
// response — StateResult
{
  "objects": [ /* ObjectView */ ],
  "snapshot": {
    "partitions": ["cam-1"], "consistency": "strong",
    "max_lag_ms": 0.0, "incomplete": [], "taken_at_ns": 1712345678000000000
  },
  "coverage": {
    "observable_fraction": 1.0, "cameras_observing": 1,
    "cameras_blind": 0, "cameras_degraded": 0, "unavailable": []
  },
  "capabilities": { /* CapabilitySummary */ },
  "cursor": null
}
```

`coverage` is **required**. A harness response missing it is a contract
violation, and `tests/architecture/coverage-invariant.test.ts` asserts the
console refuses to render a state result that lacks one — the client-side mirror
of the platform's no-default field.

`include_attributes: null` means *all*; `[]` means *none*. This asymmetry is the
platform's (`QueryOptions.include_attributes`) and is preserved rather than
normalized, because normalizing it would silently change what a query returns.

### 2.2 `ObjectView`

```jsonc
{
  "object_id": "01HX…", "class_id": "person.staff",
  "class_confidence": { "value": 0.91, "calibrated": true, "method": "isotonic" },
  "lifecycle": "active",
  "camera_id": "cam-1",
  "first_seen_ns": 0, "last_seen_ns": 0, "last_confirmed_ns": 0,
  "is_stale": false,              // DERIVED BY THE HARNESS from the platform property
  "attributes": {
    "wearing_apron": {
      "key": "wearing_apron", "value": true,
      "confidence": { "value": 0.77, "calibrated": false, "method": "raw" },
      "observed_at_ns": 0, "valid_until_ns": null,
      "evidence_ref": "ev-01HX…"   // present even without evidence privilege
    }
  },
  "spatial": { … } | null,
  "trajectory": [ { "at_ns": 0, "point": { … } } ],
  "provenance": { … } | null,
  "observation_count": 14
}
```

`is_stale` is serialized rather than recomputed client-side. The platform derives
it *"so a consumer cannot receive a stale flag that is itself stale"*; a console
that recomputed it from its own clock would reintroduce exactly that bug.

`evidence_ref` is present even when the caller cannot read evidence — *"knowing
an explanation exists is not the same as reading it."* The console shows the
reference and a locked affordance, never a blank.

### 2.3 `GET /evidence/{blob_ref}`

`?purpose=<string>` is **required and non-empty**. The platform rejects an
absent purpose with `FORBIDDEN`, and the harness does not supply a default —
supplying one would convert an attributable act into an invisible one, which is
the whole control 12_SECURITY §5.4 describes.

The console prompts the engineer for a purpose string and sends it verbatim. It
is recorded in the Vision OS audit trail with the actor.

Crop bytes are returned as base64 in `crop_b64`, or `null` when the deployment
retains no imagery — which is *"a stated posture rather than a failure"* and is
rendered as such, not as an error.

---

## 3. The stream — `ws://<harness>/ws/v1/session/{session_id}`

One multiplexed socket per validation session. Every message shares an envelope:

```jsonc
{ "seq": 41, "ts_ns": 1712345678000000000, "channel": "detection",
  "type": "layer.tap", "payload": { … } }
```

`seq` is **monotonic, gapless, per-socket**. A client that receives `seq` 44 after
42 has lost 43, and that is the point: it is how the console proves loss rather
than inferring it. See §3.3.

### 3.1 Channels

Each maps to one architectural layer and one engineering panel.

| Channel | Source inside Vision OS | Panel |
|---|---|---|
| `camera` | Camera Manager (M1) records, `CameraChanged` | Live Video |
| `acquisition` | `StreamConnected/Lost`, `EpochAdvanced`, `DecodeFailed`, frame descriptors | Frame Information |
| `detection` | `DetectionCompleted/Failed`, normalized detections | Detections |
| `tracking` | `TrackCreated/Updated/Recovered/Lost/Terminated`, `AssociationFailure` | Tracks |
| `registry` | `ObjectCreated`, `ObjectLifecycleChanged`, `IdentityAsserted/Revised`, `RegionTransition` | Visual Objects |
| `cropping` | `BudgetExhausted`, `GateRejectionSpike`, `CapabilityGap`, crop descriptors | Canonical Crops |
| `understanding` | `UnderstandingFailed`, `ModelFallbackEngaged`, `SchemaDriftSuspected`, results | Understanding Results |
| `synthesis` | `ObservationRejected`, `SchemaViolationSpike` | Observation Log |
| `state` | `PartitionDegraded/Recovered`, `ObservationQuarantined`, `StateRebuilt`, deltas | Vision State |
| `observation` | M14 subscription fan-out — real `Observation` envelopes | Observations |
| `demand` | Demand Registry snapshots | Demand Registry |
| `metrics` | Metrics Engine sample sweep | Metrics |
| `health` | Health Monitor, `HealthChanged`, `CoverageChanged`, `SilentFailureSuspected` | Health |
| `event` | The raw Event Bus firehose — all 61 typed events | Architecture Events |
| `transport` | Session lifecycle, harness-level facts | Replay Controller |

`event` carries the union of everything; the typed channels are conveniences
derived from the same bus subscription. A panel never has to reconstruct a fact
from a different channel's payload.

### 3.2 Message types

| `type` | Meaning |
|---|---|
| `layer.tap` | One layer produced output for one frame |
| `observation` | An `Observation` envelope delivered by M14's subscription hub |
| `state.delta` | `StateDeltaMessage` — which objects/regions changed |
| `coverage.change` | `CoverageChange` — observability changed inside scope |
| `metrics.sample` | A metrics sweep |
| `event` | A typed platform event, `payload` is `Event.payload()` verbatim |
| `gap` | **See §3.3.** Messages were lost |
| `heartbeat` | Liveness + resume cursor |
| `session.state` | Transport state changed (playing/paused/frame index) |
| `error` | `ApiErrorView` on a stream that cannot continue |

### 3.3 `gap` — the most important message in this contract

```jsonc
{ "seq": 44, "ts_ns": …, "channel": "observation", "type": "gap",
  "payload": {
    "start_ns": …, "end_ns": …,
    "reason": "slow_consumer",       // GapReason
    "cameras": ["cam-1"],
    "observations_missed": 118,
    "recoverable": true,             // FIELD, from GapReason.recoverable
    "seq_from": 43, "seq_to": 43     // harness-level sequence loss
  } }
```

> 09_API §3.3: *"A subscriber is never silently skipped… This is V8 applied to
> delivery."*

The console renders a gap as a **visible band on the timeline** — never a
smoothed-over interpolation between the frames on either side. Three of the five
`GapReason` values are recoverable, and for those the console offers a one-click
backfill through `POST /observations/query` over the gap window. For
`platform_blind` it offers nothing, because there is nothing to fetch: the
platform could not see, and *"an empty result over a window with
observable_fraction < 1.0 does not mean nothing happened. It means nothing was
observed."*

### 3.4 Overflow

The harness declares `overflow` per subscription: `conflate`, `drop_with_gap`
(default), or `disconnect`. The three policies `09_API §3.4` forbids — unbounded
buffering, silent drop, stalling the platform — are not expressible in this
contract; there is no enum member for them.

Every drop emits a gap. `tests/websocket/gap-accounting.test.ts` asserts
`messages_dropped <= gaps_emitted * batch`, mirroring the platform's own
`API_GAPS_EMITTED` doc note: *"a dropped count exceeding the gap count over time
means a drop path forgot to record itself."*

---

## 4. The validation control plane (harness-owned)

**Nothing here is Vision OS.** These routes drive a *source* that Vision OS
consumes through P1, and read harness-local state. They cannot write a fact, an
object, or an observation.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/media` | Upload a video (multipart). Returns `MediaAsset` |
| `GET` | `/media` | List uploaded + discovered assets |
| `DELETE` | `/media/{media_id}` | Remove an asset |
| `POST` | `/sessions` | Open a validation session over a media asset |
| `GET` | `/sessions` | List sessions |
| `GET` | `/sessions/{id}` | Session detail |
| `DELETE` | `/sessions/{id}` | Close and tear down |
| `POST` | `/sessions/{id}/transport` | play · pause · step · seek · speed · restart |
| `GET` | `/sessions/{id}/frames/{index}` | Rendered frame, `image/jpeg` (§4.3) |
| `POST` | `/sessions/{id}/faults` | Arm or clear a failure-injection scenario |
| `GET` | `/sessions/{id}/faults` | Armed scenarios |
| `POST` | `/replay/verify` | Run `VisionSystem.verify_replay` → V13 proof |
| `GET` | `/architecture` | Module graph, port bindings, layer order |
| `GET` | `/health` | Harness + Vision OS health |
| `GET` | `/metrics` | Metrics snapshot |
| `GET` | `/reports/{kind}` | Report payload (§5) |

### 4.1 `POST /sessions`

```jsonc
{
  "media_id": "m-01HX…",
  "camera_id": "cam-1",
  "tenant_id": "t-eng",
  "semantics": "archival",        // archival | realtime
  "target_fps": 12.0,
  "deterministic": true,          // §4.2
  "autostart": false
}
```

`semantics: "archival"` is the default and the one that matters. `SourceSemantics.ARCHIVAL`
*"protects completeness and is reproducible"*; `REALTIME` permits dropping. An
engineer validating determinism must be on `archival`, and the console warns —
visibly, not in a tooltip — when a replay comparison is attempted across two
sessions whose semantics differ, because that comparison is meaningless.

### 4.2 Determinism

`deterministic: true` binds the harness's `ReplayClock` instead of a wall clock.
Frames advance the clock by a fixed step derived from `target_fps`; nothing in
the pipeline observes real time. This is what makes **V13** testable:

> *"Same input + config + models ⇒ same observations."*

`POST /replay/verify` then calls the platform's own `VisionSystem.verify_replay`,
which reprojects every partition from the observation log and diffs field by
field. It returns the platform's `ReplayReport` unmodified. The console reports
`mismatches > 0` as a **failure of Vision OS**, never as a console warning — the
metric's own note says a non-zero value *"invalidates every recovery guarantee in
07_STATE section 9.1."*

### 4.3 Frames, and the V12 question

**V12 says pixels stay local; only observations travel.** A validation console
that shows video is in obvious tension with that, so the resolution is explicit
rather than assumed:

- The harness is **co-located with Vision OS**, inside the same trust boundary.
  Frames never cross a deployment edge; they cross a loopback socket to an
  engineering tool that the platform operator runs.
- Frame serving is **off by default** (`VOSVC_SERVE_FRAMES=0`) and is a
  deployment decision, not a console one.
- When on, every frame fetch carries a `purpose` and is written to the same audit
  trail as `get_evidence`, for the same reason: *"it converts imagery access from
  an invisible act into an attributable one."*

The console renders a permanent banner when frame serving is enabled. An
engineering tool may see pixels; it may not do so quietly.

### 4.4 Failure injection

```jsonc
{ "scenario": "occlusion", "params": { "coverage": 0.4 }, "at_frame": 120, "duration_frames": 60 }
```

| Scenario | Injected at | Mechanism |
|---|---|---|
| `blur` | P2 decoder | Gaussian on decoded plane |
| `low_light` | P2 decoder | Gamma + noise |
| `rain` | P2 decoder | Streak overlay |
| `occlusion` | P2 decoder | Opaque region over a fraction of frame |
| `camera_disconnect` | P1 source | `StreamLostError` mid-stream → epoch advance |
| `duplicate_frames` | P1 source | Re-emit prior packet with same PTS |
| `dropped_frames` | P1 source | Skip N packets |
| `freeze` | P1 source | Re-emit identical payload, advancing PTS |
| `slow_camera` | P1 source | Inflate inter-packet delay |
| `restart` | Session | Tear down and re-attach the pipeline |
| `network_delay` | P1 source | Delay packet delivery, preserving order |

Every scenario is injected **at the port boundary** — inside the harness's own P1
or P2 adapter, never by reaching into a Vision OS module. That is what makes the
result meaningful: the platform experiences a genuinely bad camera and responds
with its real degradation ladder. A fault poked into a module would be testing
the poke.

The console asserts the *expected architectural response* per scenario (e.g.
`camera_disconnect` ⇒ `stream.lost` → `stream.epoch_advanced` → a coverage
observation with `status=blind`) and reports a scenario as **unvalidated** if the
response does not arrive. It never repairs, retries, or hides the absence.

---

## 5. Reports

`GET /reports/{kind}` returns structured JSON; the console renders and exports it.
A report **restates recorded facts**. It computes no new ones — no scores, no
grades, no pass/fail invented by the console.

| kind | Restates |
|---|---|
| `replay` | Session transport history, frame ledger, determinism verdict from `verify_replay` |
| `performance` | Metrics Engine series over the session window |
| `observation` | Every observation, with provenance and evidence refs |
| `architecture` | Port bindings, module health, layer ordering, ceiling compliance |
| `failure` | Armed scenarios, expected response, observed response, verdict |
| `latency` | Per-module execution time histograms |
| `regression` | Field-by-field diff of two sessions over the same media |
| `summary` | The eight verification checks, each with its evidence |

The regression report's verdict is a **diff**, not a judgment: it says
"session B produced 14 observations session A did not, here they are", never
"session B is worse". Which is better is a question requiring ground truth the
platform does not have — V1 applies to the tool that validates the platform, too.

---

## 6. What the console may never do

Enforced by `tests/architecture/*`, not by convention:

1. **No import of Vision OS or backend code.** `no-backend-imports.test.ts` walks
   every module in `src/` and fails on any specifier resolving outside the repo.
2. **No business logic.** `no-business-logic.test.ts` fails on threshold
   comparisons against domain attributes, on any `if (dwell > …)`, and on any
   derived semantic verdict. The console may compare a value to a value it was
   *given*; it may not decide what a value means.
3. **No fact synthesis.** The console never constructs an `Observation`, an
   `ObjectView`, or a `Detection` outside `src/simulator/`, which is excluded from
   the production bundle and asserted absent from `dist/`.
4. **No inferred retryability, no inferred staleness, no inferred coverage.** All
   three arrive as fields. Deriving any of them client-side is a test failure.
