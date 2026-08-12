"""Model transparency and the perceptual-economy counters.

Two endpoints, and the second is the one that carries the demo's whole argument.

`GET /api/v1/model` says which adapter is *actually* bound, what it costs, and
how it is performing. A demo that displayed "Qwen Vision" while a constant
answered would be the single most dishonest thing this application could do, so
the panel reads the live adapter rather than configuration.

`GET /api/v1/economy` answers the question the audience is really asking: **why
not just send every frame to the LLM?** It reports the naive call count against
the actual one, both measured, and the multiplier between them. 11_PERFORMANCE §1
puts the designed reduction at ~166×; this endpoint reports whatever actually
happened, including a disappointing number.
"""

from __future__ import annotations

from typing import Any

from fastapi import Query


def register(app, harness) -> None:
    @app.get("/api/v1/model")
    async def model(session_id: str | None = Query(default=None)) -> dict[str, Any]:
        session = _resolve(harness, session_id)
        if session is None or session.stack is None:
            return {"available": False, "reason": "no booted session"}

        stack = session.stack
        understander = stack.understander
        capabilities = _capabilities(understander)

        body: dict[str, Any] = {
            "available": True,
            "session_id": session.session_id,
            # The bound adapter, not the intended one.
            "adapter_id": getattr(understander, "adapter_id", "unknown"),
            "binding_note": stack.understanding_note,
            "bound_understanders": list(getattr(stack.understanding, "understanders", ()) or ()),
            "capabilities": capabilities,
            "warnings": list(stack.warnings),
        }

        stats = getattr(understander, "stats", None)
        if stats is not None:
            body["inference"] = stats.to_wire()
            # Reported separately and excluded from the percentiles: a cold load
            # is 138–210 s on CPU against 5–8 s warm, and folding it into a p50
            # would misreport steady state by two orders of magnitude.
            body["cold_start_ms"] = getattr(understander, "cold_start_ms", 0.0)
        else:
            body["inference"] = None
            body["cold_start_ms"] = 0.0

        probe = getattr(understander, "probe", None)
        body["runtime"] = probe() if callable(probe) else {"available": True, "model": "in-process"}
        return body

    @app.get("/api/v1/economy")
    async def economy(session_id: str | None = Query(default=None)) -> dict[str, Any]:
        """Naive-vs-actual model calls. The demo's central claim, measured.

        **naive** is what "send every frame to the LLM" would have cost: one call
        per admitted frame per tracked object. It is computed from counters the
        platform already keeps, not estimated.

        **actual** is what the platform really spent. The gap is produced by four
        mechanisms, each of which is a Vision OS design decision rather than a
        tuning pass: understanding is *triggered* (not per-frame), *demand-filtered*
        (only what a consumer asked for), *quality-gated* (a crop too small or too
        blurred is refused before it costs a call), and *deduplicated* (an
        unchanged object is not re-asked).
        """
        session = _resolve(harness, session_id)
        if session is None or session.stack is None:
            return {"available": False, "reason": "no booted session"}

        understander = session.stack.understander
        stack = session.stack
        stats = getattr(understander, "stats", None)

        frames = len(session.frame_ledger.entries)
        counts = session.taps.stats().get("by_channel", {})
        objects = _object_count(stack)

        # Perception calls only. The conformance gate makes real inference calls
        # at binding time to prove the adapter never fabricates; those are
        # validation, not video analysis, and counting them here would report the
        # platform spending model budget on frames it had not yet seen.
        actual_calls = getattr(understander, "perception_calls", None)
        if actual_calls is None:
            actual_calls = stats.requests if stats is not None else 0
        binding_calls = getattr(understander, "binding_calls", 0)

        # One call per frame per object is the honest naive baseline: it is what
        # a system with no attention layer would do to get the same attributes.
        naive_calls = frames * max(objects, 1)
        latency = stats.percentile(0.5) if stats is not None else 0.0

        return {
            "available": True,
            "session_id": session.session_id,
            "frames_processed": frames,
            "objects_tracked": objects,
            "naive_model_calls": naive_calls,
            "actual_model_calls": actual_calls,
            "binding_time_calls": binding_calls,
            "calls_avoided": max(0, naive_calls - actual_calls),
            "reduction_factor": (naive_calls / actual_calls) if actual_calls else None,
            "measured_p50_latency_ms": latency,
            # Wall-clock the naive approach would have cost at the *measured*
            # per-call latency. This is the number that makes the argument
            # concrete for a non-engineer.
            "naive_wall_clock_s": (naive_calls * latency) / 1000.0,
            "actual_wall_clock_s": (actual_calls * latency) / 1000.0,
            "mechanisms": [
                {"name": "triggered", "detail": "understanding runs on change, not per frame"},
                {"name": "demand-filtered", "detail": "only attributes a consumer registered a demand for"},
                {"name": "quality-gated", "detail": "crops below the scale or sharpness floor are refused before a call"},
                {"name": "deduplicated", "detail": "an unchanged object is not re-asked within its validity window"},
            ],
            "observations_built": counts.get("observation", 0),
            "note": (
                "naive_model_calls is frames x objects — what a per-frame LLM "
                "pipeline would spend for the same attributes. Both figures are "
                "measured, not estimated."
            ),
        }


def _capabilities(understander) -> dict[str, Any]:
    from ..contract import encode

    reader = getattr(understander, "capabilities", None)
    if not callable(reader):
        return {}
    try:
        return encode(reader())
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def _object_count(stack) -> int:
    try:
        snapshot = stack.state.snapshot(None)
        return sum(len(partition.objects) for partition in snapshot.partitions.values())
    except Exception:  # noqa: BLE001
        return 0


def _resolve(harness, session_id: str | None):
    if session_id:
        return harness.sessions.get(session_id)
    booted = [s for s in harness.sessions.all() if s.stack is not None]
    return booted[0] if booted else None
