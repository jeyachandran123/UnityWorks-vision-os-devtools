"""Failure injection — at the port boundary, never inside a module.

Every scenario in docs/CONTRACT.md §4.4 is implemented here, in the harness's own
P1 source or P2 decoder. Nothing reaches into a Vision OS module to make it
misbehave, and that restraint is the entire value of the exercise: the platform
experiences a genuinely bad camera and answers with its **real** degradation
ladder. A fault poked into `TrackingRuntime` would be testing the poke.

Each scenario declares the architectural response it expects. The harness
records what actually arrived and reports a scenario as `unvalidated` when the
expected response does not — it never repairs, retries, or quietly passes. An
injection framework that hid a missing response would be validating itself.
"""

from __future__ import annotations

import enum
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field


class Scenario(enum.Enum):
    """The eleven validation scenarios. Closed, like every vocabulary here."""

    BLUR = "blur"
    LOW_LIGHT = "low_light"
    RAIN = "rain"
    OCCLUSION = "occlusion"
    CAMERA_DISCONNECT = "camera_disconnect"
    DUPLICATE_FRAMES = "duplicate_frames"
    DROPPED_FRAMES = "dropped_frames"
    FREEZE = "freeze"
    SLOW_CAMERA = "slow_camera"
    RESTART = "restart"
    NETWORK_DELAY = "network_delay"

    @property
    def stage(self) -> str:
        """Which port the fault is injected at."""
        if self in _DECODER_STAGE:
            return "decoder"
        if self is Scenario.RESTART:
            return "session"
        return "source"


_DECODER_STAGE = frozenset(
    {Scenario.BLUR, Scenario.LOW_LIGHT, Scenario.RAIN, Scenario.OCCLUSION}
)


#: What Vision OS is architecturally obliged to do for each scenario.
#:
#: These are not guesses. Each is traceable to a specification clause, and the
#: harness asserts them rather than assuming them — an unvalidated expectation is
#: reported as such, which is what makes this a validation tool rather than a
#: demo.
EXPECTED_RESPONSE: dict[Scenario, tuple[str, ...]] = {
    Scenario.CAMERA_DISCONNECT: (
        "stream.lost",
        "stream.epoch_advanced",
        "health.coverage_changed",
    ),
    Scenario.DROPPED_FRAMES: ("health.coverage_changed",),
    Scenario.SLOW_CAMERA: ("scheduler.sustained_drop",),
    Scenario.FREEZE: ("health.silent_failure_suspected",),
    Scenario.DUPLICATE_FRAMES: (),
    Scenario.NETWORK_DELAY: (),
    Scenario.RESTART: ("runtime.pipeline_detached", "runtime.pipeline_attached"),
    Scenario.BLUR: (),
    Scenario.LOW_LIGHT: (),
    Scenario.RAIN: (),
    Scenario.OCCLUSION: (),
}

#: Why an empty expectation is *not* a gap in the test plan.
#:
#: `blur`, `low_light`, `rain` and `occlusion` degrade image quality without
#: breaking the stream. The Semantic Ceiling (V1) forbids the platform from
#: concluding "it is raining" — that is interpretation. What it *does* is grade
#: crop quality and reject at the gate, which surfaces as `cropping.gate_rejection_spike`
#: **only if** the degradation is severe enough. Asserting the event
#: unconditionally would be asserting a threshold the architecture deliberately
#: leaves to configuration, so the harness records the quality-grade distribution
#: and lets the engineer read it.
QUALITY_SCENARIOS = frozenset(
    {Scenario.BLUR, Scenario.LOW_LIGHT, Scenario.RAIN, Scenario.OCCLUSION}
)


@dataclass(slots=True)
class FaultSpec:
    """One armed scenario.

    `at_frame` is a **media frame index** — the same number the timeline shows
    and the same one `sequence_hint` carries on every packet. It is emphatically
    not a count of packets the injector has seen: those two diverge the moment a
    scenario drops or duplicates a packet, and an engineer who armed a fault at
    frame 400 would then be watching it fire somewhere else entirely.
    """

    scenario: Scenario
    at_frame: int = 0
    duration_frames: int = 0
    """0 means "until cleared"."""

    params: dict[str, float] = field(default_factory=dict)

    armed_at_seq: int = 0
    """Tap sequence when this was armed. See `FaultLedger.verdict`."""

    def active_at(self, index: int) -> bool:
        if index < self.at_frame:
            return False
        if self.duration_frames <= 0:
            return True
        return index < self.at_frame + self.duration_frames

    def number(self, key: str, default: float) -> float:
        try:
            return float(self.params.get(key, default))
        except (TypeError, ValueError):
            return default


@dataclass(slots=True)
class FaultLedger:
    """Armed scenarios plus what was actually observed.

    The ledger is the failure report. It records the expectation and the
    observation separately and never reconciles them — a scenario whose expected
    events did not arrive stays visibly unvalidated.
    """

    specs: list[FaultSpec] = field(default_factory=list)
    injections: list[dict] = field(default_factory=list)

    #: event_type → tap sequence at which it was **first** seen.
    #:
    #: A set of "events ever observed" is not enough. `health.coverage_changed`
    #: fires during ordinary startup, so a scenario expecting it would be marked
    #: `validated` by an event that happened *before* the fault was armed — a
    #: false pass, which is the single worst defect a validation tool can have.
    #: Recording *when* each type first appeared lets the verdict ask the only
    #: question that matters: did it arrive **after** the injection?
    first_seen_seq: dict[str, int] = field(default_factory=dict)

    #: event_type → tap sequence at which it was **most recently** seen. This is
    #: what a verdict actually consults; `first_seen_seq` exists so the console
    #: can show when a signal started as well as whether it recurred.
    latest_seq: dict[str, int] = field(default_factory=dict)

    def arm(self, spec: FaultSpec) -> None:
        self.specs = [s for s in self.specs if s.scenario is not spec.scenario]
        self.specs.append(spec)

    def clear(self, scenario: Scenario | None = None) -> None:
        if scenario is None:
            self.specs.clear()
        else:
            self.specs = [s for s in self.specs if s.scenario is not scenario]

    def active(self, index: int) -> list[FaultSpec]:
        return [s for s in self.specs if s.active_at(index)]

    def note_injection(self, scenario: Scenario, index: int, detail: str) -> None:
        self.injections.append(
            {"scenario": scenario.value, "frame_index": index, "detail": detail}
        )

    def note_event(self, event_type: str, seq: int) -> None:
        self.first_seen_seq.setdefault(event_type, seq)
        self.latest_seq[event_type] = seq

    def observed_after(self, event_type: str, seq: int) -> bool:
        """Did this event type occur after `seq`?

        Uses the most recent occurrence, not the first. An event that fires
        during startup *and* again under injection must satisfy the scenario;
        one that fired only during startup must not.
        """
        latest = self.latest_seq.get(event_type)
        return latest is not None and latest > seq

    @property
    def observed_events(self) -> set[str]:
        return set(self.first_seen_seq)

    def verdict(self) -> list[dict]:
        """Per-scenario verdict. Three states, and no fourth.

        `validated` — every expected event arrived **after** this scenario was
        armed.
        `unvalidated` — at least one did not. **Not** a pass, and never upgraded
        to one.
        `observational` — the scenario mandates no event; the engineer reads the
        crop-quality distribution instead (see `QUALITY_SCENARIOS`).
        """
        report = []
        for spec in self.specs:
            expected = EXPECTED_RESPONSE.get(spec.scenario, ())
            missing = [
                e for e in expected if not self.observed_after(e, spec.armed_at_seq)
            ]
            injections = [
                i for i in self.injections if i["scenario"] == spec.scenario.value
            ]
            if not expected:
                state = "observational"
            elif not injections:
                # Nothing was injected, so nothing was tested. Reporting
                # `validated` here would certify a scenario that never ran.
                state = "not_reached"
            elif missing:
                state = "unvalidated"
            else:
                state = "validated"
            report.append(
                {
                    "scenario": spec.scenario.value,
                    "stage": spec.scenario.stage,
                    "at_frame": spec.at_frame,
                    "duration_frames": spec.duration_frames,
                    "params": dict(spec.params),
                    "armed_at_seq": spec.armed_at_seq,
                    "expected_events": list(expected),
                    "missing_events": missing,
                    "injections": injections,
                    "verdict": state,
                }
            )
        return report


# --- pixel-level degradations ---------------------------------------------- #
#
# Pure byte transforms over a BGR24 buffer. No numpy, no OpenCV — the harness
# must be able to inject a fault on a machine with no optional dependency
# installed, or failure validation would be available only on well-equipped
# workstations.


def apply_blur(buf: bytearray, width: int, height: int, radius: float) -> None:
    """Separable box blur. Approximates defocus."""
    r = max(1, int(radius))
    stride = width * 3
    source = bytes(buf)
    for y in range(height):
        row = y * stride
        for x in range(width):
            for c in range(3):
                total = count = 0
                for dx in range(-r, r + 1):
                    xx = x + dx
                    if 0 <= xx < width:
                        total += source[row + xx * 3 + c]
                        count += 1
                buf[row + x * 3 + c] = total // max(count, 1)


def apply_low_light(buf: bytearray, gain: float) -> None:
    """Multiplicative darkening. Models an under-exposed sensor."""
    factor = max(0.0, min(1.0, gain))
    for i in range(len(buf)):
        buf[i] = int(buf[i] * factor)


def apply_rain(buf: bytearray, width: int, height: int, density: float, phase: int) -> None:
    """Deterministic bright streaks. Phase advances with frame index.

    Deterministic rather than random so a replay reproduces the same streaks —
    a stochastic fault would make V13 untestable under injection, which is
    exactly when you most want to test it.
    """
    stride = width * 3
    streaks = max(1, int(width * max(0.0, min(1.0, density))))
    for s in range(streaks):
        x = (s * 37 + phase * 13) % width
        y0 = (s * 53 + phase * 7) % max(1, height - 8)
        for y in range(y0, min(y0 + 8, height)):
            offset = y * stride + x * 3
            buf[offset] = min(255, buf[offset] + 90)
            buf[offset + 1] = min(255, buf[offset + 1] + 90)
            buf[offset + 2] = min(255, buf[offset + 2] + 90)


def apply_occlusion(buf: bytearray, width: int, height: int, coverage: float) -> None:
    """An opaque band. Models something blocking the lens."""
    fraction = max(0.0, min(1.0, coverage))
    stride = width * 3
    covered = int(height * fraction)
    for y in range(covered):
        row = y * stride
        for i in range(row, row + stride):
            buf[i] = 16


def degrade(
    payload: bytes, width: int, height: int, specs: Sequence[FaultSpec], index: int
) -> bytes:
    """Apply every armed decoder-stage fault, in enum order for reproducibility."""
    decoder_specs = [s for s in specs if s.scenario.stage == "decoder"]
    if not decoder_specs:
        return payload

    buf = bytearray(payload)
    for spec in sorted(decoder_specs, key=lambda s: s.scenario.value):
        if spec.scenario is Scenario.BLUR:
            apply_blur(buf, width, height, spec.number("radius", 3.0))
        elif spec.scenario is Scenario.LOW_LIGHT:
            apply_low_light(buf, spec.number("gain", 0.25))
        elif spec.scenario is Scenario.RAIN:
            apply_rain(buf, width, height, spec.number("density", 0.15), index)
        elif spec.scenario is Scenario.OCCLUSION:
            apply_occlusion(buf, width, height, spec.number("coverage", 0.4))
    return bytes(buf)


async def stream_with_source_faults(
    packets: AsyncIterator,
    *,
    ledger: FaultLedger,
    sleep,
    stream_lost,
) -> AsyncIterator:
    """Wrap a packet stream with the source-stage scenarios.

    `sleep` and `stream_lost` are injected rather than imported so this function
    is unit-testable without Vision OS on the path — and so the delay runs on the
    **platform's** clock, which is what keeps a delayed stream deterministic
    under a virtual clock.

    The frame index comes from the packet's `sequence_hint`, never from a local
    counter. A local counter would drift from the media index the instant a
    scenario dropped or duplicated a packet, and a fault armed at frame 400 would
    then fire somewhere the engineer never asked for.
    """
    previous = None
    fallback = 0
    async for packet in packets:
        index = packet.sequence_hint if packet.sequence_hint is not None else fallback
        fallback = index + 1
        active = ledger.active(index)
        kinds = {s.scenario for s in active}

        if Scenario.CAMERA_DISCONNECT in kinds:
            ledger.note_injection(Scenario.CAMERA_DISCONNECT, index, "raising StreamLostError")
            raise stream_lost(f"injected camera disconnect at frame {index}")

        if Scenario.DROPPED_FRAMES in kinds:
            spec = next(s for s in active if s.scenario is Scenario.DROPPED_FRAMES)
            every = max(2, int(spec.number("every", 3)))
            if index % every == 0:
                ledger.note_injection(Scenario.DROPPED_FRAMES, index, f"dropped (every {every})")
                continue

        for spec in active:
            if spec.scenario in (Scenario.SLOW_CAMERA, Scenario.NETWORK_DELAY):
                delay_ms = spec.number("delay_ms", 250.0 if spec.scenario is Scenario.SLOW_CAMERA else 80.0)
                ledger.note_injection(spec.scenario, index, f"delayed {delay_ms:.0f}ms")
                await sleep(delay_ms)

        if Scenario.FREEZE in kinds and previous is not None:
            # Same payload, advancing PTS — a frozen sensor still emits packets.
            # This is what `frozen_frame_threshold` in the health config detects,
            # and the only way to exercise the silent-failure detector honestly.
            ledger.note_injection(Scenario.FREEZE, index, "re-emitted previous payload")
            packet = _with_payload(packet, previous)

        yield packet
        previous = packet.payload

        if Scenario.DUPLICATE_FRAMES in kinds:
            ledger.note_injection(Scenario.DUPLICATE_FRAMES, index, "re-emitted identical packet")
            yield packet


def _with_payload(packet, payload: bytes):
    """Rebuild a frozen `SourcePacket` with a different payload."""
    import dataclasses

    return dataclasses.replace(packet, payload=payload)
