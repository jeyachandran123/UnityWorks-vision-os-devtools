"""Per-layer observability taps.

Every layer of Vision OS must be inspectable *"without modifying the Vision OS"*,
and this module is where that is made true. It reads three surfaces the platform
already offers to anyone:

1. **The Event Bus** (M19) — all 61 typed events. Information travels *upward*
   through the bus without an upward dependency, so subscribing is the intended
   way to watch a lower layer from above.
2. **The Metrics Engine** (M21) — the closed metric vocabulary.
3. **The Health Monitor** (M20) — component state.

There is no fourth surface, and in particular there is **no reaching into a
module**. A tap that read `TrackingRuntime._table` would be measuring an
implementation detail, would break on the next refactor, and would make the
console a coupling point that keeps Vision OS from evolving — the opposite of
what a permanent validation tool should be.

**Bounded, always.** Each channel is a ring buffer. An unbounded tap in a
long-running soak grows fastest exactly when the platform is busiest, which is
when an engineer most needs the harness to stay up.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

#: Channel names. Each is one architectural layer and one console panel.
CHANNELS: tuple[str, ...] = (
    "camera",
    "acquisition",
    "detection",
    "tracking",
    "registry",
    "cropping",
    "understanding",
    "synthesis",
    "state",
    "observation",
    "demand",
    "metrics",
    "health",
    "event",
    "transport",
)

#: Event-type prefix → channel. Derived from `kernel/events/events.py`, whose
#: `event_type` strings are already namespaced by owning module. Mapping by
#: prefix rather than by an explicit table of 61 entries means a new event type
#: added to Vision OS lands on the right channel with no change here.
_PREFIX_CHANNEL: tuple[tuple[str, str], ...] = (
    ("stream.", "acquisition"),
    ("privacy.", "acquisition"),
    ("scheduler.", "acquisition"),
    ("buffer.", "acquisition"),
    ("camera.", "camera"),
    ("config.", "transport"),
    ("plugin.", "transport"),
    ("health.", "health"),
    ("runtime.", "transport"),
    ("bus.", "transport"),
    ("model.", "transport"),
    ("detection.", "detection"),
    ("tracking.", "tracking"),
    ("registry.", "registry"),
    ("cropping.", "cropping"),
    ("understanding.", "understanding"),
    ("synthesis.", "synthesis"),
    ("state.", "state"),
)


def channel_for(event_type: str) -> str:
    for prefix, channel in _PREFIX_CHANNEL:
        if event_type.startswith(prefix):
            return channel
    return "event"


@dataclass(slots=True)
class TapRecord:
    """One observed fact, with the sequence number that proves nothing was lost."""

    seq: int
    ts_ns: int
    channel: str
    type: str
    payload: dict[str, Any]
    frame_index: int | None = None

    def to_wire(self) -> dict[str, Any]:
        message = {
            "seq": self.seq,
            "ts_ns": self.ts_ns,
            "channel": self.channel,
            "type": self.type,
            "payload": self.payload,
        }
        if self.frame_index is not None:
            message["frame_index"] = self.frame_index
        return message


class TapBus:
    """Fan-in from Vision OS, fan-out to console sockets.

    Sequence numbers are **global to the bus**, not per channel. A console that
    subscribes to three channels and sees 41, 42, 44 has lost message 43 and
    knows it, even though it cannot know which channel 43 belonged to. Per-channel
    numbering would hide loss whenever a whole channel went quiet, and a
    validation tool that cannot detect its own message loss cannot certify the
    platform's delivery guarantees.
    """

    def __init__(self, *, history: int = 20_000) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._history: dict[str, deque[TapRecord]] = {c: deque(maxlen=history) for c in CHANNELS}
        self._subscribers: list[Callable[[TapRecord], None]] = []
        self._dropped = 0
        self._counts: dict[str, int] = {c: 0 for c in CHANNELS}

    # --- publication -------------------------------------------------------- #

    def publish(
        self,
        channel: str,
        type_: str,
        payload: dict[str, Any],
        *,
        frame_index: int | None = None,
    ) -> TapRecord:
        with self._lock:
            self._seq += 1
            record = TapRecord(
                seq=self._seq,
                ts_ns=time.time_ns(),
                channel=channel if channel in self._history else "event",
                type=type_,
                payload=payload,
                frame_index=frame_index,
            )
            self._history[record.channel].append(record)
            self._counts[record.channel] = self._counts.get(record.channel, 0) + 1
            subscribers = list(self._subscribers)

        for deliver in subscribers:
            try:
                deliver(record)
            except Exception:  # noqa: BLE001 - one slow socket must not stall the bus
                with self._lock:
                    self._dropped += 1
        return record

    def publish_event(self, event: Any) -> TapRecord:
        """Publish one platform `Event`, verbatim.

        `Event.payload()` is the platform's own transport-ready shape. Using it
        rather than re-deriving fields means the console sees exactly what any
        other bus consumer would — the tap adds nothing and removes nothing.
        """
        try:
            payload = event.payload()
        except Exception:  # noqa: BLE001
            payload = {"__repr__": repr(event)}
        event_type = str(payload.get("event_type", type(event).__name__))
        return self.publish(channel_for(event_type), "event", payload)

    # --- subscription ------------------------------------------------------- #

    def subscribe(self, deliver: Callable[[TapRecord], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(deliver)

        def unsubscribe() -> None:
            with self._lock:
                if deliver in self._subscribers:
                    self._subscribers.remove(deliver)

        return unsubscribe

    # --- history ------------------------------------------------------------ #

    def history(
        self, channels: Iterable[str] | None = None, *, since_seq: int = 0, limit: int = 500
    ) -> list[TapRecord]:
        wanted = list(channels) if channels else list(CHANNELS)
        with self._lock:
            collected: list[TapRecord] = []
            for channel in wanted:
                for record in self._history.get(channel, ()):
                    if record.seq > since_seq:
                        collected.append(record)
        collected.sort(key=lambda r: r.seq)
        return collected[-limit:] if limit else collected

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sequence": self._seq,
                "dropped": self._dropped,
                "subscribers": len(self._subscribers),
                "by_channel": dict(self._counts),
            }

    @property
    def sequence(self) -> int:
        with self._lock:
            return self._seq


class EventBusTap:
    """Drains the platform Event Bus onto the tap bus.

    **A pull, not a push.** `EventBus.subscribe(None)` returns a bounded
    `Subscription` that its owner drains; the bus deliberately *"never runs
    subscriber code on the publisher's thread, so a slow or broken subscriber
    cannot stall a producer."* Registering a callback would defeat that, so the
    tap owns a queue and empties it from the session pump.

    The bus's own `Gap` arrives at the head of a drain when events were dropped —
    *"a subscriber cannot drain without learning what it missed."* That gap is
    forwarded verbatim. The harness never fills one in and never smooths one
    over, because a console that hid the bus's own loss could not be trusted to
    report the platform's.
    """

    def __init__(self, bus_tap: TapBus) -> None:
        self._taps = bus_tap
        self._subscription: Any = None
        self.attached = False
        self.attach_error: str | None = None
        self.drained = 0

    def attach(self, platform_bus: Any) -> bool:
        """Open an all-types subscription.

        Failure is **recorded explicitly**, never swallowed. A tap that quietly
        failed to attach would make a healthy platform look silent — the exact
        failure mode V8 exists to prevent, reproduced inside the tool built to
        detect it. The console renders Architecture Events as UNAVAILABLE with
        this reason attached rather than as an empty list.
        """
        try:
            # `None` means every registered type (bus.py `subscribe` docstring).
            self._subscription = platform_bus.subscribe(None)
        except Exception as exc:  # noqa: BLE001
            self.attach_error = (
                f"EventBus.subscribe(None) failed: {type(exc).__name__}: {exc}; "
                f"architecture events are UNAVAILABLE"
            )
            return False

        if not hasattr(self._subscription, "drain"):
            self.attach_error = (
                "EventBus.subscribe returned an object with no drain(); "
                "architecture events are UNAVAILABLE"
            )
            self._subscription = None
            return False

        self.attached = True
        return True

    def drain(self, limit: int | None = None) -> int:
        """Move whatever the bus has queued onto the tap bus."""
        if self._subscription is None:
            return 0
        try:
            events = self._subscription.drain(limit)
        except TypeError:
            events = self._subscription.drain()
        except Exception:  # noqa: BLE001 - a broken drain must not stop the pump
            return 0

        for event in events or ():
            self._taps.publish_event(event)
        self.drained += len(events or ())
        return len(events or ())

    def detach(self) -> None:
        subscription = self._subscription
        self._subscription = None
        self.attached = False
        for closer in ("close", "cancel", "unsubscribe"):
            fn = getattr(subscription, closer, None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    pass
                return


class MetricsTap:
    """Samples the Metrics Engine on a cadence.

    Polls rather than subscribes because M21 is a recording surface, not a
    notifier. The sweep interval is configuration; each sample carries its own
    timestamp so the console plots real spacing rather than assuming the cadence
    was met.
    """

    def __init__(self, tap_bus: TapBus) -> None:
        self._taps = tap_bus
        self._last: dict[str, Any] = {}

    def sweep(self, metrics: Any) -> dict[str, Any]:
        sample = self._read(metrics)
        self._last = sample
        self._taps.publish("metrics", "metrics.sample", sample)
        return sample

    @property
    def last(self) -> dict[str, Any]:
        return dict(self._last)

    @staticmethod
    def _read(metrics: Any) -> dict[str, Any]:
        """Read whatever the engine exposes, without assuming an accessor.

        Returns `{"unavailable": reason}` rather than `{}` when nothing can be
        read. An empty metrics sample and an unreadable metrics engine are
        different facts and the console renders them differently.
        """
        for name in ("snapshot", "collect", "export", "samples", "as_dict"):
            reader = getattr(metrics, name, None)
            if reader is None:
                continue
            try:
                value = reader() if callable(reader) else reader
            except Exception:  # noqa: BLE001
                continue
            if value is None:
                continue
            from .contract import encode

            return {"source": name, "values": encode(value)}
        return {
            "source": None,
            "values": {},
            "unavailable": "MetricsEngine exposes no readable accessor in this build",
        }


@dataclass(slots=True)
class FrameLedger:
    """Every frame the source emitted, in order.

    The timeline's backing store. Holds descriptors, never pixels — a ledger that
    retained payloads would put the whole video in memory twice.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    max_entries: int = 200_000

    def record(self, index: int, frame: Any, *, faults: list[str]) -> dict[str, Any]:
        entry = {
            "frame_index": index,
            "pts_ms": getattr(frame, "pts_ms", 0),
            "width": getattr(frame, "width", 0),
            "height": getattr(frame, "height", 0),
            "bytes": len(getattr(frame, "payload", b"")),
            "is_keyframe": bool(getattr(frame, "is_keyframe", True)),
            "faults": faults,
            "emitted_at_ns": time.time_ns(),
        }
        if len(self.entries) < self.max_entries:
            self.entries.append(entry)
        return entry

    def at(self, index: int) -> dict[str, Any] | None:
        for entry in self.entries:
            if entry["frame_index"] == index:
                return entry
        return None
