"""Wire serialization — shapes only, never meanings.

This module implements P32 obligation **T2**: *"A transport translates shapes,
not meanings. Neither adapter here filters a result, aggregates one, or decides
what a consumer should see."*

`encode()` is deliberately **reflective**. It walks whatever the platform hands
it and renders it structurally: a dataclass becomes an object, an enum becomes
its value, an `Instant` becomes integer nanoseconds. It has no table of known
Vision OS types and no per-type projection logic, which means it *cannot*
interpret — there is no place to put a judgment even if someone wanted to. A
field added to `ObjectView` next year appears on the wire without this file
changing, and a field removed disappears; neither is a harness decision.

Three rules the encoding follows, each traceable to the platform:

1. **Time is integer nanoseconds.** `Instant.ns` is an integer and a float would
   lose precision past 2^53 ns (~104 days). Field names gain an `_ns` suffix so a
   consumer can never mistake the unit. `Duration` renders as float milliseconds
   with an `_ms` suffix, matching `Duration.millis`.
2. **Bytes become base64, never text.** A crop is imagery; decoding it as UTF-8
   would corrupt it silently.
3. **Nothing is dropped for being unrecognised.** An unknown object renders as
   its `repr` under a `"__repr__"` key rather than vanishing. A transport that
   silently omitted a field it did not understand would be the exact silent
   failure 10_RELIABILITY §5 is about.
"""

from __future__ import annotations

import base64
import dataclasses
import enum
from collections.abc import Mapping, Sequence
from typing import Any

WIRE_MAJOR = 1
WIRE_VERSION = "1.0.0"

#: The platform's two time wrappers, identified by name rather than by shape.
#:
#: Shape does not separate them: `Instant` and `Duration` are both frozen
#: dataclasses with an integer `ns` field *and* a `millis` property. The only
#: structural difference is that `Instant.millis` returns `int` while
#: `Duration.millis` returns `float` — a distinction that would silently
#: reclassify every timestamp the first time someone changed a `//` to a `/`.
#: Matching the class name is blunt, but it is stable and it is checkable.
_INSTANT_NAMES = frozenset({"Instant"})
_DURATION_NAMES = frozenset({"Duration"})


def _time_kind(value: Any) -> str | None:
    """`"instant"`, `"duration"`, or `None`."""
    if isinstance(value, (int, float, str, bytes, bool)) or value is None:
        return None
    name = type(value).__name__
    if not isinstance(getattr(value, "ns", None), int):
        return None
    if name in _INSTANT_NAMES:
        return "instant"
    if name in _DURATION_NAMES:
        return "duration"
    return None


def _is_instant(value: Any) -> bool:
    return _time_kind(value) == "instant"


def _is_duration(value: Any) -> bool:
    return _time_kind(value) == "duration"


def encode(value: Any) -> Any:
    """Render any platform value as JSON-ready data.

    Never raises on an unexpected shape. A transport that raised would drop the
    one event type it could not render, and a gap in exactly one event type is
    the hardest kind of observability hole to notice.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")

    if isinstance(value, enum.Enum):
        return value.value

    if _is_duration(value):
        return float(value.millis)

    if _is_instant(value):
        return int(value.ns)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(value):
            out.update(_field(f.name, getattr(value, f.name, None)))
        _add_properties(value, out)
        return out

    if isinstance(value, Mapping):
        return {str(k): encode(v) for k, v in value.items()}

    if isinstance(value, (set, frozenset)):
        return sorted(encode(v) for v in value)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [encode(v) for v in value]

    slots = getattr(type(value), "__slots__", None)
    if slots:
        out = {}
        for name in slots:
            if name.startswith("_"):
                continue
            out.update(_field(name, getattr(value, name, None)))
        _add_properties(value, out)
        if out:
            return out

    return {"__repr__": repr(value)}


def _field(name: str, value: Any) -> dict[str, Any]:
    """One field, with the unit encoded into its name.

    ``first_seen`` (an `Instant`) becomes ``first_seen_ns``. The rename is what
    makes the unit impossible to misread on the wire — a consumer reading
    ``first_seen`` and getting nanoseconds where it expected milliseconds is a
    bug that would survive every test until a real deployment.
    """
    if _is_duration(value):
        return {f"{name}_ms": float(value.millis)}
    if _is_instant(value):
        return {f"{name}_ns": int(value.ns)}
    return {name: encode(value)}


#: Derived properties the platform computes and a consumer must not recompute.
#: Each is here because the platform's own docstring says so — `is_stale` exists
#: *"so a consumer cannot receive a stale flag that is itself stale"*, and
#: `retryable` because *"inferring it is how retry storms begin"*.
_DERIVED = (
    "is_stale",
    "complete",
    "fully_observable",
    "recoverable",
    "count",
    "failure_rate",
)


def _add_properties(value: Any, out: dict[str, Any]) -> None:
    kind = type(value)
    for name in _DERIVED:
        if name in out:
            continue
        prop = getattr(kind, name, None)
        if isinstance(prop, property):
            try:
                out[name] = encode(prop.fget(value))  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - a derived value must never break the wire
                pass


# --- error rendering ------------------------------------------------------- #


def encode_error(exc: BaseException, *, request_id: str = "") -> dict[str, Any]:
    """Render a platform error as the consumer's envelope (09_API §8).

    Delegates to Vision OS's own ``error_view`` when the exception is one of its
    typed errors, so the ``code`` a consumer sees is the platform's stable code
    rather than one this harness invented. Codes are contract; messages are not.
    """
    try:
        from app.vision_os.adapters.exposure.transport import error_view
        from app.vision_os.core.errors import VisionOSError

        if isinstance(exc, VisionOSError):
            return encode(error_view(exc, request_id=request_id))
    except Exception:  # noqa: BLE001 - fall through to the generic envelope
        pass

    return {
        "code": "INTERNAL",
        "message": f"{type(exc).__name__}: {exc}",
        "retryable": False,
        "retry_after_ms": None,
        "details": {},
        "request_id": request_id,
    }


#: Maps the platform's stable error codes onto HTTP status. HTTP status is a
#: courtesy for proxies and logs; ``code`` is the contract (docs/CONTRACT.md §1.1).
HTTP_STATUS: Mapping[str, int] = {
    "FORBIDDEN": 403,
    "TENANT_SCOPE_VIOLATION": 403,
    "NOT_FOUND": 404,
    "STATE_NOT_FOUND": 404,
    "INVALID_SCOPE": 400,
    "WINDOW_TOO_LARGE": 400,
    "UNSUPPORTED_VERSION": 400,
    "EVIDENCE_EXPIRED": 410,
    "OVERLOADED": 429,
    "INTERNAL": 500,
}


def status_for(code: str) -> int:
    return HTTP_STATUS.get(code, 400)
