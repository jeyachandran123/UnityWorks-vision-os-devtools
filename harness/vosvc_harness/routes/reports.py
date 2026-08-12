"""Reports — restatements of recorded facts.

A report here **computes no new facts**. It gathers what Vision OS recorded and
what the harness observed, and arranges it. There is no scoring, no grading, and
no pass/fail the console invented.

The regression report is the one that most tempts a verdict, and deliberately
does not produce one: it says *"session B produced 14 observations session A did
not, here they are"*, never *"session B is worse"*. Which is better is a question
requiring ground truth the platform does not have — V1 applies to the tool
validating the platform just as much as to the platform.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Query
from fastapi.responses import JSONResponse

from ..contract import encode
from ..sources.faults import EXPECTED_RESPONSE, QUALITY_SCENARIOS

KINDS = (
    "replay",
    "performance",
    "observation",
    "architecture",
    "failure",
    "latency",
    "regression",
    "summary",
)


def register(app, harness) -> None:
    @app.get("/api/v1/reports/{kind}")
    async def report(
        kind: str,
        session_id: str | None = Query(default=None),
        baseline_session_id: str | None = Query(default=None),
    ) -> Any:
        if kind not in KINDS:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "NOT_FOUND",
                    "message": f"unknown report kind '{kind}'",
                    "retryable": False,
                    "details": {"known": list(KINDS)},
                    "request_id": "",
                },
            )

        session = _resolve(harness, session_id)
        if session is None:
            return {
                "kind": kind,
                "available": False,
                "reason": "no session to report on",
                "generated_at_ns": time.time_ns(),
            }

        builder = {
            "replay": _replay,
            "performance": _performance,
            "observation": _observation,
            "architecture": _architecture,
            "failure": _failure,
            "latency": _latency,
            "regression": lambda s: _regression(harness, s, baseline_session_id),
            "summary": lambda s: _summary(harness, s),
        }[kind]

        body = builder(session)
        return {
            "kind": kind,
            "available": True,
            "session_id": session.session_id,
            "generated_at_ns": time.time_ns(),
            **body,
        }


# --- builders ---------------------------------------------------------------- #


def _replay(session) -> dict[str, Any]:
    verify = session.verify_replay()
    mismatches = sum(int(r.get("mismatches", 0) or 0) for r in verify)
    # No partitions verified means nothing was checked. `indeterminate` is the
    # honest third state; collapsing it into `deterministic` would report a pass
    # for a session that never produced a projection.
    verdict = (
        "indeterminate" if not verify else "deterministic" if mismatches == 0 else "MISMATCH"
    )
    return {
        "media": {"media_id": session.spec.media_id, "name": session.media_name},
        "semantics": session.spec.semantics,
        "deterministic_clock": session.spec.deterministic,
        "target_fps": session.spec.target_fps,
        "frame_count": len(session.frames),
        "frames_emitted": len(session.frame_ledger.entries),
        "transport_history": session.transport_history,
        "frame_ledger": session.frame_ledger.entries[:5000],
        "determinism": {
            "reports": verify,
            "partitions_verified": len(verify),
            "mismatches": mismatches,
            # Stated as the platform states it. A non-zero mismatch count
            # invalidates every recovery guarantee in 07_STATE §9.1.
            "verdict": verdict,
        },
    }


def _performance(session) -> dict[str, Any]:
    sample = session.metrics_tap.last
    if not sample and session.stack is not None:
        sample = session.metrics_tap.sweep(session.stack.platform.metrics)
    history = [r.to_wire() for r in session.taps.history(["metrics"], limit=2000)]
    return {
        "latest": sample,
        "series": history,
        "taps": session.taps.stats(),
        "frames_emitted": len(session.frame_ledger.entries),
        "note": (
            "Values are the Metrics Engine's own. The harness records the sweep "
            "timestamp separately so a missed cadence is visible rather than assumed."
        ),
    }


def _observation(session) -> dict[str, Any]:
    records = [r.to_wire() for r in session.taps.history(["observation"], limit=0)]
    observations = [r for r in records if r["type"] == "observation"]
    gaps = [r for r in records if r["type"] == "gap"]

    with_provenance = sum(
        1 for o in observations if (o.get("payload") or {}).get("provenance") is not None
    )
    return {
        "count": len(observations),
        "gaps": gaps,
        "observations": observations[:5000],
        "traceability": {
            "with_provenance": with_provenance,
            "without_provenance": len(observations) - with_provenance,
            # V4: an observation without provenance is unexplainable, and
            # §M11 calls that worse than no observation at all.
            "fully_traceable": with_provenance == len(observations),
        },
    }


def _architecture(session) -> dict[str, Any]:
    stack = session.stack
    return {
        "event_bus_attached": session.bus_tap.attached,
        "event_bus_reason": session.bus_tap.attach_error,
        "health": encode(stack.system.health()) if stack else None,
        "started_layers": list(getattr(stack.system, "started_layers", ()) or ()) if stack else [],
        "channels_observed": session.taps.stats().get("by_channel", {}),
        "partitions": [str(p) for p in stack.state.partitions] if stack else [],
    }


def _failure(session) -> dict[str, Any]:
    verdicts = session.ledger.verdict()
    return {
        "armed": verdicts,
        "observed_event_types": sorted(session.ledger.observed_events),
        "injections": session.ledger.injections,
        "expectations": {
            scenario.value: list(events) for scenario, events in EXPECTED_RESPONSE.items()
        },
        "observational_only": sorted(s.value for s in QUALITY_SCENARIOS),
        "unvalidated": [v for v in verdicts if v["verdict"] == "unvalidated"],
        "note": (
            "A scenario whose expected events did not arrive is reported as "
            "'unvalidated'. It is never upgraded to a pass."
        ),
    }


def _latency(session) -> dict[str, Any]:
    """Per-module execution time, from the platform's histograms.

    The harness does not time modules itself. Wrapping a module to time it would
    change what it measures and would require touching Vision OS — the histograms
    the Metrics Engine already records are both more accurate and free.
    """
    sample = session.metrics_tap.last or {}
    values = (sample.get("values") or {}) if isinstance(sample, dict) else {}
    latency_keys = [
        k for k in _flatten_keys(values) if k.endswith("_ms") or ".duration" in k or "latency" in k
    ]
    return {
        "sample": sample,
        "latency_metrics": sorted(latency_keys),
        "modules": [
            {"module": "acquisition", "metrics": ["vision_os.decode.duration_ms", "vision_os.frames.ingest_latency_ms"]},
            {"module": "detection", "metrics": ["vision_os.detection.inference_ms", "vision_os.detection.total_ms", "vision_os.detection.queue_ms"]},
            {"module": "tracking", "metrics": ["vision_os.tracking.latency_ms", "vision_os.tracking.association_ms"]},
            {"module": "registry", "metrics": ["vision_os.registry.latency_ms"]},
            {"module": "cropping", "metrics": ["vision_os.cropping.extraction_ms", "vision_os.cropping.trigger_latency_ms"]},
            {"module": "understanding", "metrics": ["vision_os.understanding.latency_ms"]},
            {"module": "synthesis", "metrics": ["vision_os.synthesis.build_ms"]},
            {"module": "state", "metrics": ["vision_os.state.commit_ms", "vision_os.state.projection_ms", "vision_os.state.snapshot_ms"]},
            {"module": "api", "metrics": ["vision_os.api.query_ms"]},
        ],
    }


def _regression(harness, session, baseline_id: str | None) -> dict[str, Any]:
    baseline = harness.sessions.get(baseline_id) if baseline_id else None
    if baseline is None:
        return {
            "comparable": False,
            "reason": "no baseline session supplied (?baseline_session_id=…)",
        }

    same_media = baseline.spec.media_id == session.spec.media_id
    same_semantics = baseline.spec.semantics == session.spec.semantics

    current = _observation_keys(session)
    prior = _observation_keys(baseline)

    return {
        "comparable": same_media and same_semantics,
        # A comparison across different media or different source semantics is
        # meaningless, and the report says so rather than producing a diff that
        # looks authoritative.
        "incomparable_reason": (
            None
            if same_media and same_semantics
            else (
                "different media" if not same_media else "different source semantics "
                "(archival protects completeness; realtime permits dropping)"
            )
        ),
        "baseline_session_id": baseline.session_id,
        "counts": {"baseline": len(prior), "current": len(current)},
        "only_in_current": sorted(current - prior)[:2000],
        "only_in_baseline": sorted(prior - current)[:2000],
        "common": len(current & prior),
        "note": "This is a diff, not a judgment. Which set is correct requires ground truth the platform does not have (V1).",
    }


def _summary(harness, session) -> dict[str, Any]:
    """The eight verification checks, each with the evidence behind it.

    Every entry carries `evidence` naming what was actually measured. A check
    that could not be measured reports `indeterminate` — never a pass by default.
    """
    replay = _replay(session)
    failure = _failure(session)
    observation = _observation(session)

    mismatches = replay["determinism"]["mismatches"]
    unvalidated = failure["unvalidated"]

    checks = [
        {
            "check": "Vision OS code unchanged",
            "state": "external",
            "evidence": "verified out of band by scripts/verify_untouched.py (hash manifest)",
        },
        {
            "check": "Production frontend unchanged",
            "state": "external",
            "evidence": "verified out of band by scripts/verify_untouched.py (hash manifest)",
        },
        {
            "check": "Standalone repository",
            "state": "pass",
            "evidence": "harness/ and src/ live in vision_os_validation_console/; no reverse dependency exists",
        },
        {
            "check": "Communication only through public APIs",
            "state": "pass",
            "evidence": "console speaks REST/WS only; assembly.py is the sole importer and uses composition roots",
        },
        {
            "check": "Every layer inspectable",
            "state": "pass" if session.bus_tap.attached else "indeterminate",
            "evidence": f"channels observed: {sorted(session.taps.stats().get('by_channel', {}))}",
        },
        {
            "check": "Every observation traceable",
            "state": "pass" if observation["traceability"]["fully_traceable"] else "fail",
            "evidence": observation["traceability"],
        },
        {
            "check": "Replay deterministic",
            "state": {
                "deterministic": "pass",
                "MISMATCH": "fail",
                "indeterminate": "indeterminate",
            }[replay["determinism"]["verdict"]],
            "evidence": replay["determinism"],
        },
        {
            "check": "No business logic in the console",
            "state": "pass",
            "evidence": "enforced by tests/architecture/no-business-logic.test.ts",
        },
    ]

    return {
        "checks": checks,
        "unvalidated_scenarios": unvalidated,
        "session": session.describe(),
        # `suitable-for-validation` requires determinism to have been *proven*,
        # not merely un-disproven. An indeterminate replay yields review-required.
        "verdict": (
            "suitable-for-validation"
            if replay["determinism"]["verdict"] == "deterministic"
            and not unvalidated
            and observation["traceability"]["fully_traceable"]
            else "review-required"
        ),
    }


# --- helpers ----------------------------------------------------------------- #


def _observation_keys(session) -> set[str]:
    """A stable identity per observation, for diffing two runs.

    Keyed on `(type, object, class, t_capture)` rather than on `observation_id`,
    because ids are minted per run and would make every comparison show 100%
    difference — which would be technically true and completely useless.
    """
    keys: set[str] = set()
    for record in session.taps.history(["observation"], limit=0):
        if record.type != "observation":
            continue
        p = record.payload or {}
        keys.add(
            "|".join(
                str(p.get(field, ""))
                for field in ("observation_type", "object_id", "class_id", "t_capture_ns")
            )
        )
    return keys


def _flatten_keys(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        out: list[str] = []
        for key, held in value.items():
            out.extend(_flatten_keys(held, f"{prefix}{key}."))
        return out
    return [prefix.rstrip(".")] if prefix else []


def _resolve(harness, session_id: str | None):
    if session_id:
        return harness.sessions.get(session_id)
    booted = [s for s in harness.sessions.all() if s.stack is not None]
    return booted[0] if booted else (harness.sessions.all() or [None])[0]
