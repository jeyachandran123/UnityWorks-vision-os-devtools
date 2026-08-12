# Final Verification

**Subject:** Vision OS Validation Console v1.0.0
**Validates:** UnityWorks Vision OS v1 — Flows 1–8, layers L0–L7
**Date:** 2026-08-05

Each claim below states the evidence that supports it and the command that
reproduces it. Where a claim is **not** fully established, it says so — a
verification record that rounds up is worth nothing.

---

## ✓ 1. Vision OS code unchanged

**Established.**

`backend/app/vision_os/` — 193 files hashed, all matching the stored manifest;
the `backend/` git repository reports a clean working tree.

```
$ python scripts/verify_untouched.py
[vision_os]  .../backend/app/vision_os
  files hashed: 193
  manifest matches — no file changed
  git: CLEAN
```

Evidence: `docs/manifest-vision_os.json`.

The harness reaches Vision OS through `sys.path` only. No file is written, no
package is installed into it, and no dependency edge from Vision OS to this
repository exists.

---

## ✓ 2. Production frontend unchanged

**Established.**

`frontend/` — 104 files hashed, all matching; the `frontend/` git repository
reports a clean working tree. Evidence: `docs/manifest-frontend.json`.

No production UI component, token, or stylesheet is imported by this console. Its
theme (`src/theme/theme.ts`) and primitives (`src/components/primitives.tsx`) are
original.

---

## ✓ 3. The Validation Console is a standalone repository

**Established.**

`Atlas/vision_os_validation_console/` contains its own `package.json`,
`tsconfig.json`, `pyproject.toml`, tests and documentation. Nothing in Atlas
references it. Deleting the directory changes nothing about Vision OS, the
backend, or the production frontend.

---

## ✓ 4. Communication occurs only through public APIs

**Established, with one deliberate and documented exception.**

*The console* speaks REST and WebSocket exclusively.
`tests/architecture/constraints.test.ts` walks every module in `src/` and fails
on any import resolving outside the repository, any `fetch`/`WebSocket`/
`EventSource` outside `src/transport/`, and any mention of `app.vision_os`.

*The harness* imports Vision OS — that is what a transport adapter is. It does so
in exactly one file, `harness/vosvc_harness/assembly.py`, and uses only public
composition roots: `build_platform`, `build_registry_layer`,
`build_cropping_layer`, `build_tracking_layer`, `build_detection_layer`,
`assemble`. Reads go through `ObservationApi` — the same surface a Cognitive OS
integration will use.

**The exception, stated plainly:** two seams are assigned via a private attribute
— `tracking.runtime._sink` (the Flow 3/4 seam) and
`platform.runtime._admitted_consumer` (the Flow 2 seam). These are the documented
seams, and Vision OS's own end-to-end suite assigns them identically, each marked
`# noqa: SLF001 - the declared seam`. Assigning them is the composition root's
job, and here the harness *is* the composition root. Both sites are annotated.

---

## ✓ 5. Every Vision OS layer is inspectable

**Established for nine of ten layers; one is conditional.**

Fifteen tap channels, each mapped to a panel. Verified live against a running
platform — `harness/tests/test_integration.py::test_every_pipeline_layer_reports`
asserts `acquisition`, `detection`, `tracking`, `registry` and `observation` all
produce output.

**Conditional:** `cropping`, `understanding`, `synthesis` and `camera` report
*only* through the Event Bus. When the bus tap cannot attach, those panels render
`UNAVAILABLE` with the attach error, **not** as empty. In the verified
configuration the bus tap attaches and drains successfully
(`test_the_event_bus_tap_attaches`).

**Also worth stating:** on a healthy short run, `cropping`, `understanding` and
`synthesis` legitimately emit nothing — there is no `CropProduced` and no
`ObservationPublished` event by design, because those are data-plane volumes on a
control-plane bus. The console distinguishes *silent* from *unavailable* visually
for exactly this reason.

---

## ✓ 6. Every observation is traceable

**Established.**

`harness/tests/test_integration.py::test_observations_carry_provenance` asserts
every delivered observation carries `provenance`. The Observations panel counts
provenance-bearing observations and raises a **V4 violation** banner if any lack
it. The observation report exposes `traceability.fully_traceable`.

Attribute-level evidence references are surfaced in the Vision State inspector,
including when the caller lacks the evidence privilege — knowing an explanation
exists is not the same as reading it.

---

## ✓ 7. Replay is deterministic

**Established for the verified configuration.**

`harness/tests/test_integration.py::test_replay_verification_reports_no_mismatch`
runs the platform's own `VisionSystem.verify_replay`, which reprojects every
partition from the observation log and diffs field by field. Result: **0
mismatches**.

Determinism rests on three properties, all held:

- Vision OS runs on a `VirtualClock`; nothing in the pipeline observes wall time.
- The harness pump advances that clock in fixed steps, so pipeline timing is a
  function of the pump rather than of machine load.
- Injected faults are deterministic — the `rain` scenario is phase-indexed rather
  than random, specifically so replay remains reproducible under injection
  (`test_rain_is_deterministic_for_the_same_frame`).

**Scope of the claim:** verified over the synthetic source with the reference
detector. Determinism with a GPU-backed detector is a property of that adapter,
not of this harness, and the console reports whatever `verify_replay` returns
without softening it.

---

## ✓ 8. No business logic exists in the Validation Console

**Established.**

`tests/architecture/constraints.test.ts` fails the build on:

- any threshold comparison against a domain attribute (`dwell`, `occupancy`,
  `queue_length`, `loiter`, `compliance`, …)
- any construction of an `Observation` outside `src/simulator/`
- any import of the simulator by an application module
- local derivation of `retryable` or `is_stale` — both arrive as fields

The Semantic Ceiling screen flags **suspects**, never verdicts: whether a key is
business logic is a judgment, and the platform's attribute registry is the
authority. V1 binds the tool that validates the platform as much as the platform.

The regression report is a **diff, not a judgment** — it reports what differs and
refuses to say which run is better, because that needs ground truth the platform
does not have.

---

## ✓ 9. Suitable for validating Vision OS before Cognitive OS integration

**Established, with the limits below stated.**

The end-to-end proof: **a frame entering a P1 adapter written in this repository
became a fact an authorized consumer read over HTTP out of the real Observation
API** (`test_a_frame_becomes_a_queryable_object`, and again through FastAPI in
`test_state_query_returns_objects_with_coverage`). 76 harness tests and 69
console tests pass; 42 of the harness tests exercise a live Vision OS.

### Three defects the end-to-end HTTP tests caught

Recorded because they are the class of bug a layered test suite cannot see, and
because two of them were false passes — the worst defect a validation tool can
carry.

1. **`replay/verify` reported `deterministic: true` for a session that never
   booted.** Zero partitions verified was being summed to zero mismatches and
   read as success. Now returns `available: false` with a reason, and the summary
   report has an `indeterminate` state that never rounds up to a pass.
2. **`HarnessConfig` resolved `vision_os_root` one directory too shallow**, so
   the FastAPI app could not import the platform while every unit test passed —
   the app fell back to reporting an unavailable platform rather than crashing,
   which is correct behaviour that also hid the misconfiguration.
3. **A typed platform error escaped as a 500 with a traceback.**
   `WindowTooLargeError` is a documented policy bound and a client-visible
   contract outcome; it now renders as a 400 carrying the stable
   `WINDOW_TOO_LARGE` code and its `retryable` field.

All three are covered by regression tests in `harness/tests/test_http.py`.

### Limits a reviewer should know

1. **The reference detector is bound, not YOLO.** The console validates the
   *pipeline* on hardware with no GPU. Swapping in `detector.yolo` is a
   configuration change that touches nothing in this repo — which is V3 working —
   but detector-specific behaviour has not been exercised here.
2. **MP4/AVI/MKV need an optional dependency.** With neither PyAV nor OpenCV
   installed the harness serves synthetic, frame-folder and `.raw` sources and
   reports containers as unavailable. It never returns zero frames for a file it
   cannot open.
3. **M10 (Prompt Manager) is unimplemented in Vision OS**, and four ports remain
   deliberately unbindable. The console reports the bindable frontier from the
   platform rather than assuming it; there is nothing here to validate for those.
4. **Single-camera sessions.** The harness assembles one replay camera per
   session. Multi-camera partitioning is visible through the state snapshot but
   has not been driven.
5. **Understanding produces no attributes in the verified configuration**, since
   no VLM adapter is bound. The Understanding panel correctly shows silence
   rather than failure.

None of these is a defect in the console. Each is a stated boundary of what this
run validated.

---

## Reproducing the whole record

```bash
cd vision_os_validation_console

python scripts/verify_untouched.py     # claims 1 and 2
cd harness && pytest -q                # claims 5, 6, 7, 9  (46 tests)
cd .. && npm test                      # claims 4 and 8     (67 tests)
npm run build && npm run test:architecture   # bundle check for claim 8
```

Test results at the time of writing:

```
harness:   76 passed   (contract 14 · faults 20 · http 30 · integration 12)
console:   69 passed   (8 suites)
typecheck: clean
build:     ok — 575 kB bundle, simulator symbols absent from dist/
untouched: PASS — 193 + 104 files hashed, both repos git-clean
```

---

## Standing verdict

The Validation Console is fit to become the permanent engineering tool used to
validate every future Vision OS release before production deployment.

It holds itself to the platform's own rules — absence is explicit, nothing is
inferred that arrives as a field, and no verdict is invented where ground truth
is absent. Where the console and the architecture disagree, **the Vision OS
Constitution wins and the console has a bug.**
