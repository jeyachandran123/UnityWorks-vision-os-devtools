"""End-to-end tests against a real Vision OS.

These boot the actual platform through its public composition roots and drive
frames through it. They are the tests that would have caught the buffer-sizing
stall, and they are skipped — loudly — when Vision OS is not importable rather
than passing vacuously.
"""

from __future__ import annotations

import asyncio

import pytest

from vosvc_harness.assembly import probe_vision_os
from vosvc_harness.session import Session, SessionSpec
from vosvc_harness.sources.decoding import synthetic_frames
from vosvc_harness.sources.faults import FaultSpec, Scenario
from vosvc_harness.taps import CHANNELS, channel_for

_probe = probe_vision_os()
requires_vision_os = pytest.mark.skipif(
    not _probe.get("available"),
    reason=f"Vision OS is not importable: {_probe.get('error')}",
)


class TestChannelRouting:
    """No Vision OS needed — pure mapping."""

    def test_every_event_prefix_lands_on_a_known_channel(self) -> None:
        for event_type in (
            "stream.connected",
            "detection.completed",
            "tracking.track_updated",
            "registry.object_created",
            "cropping.budget_exhausted",
            "understanding.failed",
            "synthesis.observation_rejected",
            "state.partition_degraded",
            "health.coverage_changed",
            "camera.changed",
            "runtime.pipeline_attached",
        ):
            assert channel_for(event_type) in CHANNELS

    def test_an_unknown_prefix_falls_back_to_the_firehose(self) -> None:
        # Not dropped. A new event type Vision OS adds must still be visible.
        assert channel_for("brand.new.thing") == "event"


@requires_vision_os
class TestFullPipeline:
    async def _run(self, *, frames: int = 40, fps: float = 12.0, seconds: float = 4.0) -> Session:
        decoded, _ = synthetic_frames(count=frames, width=64, height=64)
        session = Session(
            SessionSpec(media_id="m-synthetic", target_fps=fps),
            decoded,
            media_name="synthetic",
        )
        await session.start()
        await session.play()
        await asyncio.sleep(seconds)
        await session.pause()
        return session

    async def test_a_frame_becomes_a_queryable_object(self) -> None:
        """The single most important assertion in this repository.

        Pixels entered a P1 adapter written here; a consumer read a fact out of
        the real Observation API. Every layer in between did its own job.
        """
        session = await self._run()
        try:
            assert session.state in {"paused", "playing"}, session.error

            from app.vision_os.core.model.api import Principal, Scope
            from app.vision_os.core.model.ids import TenantId

            result = session.stack.api.query_state(
                Principal(subject="engineer", tenant_id=TenantId("t-eng")),
                Scope(tenant_id=TenantId("t-eng")),
            )
            assert result.objects, "no object reached the consumer"
            assert result.coverage is not None
            assert result.snapshot.partitions
        finally:
            await session.shutdown()

    async def test_the_replay_does_not_stall_on_buffer_pressure(self) -> None:
        """The regression that motivated `_buffer_sizing`.

        With a fixed six-slot pool and a ten-second history window the source
        blocked on an exhausted pool at frame 7 and the whole replay stopped. A
        validation console that can only replay seven frames validates nothing.
        """
        session = await self._run(frames=40, seconds=5.0)
        try:
            emitted = len(session.frame_ledger.entries)
            assert emitted >= 20, f"replay stalled after {emitted} frames"
        finally:
            await session.shutdown()

    async def test_every_pipeline_layer_reports(self) -> None:
        session = await self._run()
        try:
            counts = session.taps.stats()["by_channel"]
            for channel in ("acquisition", "detection", "tracking", "registry", "observation"):
                assert counts.get(channel, 0) > 0, f"{channel} produced nothing"
        finally:
            await session.shutdown()

    async def test_the_event_bus_tap_attaches(self) -> None:
        session = await self._run(seconds=2.0)
        try:
            assert session.bus_tap.attached, session.bus_tap.attach_error
            assert session.bus_tap.drained > 0
        finally:
            await session.shutdown()

    async def test_replay_verification_reports_no_mismatch(self) -> None:
        session = await self._run()
        try:
            reports = session.verify_replay()
            assert reports, "no partition was verified"
            total = sum(int(r.get("mismatches", 0) or 0) for r in reports)
            assert total == 0, f"V13 violated: {total} mismatches"
        finally:
            await session.shutdown()

    async def test_the_attention_path_produces_attributes(self) -> None:
        """Crop Manager → Understanding → Builder, end to end.

        The regression this pins was invisible: the Flow 3/4 bridge fabricated
        `FrameSeq(len(tracks))` instead of carrying the real `frame_ref`. The
        Crop Manager triggered correctly every time, asked the Frame Buffer for a
        frame that never existed, and silently got nothing — 63
        `cropping.frame_unavailable` in a 76-frame run, no exception anywhere,
        and the only symptom was an Understanding panel that stayed empty.

        Asserting on a *populated attribute* rather than on an event is
        deliberate: there is no `CropProduced` and no `UnderstandingSucceeded`
        event by design, so the only honest proof the path ran is that a value
        arrived at the far end of it.
        """
        session = await self._run(frames=60, seconds=6.0)
        try:
            from app.vision_os.core.model.api import Principal, Scope
            from app.vision_os.core.model.ids import TenantId

            result = session.stack.api.query_state(
                Principal(subject="engineer", tenant_id=TenantId("t-eng")),
                Scope(tenant_id=TenantId("t-eng")),
            )
            assert result.objects, "no object reached state"

            attributed = [o for o in result.objects if o.attributes]
            assert attributed, (
                "no object carried an attribute; the Crop Manager or the "
                "Understanding Engine never ran. Check "
                "vision_os.cropping.frame_unavailable."
            )

            held = attributed[0].attributes["posture"]
            assert held.value == "standing"
            # V4: the attribute must be explainable, not merely present.
            assert held.evidence_ref, "attribute arrived without an evidence reference"
        finally:
            await session.shutdown()

    async def test_the_frame_reference_survives_the_tracking_seam(self) -> None:
        """A fabricated frame_ref points the Crop Manager at nothing."""
        from app.vision_os.core.model.ids import CameraId

        from vosvc_harness.assembly import _parse_frame_ref

        parsed = _parse_frame_ref("cam-validation/e1/f42", CameraId("cam-validation"))
        assert parsed is not None
        assert int(parsed.frame_seq) == 42
        assert int(parsed.stream_epoch) == 1

        # Refuses rather than guesses. A wrong frame number is worse than none.
        assert _parse_frame_ref("nonsense", CameraId("cam-1")) is None
        assert _parse_frame_ref("", CameraId("cam-1")) is None

    async def test_observations_carry_provenance(self) -> None:
        session = await self._run()
        try:
            records = [
                r for r in session.taps.history(["observation"], limit=0) if r.type == "observation"
            ]
            assert records, "no observations were delivered"
            for record in records:
                assert record.payload.get("provenance") is not None, "V4: unexplainable observation"
        finally:
            await session.shutdown()

    async def test_pausing_stops_frame_production(self) -> None:
        session = await self._run(seconds=2.0)
        try:
            at_pause = len(session.frame_ledger.entries)
            await asyncio.sleep(0.7)
            assert len(session.frame_ledger.entries) == at_pause
        finally:
            await session.shutdown()

    async def test_a_step_advances_exactly_one_frame(self) -> None:
        decoded, _ = synthetic_frames(count=30, width=64, height=64)
        session = Session(SessionSpec(media_id="m", target_fps=12.0), decoded, media_name="syn")
        await session.start()
        try:
            before = len(session.frame_ledger.entries)
            await session.step(1)
            await asyncio.sleep(1.0)
            after = len(session.frame_ledger.entries)
            assert after - before == 1, f"step produced {after - before} frames"
        finally:
            await session.shutdown()

    async def test_an_injected_occlusion_reaches_the_decoder(self) -> None:
        decoded, _ = synthetic_frames(count=40, width=64, height=64)
        session = Session(SessionSpec(media_id="m", target_fps=12.0), decoded, media_name="syn")
        await session.start()
        try:
            session.arm_fault(
                FaultSpec(scenario=Scenario.OCCLUSION, at_frame=0, params={"coverage": 0.5})
            )
            await session.play()
            await asyncio.sleep(2.5)
            await session.pause()

            verdict = next(v for v in session.ledger.verdict() if v["scenario"] == "occlusion")
            assert verdict["injections"], "the fault never reached the decoder"
            assert verdict["verdict"] == "observational"
        finally:
            await session.shutdown()

    async def test_a_camera_disconnect_advances_the_stream_epoch(self) -> None:
        decoded, _ = synthetic_frames(count=60, width=64, height=64)
        session = Session(SessionSpec(media_id="m", target_fps=12.0), decoded, media_name="syn")
        await session.start()
        try:
            await session.play()
            await asyncio.sleep(1.0)
            session.arm_fault(FaultSpec(scenario=Scenario.CAMERA_DISCONNECT, at_frame=0))
            await asyncio.sleep(3.0)
            await session.pause()

            seen = session.ledger.observed_events
            assert "stream.lost" in seen or "stream.epoch_advanced" in seen, sorted(seen)
        finally:
            await session.shutdown()

    async def test_end_of_media_still_settles_the_pipeline(self) -> None:
        """End of media must not strand the frame that discovered it.

        The pump used to `continue` the moment `cursor.exhausted` went true — on
        the same iteration that emitted the final frame. That frame was still
        travelling detection → tracking → registry → synthesis, and nothing
        advanced the clock or drained the bridge again, so it never arrived: it
        appeared in the frame ledger and in no observation, and the
        Frame-by-Frame timeline was permanently one sample short of the end of
        the video.

        **Asserted on the clock, not on observations.** This suite binds the
        scripted detector, which reports the same box on every frame, so exact
        suppression correctly silences later frames — an absent observation for
        the last frame is legitimate here and would make an observation-counting
        assertion meaningless. What the fix changed is that the pump keeps
        running after the video ends, and the virtual clock measures that
        exactly: it advances once per cycle and not at all otherwise.
        """
        fps = 12.0
        interval_ns = 10**9 / fps
        decoded, _ = synthetic_frames(count=24, width=64, height=64)
        session = Session(
            SessionSpec(media_id="m-synthetic", target_fps=fps), decoded, media_name="synthetic"
        )
        await session.start()
        try:
            await session.play()

            at_exhaustion: int | None = None
            for _ in range(4000):
                await asyncio.sleep(0.005)
                if session.cursor.exhausted:
                    at_exhaustion = session.stack.clock.now().ns
                    break
            assert at_exhaustion is not None, "the replay never reached the end of the video"

            await asyncio.sleep(3.0)
            after = session.stack.clock.now().ns

            settled_cycles = (after - at_exhaustion) / interval_ns
            assert settled_cycles >= 8, (
                f"the pump advanced {settled_cycles:.1f} cycles after end of media; it "
                f"stopped dead and the final frame is still mid-pipeline"
            )
        finally:
            await session.shutdown()
