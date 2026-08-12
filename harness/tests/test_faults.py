"""Failure-injection tests.

The verdict logic is the part that must not be wrong. A validation tool that
reports a false pass is worse than no tool, so these tests concentrate on the
two ways this one could:

1. Crediting a scenario with an event that fired **before** it was armed.
2. Reporting a scenario as validated when nothing was ever injected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from vosvc_harness.sources.faults import (
    EXPECTED_RESPONSE,
    QUALITY_SCENARIOS,
    FaultLedger,
    FaultSpec,
    Scenario,
    apply_low_light,
    apply_occlusion,
    degrade,
    stream_with_source_faults,
)


@dataclass(frozen=True)
class Packet:
    payload: bytes
    sequence_hint: int | None = None
    pts: int = 0


class TestScenarioVocabulary:
    def test_there_are_exactly_eleven(self) -> None:
        assert len(list(Scenario)) == 11

    def test_every_scenario_declares_a_stage(self) -> None:
        for scenario in Scenario:
            assert scenario.stage in {"source", "decoder", "session"}

    def test_every_scenario_has_a_declared_expectation(self) -> None:
        for scenario in Scenario:
            assert scenario in EXPECTED_RESPONSE

    def test_quality_scenarios_mandate_no_event(self) -> None:
        """The Semantic Ceiling reaches the test plan.

        The platform may not conclude "it is raining" — that is interpretation.
        Demanding an event for `rain` would be demanding a V1 violation, so these
        four are observational by design rather than by omission.
        """
        for scenario in QUALITY_SCENARIOS:
            assert EXPECTED_RESPONSE[scenario] == ()


class TestVerdicts:
    def test_an_event_seen_before_arming_does_not_validate_a_scenario(self) -> None:
        ledger = FaultLedger()
        # coverage_changed fires during ordinary startup.
        ledger.note_event("health.coverage_changed", seq=5)

        spec = FaultSpec(scenario=Scenario.DROPPED_FRAMES, armed_at_seq=10)
        ledger.arm(spec)
        ledger.note_injection(Scenario.DROPPED_FRAMES, 0, "dropped")

        verdict = ledger.verdict()[0]
        assert verdict["verdict"] == "unvalidated"
        assert "health.coverage_changed" in verdict["missing_events"]

    def test_an_event_seen_after_arming_validates_it(self) -> None:
        ledger = FaultLedger()
        ledger.note_event("health.coverage_changed", seq=5)

        spec = FaultSpec(scenario=Scenario.DROPPED_FRAMES, armed_at_seq=10)
        ledger.arm(spec)
        ledger.note_injection(Scenario.DROPPED_FRAMES, 0, "dropped")
        ledger.note_event("health.coverage_changed", seq=20)

        assert ledger.verdict()[0]["verdict"] == "validated"

    def test_a_scenario_that_never_injected_is_not_reached(self) -> None:
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.DROPPED_FRAMES, armed_at_seq=0))
        ledger.note_event("health.coverage_changed", seq=99)

        # The expected event arrived, but this scenario did not cause it —
        # nothing was injected, so nothing was tested.
        assert ledger.verdict()[0]["verdict"] == "not_reached"

    def test_a_quality_scenario_is_observational(self) -> None:
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.BLUR, armed_at_seq=0))
        assert ledger.verdict()[0]["verdict"] == "observational"

    def test_arming_twice_replaces_rather_than_duplicates(self) -> None:
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.BLUR, at_frame=1))
        ledger.arm(FaultSpec(scenario=Scenario.BLUR, at_frame=99))
        assert len(ledger.specs) == 1
        assert ledger.specs[0].at_frame == 99


class TestWindows:
    def test_a_fault_with_no_duration_runs_until_cleared(self) -> None:
        spec = FaultSpec(scenario=Scenario.BLUR, at_frame=10, duration_frames=0)
        assert not spec.active_at(9)
        assert spec.active_at(10)
        assert spec.active_at(10_000)

    def test_a_bounded_fault_stops(self) -> None:
        spec = FaultSpec(scenario=Scenario.BLUR, at_frame=10, duration_frames=5)
        assert not spec.active_at(9)
        assert spec.active_at(14)
        assert not spec.active_at(15)


class TestPixelDegradation:
    def test_low_light_darkens_every_channel(self) -> None:
        buf = bytearray([200] * 27)
        apply_low_light(buf, 0.25)
        assert all(value == 50 for value in buf)

    def test_occlusion_covers_the_requested_fraction(self) -> None:
        width = height = 4
        buf = bytearray([200] * (width * height * 3))
        apply_occlusion(buf, width, height, 0.5)
        covered_rows = height // 2
        assert all(v == 16 for v in buf[: covered_rows * width * 3])
        assert all(v == 200 for v in buf[covered_rows * width * 3 :])

    def test_degrade_is_a_no_op_without_decoder_faults(self) -> None:
        payload = bytes([7] * 48)
        spec = FaultSpec(scenario=Scenario.DROPPED_FRAMES, at_frame=0)
        assert degrade(payload, 4, 4, [spec], 0) == payload

    def test_rain_is_deterministic_for_the_same_frame(self) -> None:
        """A stochastic fault would make V13 untestable under injection."""
        payload = bytes([10] * (8 * 8 * 3))
        spec = FaultSpec(scenario=Scenario.RAIN, at_frame=0, params={"density": 0.3})
        first = degrade(payload, 8, 8, [spec], 4)
        second = degrade(payload, 8, 8, [spec], 4)
        assert first == second

    def test_rain_differs_between_frames(self) -> None:
        payload = bytes([10] * (8 * 8 * 3))
        spec = FaultSpec(scenario=Scenario.RAIN, at_frame=0, params={"density": 0.3})
        assert degrade(payload, 8, 8, [spec], 1) != degrade(payload, 8, 8, [spec], 7)


class TestSourceStream:
    @staticmethod
    async def _collect(packets, ledger):
        async def source():
            for packet in packets:
                yield packet

        async def sleep(_ms):
            return None

        out = []
        async for packet in stream_with_source_faults(
            source(), ledger=ledger, sleep=sleep, stream_lost=RuntimeError
        ):
            out.append(packet)
        return out

    async def test_dropped_frames_uses_the_media_index_not_a_local_counter(self) -> None:
        """The index origin bug.

        A local counter drifts from the media index the moment a packet is
        dropped, so a fault armed at frame 400 fires somewhere else. These
        packets start at index 10 to prove the ledger reads `sequence_hint`.
        """
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.DROPPED_FRAMES, at_frame=12, params={"every": 2}))

        packets = [Packet(bytes([i]), sequence_hint=i) for i in range(10, 20)]
        out = await self._collect(packets, ledger)

        kept = [p.sequence_hint for p in out]
        assert 10 in kept and 11 in kept  # before the window
        assert 12 not in kept  # 12 % 2 == 0, dropped
        assert 13 in kept

    async def test_duplicate_frames_emits_the_packet_twice(self) -> None:
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.DUPLICATE_FRAMES, at_frame=0))
        packets = [Packet(b"a", sequence_hint=0), Packet(b"b", sequence_hint=1)]
        out = await self._collect(packets, ledger)
        assert len(out) == 4

    async def test_camera_disconnect_raises_rather_than_ending_cleanly(self) -> None:
        """A clean return means end-of-stream; only a raise means loss.

        The distinction decides whether the source actor reconnects or stops, so
        a disconnect that returned would exercise the wrong code path entirely.
        """
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.CAMERA_DISCONNECT, at_frame=1))
        packets = [Packet(b"a", sequence_hint=0), Packet(b"b", sequence_hint=1)]

        with pytest.raises(RuntimeError):
            await self._collect(packets, ledger)

    async def test_freeze_reemits_the_previous_payload(self) -> None:
        ledger = FaultLedger()
        ledger.arm(FaultSpec(scenario=Scenario.FREEZE, at_frame=1))
        packets = [Packet(b"a", sequence_hint=0), Packet(b"b", sequence_hint=1)]
        out = await self._collect(packets, ledger)
        assert out[0].payload == b"a"
        assert out[1].payload == b"a"  # frozen sensor keeps sending the same image
