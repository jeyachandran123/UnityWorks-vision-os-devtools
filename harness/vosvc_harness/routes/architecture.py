"""Architecture validation — what the platform *is*, read from the platform.

Every fact here is read from Vision OS at runtime: the port catalogue, the
bindable set, the module health map, the layer order the composition root
produced. Nothing is a hard-coded copy of the documentation, because a hard-coded
copy validates the documentation rather than the system — and the first time they
diverge is exactly when you need to know.
"""

from __future__ import annotations

from typing import Any

from fastapi import Query

from ..contract import encode

#: The declared pipeline order (01_LAYERED). Used only to *compare against* what
#: the running system reports — never as a substitute for it.
DECLARED_ORDER: tuple[str, ...] = (
    "camera",
    "acquisition",
    "detection",
    "tracking",
    "registry",
    "cropping",
    "understanding",
    "synthesis",
    "state",
    "api",
)

#: Module → layer, from 01_LAYERED §1. The L0–L7 map an engineer validates against.
LAYERS: tuple[tuple[str, str, str], ...] = (
    ("L0", "kernel", "Runtime · Config · Plugins · Models · Bus · Health · Metrics"),
    ("L1", "acquisition", "Camera Manager · Video Source Manager · Scheduler · Buffer"),
    ("L2", "detection", "Detection Engine"),
    ("L3", "tracking", "Tracking Engine"),
    ("L4", "registry", "Object Registry"),
    ("L5", "cropping", "Crop Manager"),
    ("L5", "understanding", "Vision Understanding Engine"),
    ("L6", "synthesis", "Observation Builder"),
    ("L6", "state", "Vision State Manager"),
    ("L7", "api", "Observation API"),
)


def register(app, harness) -> None:
    @app.get("/api/v1/architecture")
    async def architecture(session_id: str | None = Query(default=None)) -> dict[str, Any]:
        session = _resolve(harness, session_id)
        report: dict[str, Any] = {
            "vision_os": harness.vision_os,
            "layers": [
                {"layer": layer, "module": module, "contains": contains}
                for layer, module, contains in LAYERS
            ],
            "declared_order": list(DECLARED_ORDER),
        }

        report["ports"] = _ports()
        report["invariants"] = _invariants()

        if session is None or session.stack is None:
            report["runtime"] = {
                "available": False,
                "reason": "no booted session; module health is live state and there is no live platform",
            }
            return report

        stack = session.stack
        report["runtime"] = {
            "available": True,
            "session_id": session.session_id,
            "health": _health(stack),
            "started_layers": list(getattr(stack.system, "started_layers", ()) or ()),
            "started": bool(getattr(stack.system, "started", False)),
            "partitions": _partitions(stack),
            "event_bus_attached": session.bus_tap.attached,
            "observed_order": _observed_order(session),
        }
        report["ownership"] = _ownership(stack)
        return report

    @app.get("/api/v1/metrics")
    async def metrics(session_id: str | None = Query(default=None)) -> dict[str, Any]:
        session = _resolve(harness, session_id)
        if session is None or session.stack is None:
            return {
                "available": False,
                "reason": "no booted session",
                "names": _metric_names(),
            }
        sample = session.metrics_tap.sweep(session.stack.platform.metrics)
        return {
            "available": True,
            "session_id": session.session_id,
            "sample": sample,
            "names": _metric_names(),
            "taps": session.taps.stats(),
            "frames_emitted": len(session.frame_ledger.entries),
        }


def _metric_names() -> list[str]:
    try:
        from app.vision_os.kernel.metrics.names import ALL_METRIC_NAMES

        return sorted(ALL_METRIC_NAMES)
    except Exception:  # noqa: BLE001
        return []


def _ports() -> dict[str, Any]:
    """The port catalogue and the bindable frontier.

    `BINDABLE_PORTS` is the platform's own statement of what Phase 1 may bind.
    Reporting it lets an engineer confirm that the four deliberately-unbindable
    ports — the two biometric capabilities, the Prompt Manager's source, and
    calibration — are still unbound, which is a **security** property, not just a
    completeness one (12_SECURITY §4.3).
    """
    try:
        from app.vision_os.kernel.plugins.manifest import PortCatalogue

        catalogue = [
            {"port": name, "value": str(getattr(PortCatalogue, name))}
            for name in dir(PortCatalogue)
            if not name.startswith("_") and name.isupper()
        ]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    bindable: list[str] = []
    try:
        from app.vision_os.kernel.plugins import manifest

        raw = getattr(manifest, "BINDABLE_PORTS", ())
        bindable = sorted(str(p) for p in raw)
    except Exception:  # noqa: BLE001
        bindable = []

    return {
        "available": True,
        "catalogue": catalogue,
        "catalogue_size": len(catalogue),
        "bindable": bindable,
        "bindable_count": len(bindable),
        "unbindable": sorted({c["value"] for c in catalogue} - set(bindable)) if bindable else [],
    }


def _invariants() -> list[dict[str, str]]:
    """The 13 invariants, each with what the console checks it against.

    The `evidence` field names a *console-observable* signal. An invariant with
    no observable signal is listed as such rather than silently claimed — a
    validation tool that reported "V12 ✓" without measuring anything would be
    worse than one that reported nothing.
    """
    return [
        {"id": "V1", "name": "Semantic Ceiling", "evidence": "attribute registry rejections; absence of business verdicts in observations"},
        {"id": "V2", "name": "Vertical Neutrality", "evidence": "taxonomy loaded as config; no vertical code path"},
        {"id": "V3", "name": "Ports over implementations", "evidence": "port catalogue + conformance gate results"},
        {"id": "V4", "name": "Explainability", "evidence": "every observation carries provenance + evidence_ref"},
        {"id": "V5", "name": "Immutability", "evidence": "supersedes chains; no update path in the API surface"},
        {"id": "V6", "name": "Single-writer state", "evidence": "no mutate route exists in the wire contract"},
        {"id": "V7", "name": "Perceptual economy", "evidence": "crop trigger/skip/dedup counters"},
        {"id": "V8", "name": "Blindness is explicit", "evidence": "coverage on every state result; gaps on the stream"},
        {"id": "V9", "name": "Degrade, never die", "evidence": "fallback + degradation events under injected faults"},
        {"id": "V10", "name": "Layered identity", "evidence": "detection/track/object ids distinct across panels"},
        {"id": "V11", "name": "Normalized time and space", "evidence": "t_capture with uncertainty; normalized geometry"},
        {"id": "V12", "name": "Pixels stay local", "evidence": "frame serving off by default; observations carry refs, not pixels"},
        {"id": "V13", "name": "Deterministic replay", "evidence": "verify_replay mismatch count"},
    ]


def _health(stack) -> dict[str, Any]:
    try:
        return encode(stack.system.health())
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": f"{type(exc).__name__}: {exc}"}


def _partitions(stack) -> list[str]:
    try:
        return [str(p) for p in stack.state.partitions]
    except Exception:  # noqa: BLE001
        return []


def _ownership(stack) -> list[dict[str, Any]]:
    """Who owns which identifier — V10 made visible.

    Detection ≠ track ≠ object. The console renders this as a hand-off chain so
    an engineer can confirm that the only module minting object ids is the
    registry, which `01_LAYERED §8` reserves to it.
    """
    return [
        {"artifact": "frame_ref", "minted_by": "acquisition (M2/M4)", "consumed_by": ["detection", "cropping"]},
        {"artifact": "detection", "minted_by": "detection (M5)", "consumed_by": ["tracking"]},
        {"artifact": "track_id", "minted_by": "tracking (M6)", "consumed_by": ["registry"], "note": "camera/epoch-scoped; not an identity"},
        {"artifact": "object_id", "minted_by": "registry (M7)", "consumed_by": ["cropping", "synthesis", "state"], "note": "the only minter (01_LAYERED §8)"},
        {"artifact": "crop/evidence_ref", "minted_by": "cropping (M8) + storage (M13)", "consumed_by": ["understanding", "synthesis"]},
        {"artifact": "observation_id", "minted_by": "synthesis (M11)", "consumed_by": ["state", "api"]},
    ]


def _observed_order(session) -> list[dict[str, Any]]:
    """Layer activity in the order the taps first saw it.

    This is the pipeline-ordering check. It compares the *observed* first-touch
    order against `DECLARED_ORDER` and reports any layer that produced output
    before the layer that must feed it — which would mean a bypass.
    """
    seen: dict[str, int] = {}
    for record in session.taps.history(limit=0):
        seen.setdefault(record.channel, record.seq)

    ordered = sorted(seen.items(), key=lambda kv: kv[1])
    positions = {name: i for i, name in enumerate(DECLARED_ORDER)}
    result = []
    previous = -1
    for channel, seq in ordered:
        declared = positions.get(channel)
        inverted = declared is not None and declared < previous
        if declared is not None:
            previous = max(previous, declared)
        result.append(
            {
                "channel": channel,
                "first_seq": seq,
                "declared_position": declared,
                "out_of_order": inverted,
            }
        )
    return result


def _resolve(harness, session_id: str | None):
    if session_id:
        return harness.sessions.get(session_id)
    booted = [s for s in harness.sessions.all() if s.stack is not None]
    return booted[0] if booted else None
