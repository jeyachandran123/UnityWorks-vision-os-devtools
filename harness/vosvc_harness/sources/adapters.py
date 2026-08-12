"""P1 and P2 adapters for recorded video.

These are **sibling adapters**, exactly as `adapters/acquisition/raw_video.py`
describes them:

> *"An RTSP/WebRTC source and an NVDEC/QSV/VAAPI decoder are sibling adapters
> behind the same ports. **No platform module changes to add them** — that is the
> whole point of P1 and P2."*

Nothing in Vision OS changes to accept these. They satisfy `SourcePort` and
`DecoderPort` structurally, are handed to the platform through the documented
`bindings_factory`, and the platform cannot tell them apart from a camera.

**Capabilities are declared honestly** — adapter obligation A1. `ReplayFileSource`
reports `seekable=True` because it genuinely is; `RtspReplaySource` reports
`seekable=False` because a live RTSP stream is not, and a validation source that
claimed otherwise would let an engineer validate a seek path that will not exist
in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from .decoding import DecodedFrame
from .faults import FaultLedger, degrade, stream_with_source_faults

RAW_CODEC = "raw_bgr24"


def _errors():
    from app.vision_os.core.errors import (
        ConnectFailedError,
        DecodeError,
        NotSeekableError,
        StreamLostError,
    )

    return ConnectFailedError, DecodeError, NotSeekableError, StreamLostError


def _acquisition():
    from app.vision_os.core.ports.acquisition import (
        DecodeOutcome,
        DecoderCapabilities,
        SourceCapabilities,
        SourcePacket,
    )

    return DecodeOutcome, DecoderCapabilities, SourceCapabilities, SourcePacket


class _Handle:
    """An open replay source. Owned by exactly one source actor."""

    __slots__ = ("_open", "camera_id")

    def __init__(self, camera_id: str) -> None:
        self._open = True
        self.camera_id = camera_id

    @property
    def is_open(self) -> bool:
        return self._open

    async def close(self) -> None:
        self._open = False


@dataclass(slots=True)
class ReplayCursor:
    """Where the session is in the media, and how it is moving.

    Owned by the source because the source is what the platform pulls from.
    Pausing is implemented as *not yielding a packet* rather than as a flag some
    downstream module consults — the platform must experience a paused camera as
    a quiet camera, which is precisely what it would be in the field.
    """

    index: int = 0
    playing: bool = False
    speed: float = 1.0
    step_budget: int = 0
    loop: bool = False
    exhausted: bool = False

    def may_emit(self) -> bool:
        """Whether the source is allowed to work toward the next packet.

        A **peek**, not a take. The budget is spent in `spend_step`, at the
        moment a packet is actually yielded.

        Consuming it here instead deadlocks a single step, and did: the source
        decremented the budget, then awaited the clock; the pump saw a zero
        budget with `playing` false, decided the session was idle, and stopped
        advancing the clock the source was waiting on. The step never completed
        and the frame count never moved.
        """
        return self.playing or self.step_budget > 0

    def spend_step(self) -> None:
        """Consume one step's budget. Called only when a packet is emitted."""
        if not self.playing and self.step_budget > 0:
            self.step_budget -= 1


class ReplayFileSource:
    """P1 — replay decoded frames as if they arrived from a camera.

    The frames were decoded once, up front (`decoding.VideoReader`), so the
    packet stream carries no decoder jitter. That is what lets a latency
    measurement in the console describe Vision OS rather than describing ffmpeg.

    Args:
        frames: Already-decoded BGR24 frames.
        clock: The **platform's** clock. Every delay here runs on it, so a
            virtual clock makes the whole replay deterministic (V13).
        cursor: Shared transport state, driven by `POST /sessions/{id}/transport`.
        ledger: Armed failure scenarios.
        semantics: `ARCHIVAL` protects completeness and is reproducible;
            `REALTIME` permits dropping.
    """

    def __init__(
        self,
        frames: Sequence[DecodedFrame],
        *,
        clock,
        cursor: ReplayCursor,
        ledger: FaultLedger,
        semantics=None,
        interpacket_ms: float = 40.0,
        seekable: bool = True,
        on_frame=None,
    ) -> None:
        from app.vision_os.core.model.camera import SourceSemantics

        self._frames = list(frames)
        self._clock = clock
        self._cursor = cursor
        self._ledger = ledger
        self._semantics = semantics or SourceSemantics.ARCHIVAL
        self._interpacket_ms = interpacket_ms
        self._seekable = seekable
        self._on_frame = on_frame
        self.open_calls = 0

    @property
    def frames(self) -> list[DecodedFrame]:
        return self._frames

    def capabilities(self):
        _, _, SourceCapabilities, _ = _acquisition()
        return SourceCapabilities(
            semantics=self._semantics,
            codecs=(RAW_CODEC,),
            seekable=self._seekable,
            provides_wallclock=False,
            max_bytes_per_packet=max((len(f.payload) for f in self._frames), default=0) or 1,
        )

    async def open(self, camera, credential: str | None):
        self.open_calls += 1
        ConnectFailedError, *_ = _errors()
        if not self._frames:
            raise ConnectFailedError(
                f"no frames decoded for '{camera.camera_id}'",
                camera_id=str(camera.camera_id),
            )
        return _Handle(str(camera.camera_id))

    async def packets(self, handle: _Handle) -> AsyncIterator:
        *_, StreamLostError = _errors()

        async def sleep_ms(ms: float) -> None:
            from app.vision_os.core.model.timebase import Duration

            await self._clock.sleep(Duration.from_millis(ms))

        async for packet in stream_with_source_faults(
            self._raw_packets(handle),
            ledger=self._ledger,
            sleep=sleep_ms,
            stream_lost=StreamLostError,
        ):
            yield packet

    async def _raw_packets(self, handle: _Handle) -> AsyncIterator:
        import asyncio

        from app.vision_os.core.model.timebase import Duration

        _, _, _, SourcePacket = _acquisition()

        while handle.is_open:
            if self._cursor.index >= len(self._frames):
                if self._cursor.loop:
                    self._cursor.index = 0
                else:
                    # Clean end-of-stream. Returning (rather than raising) is what
                    # tells the source actor this was an ending, not a loss — the
                    # actor correctly stops instead of reconnecting.
                    self._cursor.exhausted = True
                    return

            if not self._cursor.may_emit():
                # Paused. Yield to the loop without emitting: from the platform's
                # point of view the camera has simply gone quiet, which is a state
                # it must handle anyway.
                await asyncio.sleep(0)
                continue

            frame = self._frames[self._cursor.index]
            delay = self._interpacket_ms / max(self._cursor.speed, 0.01)
            if delay > 0:
                await self._clock.sleep(Duration.from_millis(delay))
            if not handle.is_open:
                return

            # Re-check after the wait. A pause that lands mid-interval must not
            # produce one more frame: the engineer pressed pause at frame 22 and
            # has to be looking at frame 22, not 23. Frames already emitted keep
            # travelling the pipeline during the pump's settle window — stopping
            # those mid-flight would strand a frame between layers.
            if not self._cursor.may_emit():
                await asyncio.sleep(0)
                continue

            # The budget is spent here — after the wait, at the moment the packet
            # becomes real. Spending it before the await would let the pump
            # conclude the step was finished while the source was still waiting.
            self._cursor.spend_step()

            if self._on_frame is not None:
                self._on_frame(self._cursor.index, frame)

            yield SourcePacket(
                payload=frame.payload,
                pts=frame.pts_ms,
                pts_timebase_hz=1000,
                is_keyframe=frame.is_keyframe,
                codec=RAW_CODEC,
                arrival=self._clock.now(),
                sequence_hint=self._cursor.index,
            )
            self._cursor.index += 1

    async def seek(self, handle: _Handle, position) -> None:
        _, _, NotSeekableError, _ = _errors()
        if not self._seekable:
            raise NotSeekableError("this source replays a live stream and cannot seek")
        target = int(position.millis / max(self._interpacket_ms, 1e-6))
        self._cursor.index = max(0, min(target, len(self._frames)))
        self._cursor.exhausted = False


class RtspReplaySource(ReplayFileSource):
    """P1 — replay a file under **live-stream** semantics.

    Same frames, different honesty: `seekable=False` and `REALTIME` semantics, so
    the platform may drop rather than protect completeness. An engineer
    validating what production will actually do needs this source, because a
    seekable archival replay quietly exercises a code path RTSP never takes.
    """

    def __init__(self, frames, *, clock, cursor, ledger, jitter_ms: float = 0.0, **kwargs) -> None:
        from app.vision_os.core.model.camera import SourceSemantics

        kwargs.pop("semantics", None)
        kwargs.pop("seekable", None)
        super().__init__(
            frames,
            clock=clock,
            cursor=cursor,
            ledger=ledger,
            semantics=SourceSemantics.REALTIME,
            seekable=False,
            **kwargs,
        )
        self._jitter_ms = jitter_ms


class ValidationDecoder:
    """P2 — write raw BGR24 into a pooled slot, applying decoder-stage faults.

    Zero-copy in spirit, like `PassthroughDecoder`: the payload is written *into*
    buffer-pool memory rather than allocated separately and copied.

    The four image-quality scenarios are applied **here**, before the frame is
    published, because that is where a real camera's problems appear — a blurry
    lens produces a blurry decode, not a blurry tracker. Injecting later would
    let the frame buffer and the scheduler see a clean frame the detector never
    got, and every downstream measurement would describe a pipeline that never
    existed.
    """

    def __init__(self, *, dimensions, ledger: FaultLedger, cursor: ReplayCursor) -> None:
        self._dimensions = dimensions
        self._ledger = ledger
        self._cursor = cursor
        self._count = 0
        self.reset_calls = 0

    def capabilities(self):
        _, DecoderCapabilities, _, _ = _acquisition()
        return DecoderCapabilities(
            codecs=(RAW_CODEC,),
            hardware_accelerated=False,
            colour_space=getattr(self._dimensions, "colour_space", "bgr24"),
        )

    def decode_into(self, packet, slot):
        DecodeOutcome, *_ = _acquisition()
        _, DecodeError, _, _ = _errors()
        from app.vision_os.core.model.frame import DecodeQuality

        self._count += 1
        index = packet.sequence_hint if packet.sequence_hint is not None else self._count - 1
        active = self._ledger.active(index)

        payload = degrade(
            packet.payload,
            int(self._dimensions.width),
            int(self._dimensions.height),
            active,
            index,
        )
        for spec in active:
            if spec.scenario.stage == "decoder":
                self._ledger.note_injection(spec.scenario, index, "applied to decoded plane")

        if len(payload) > slot.capacity:
            raise DecodeError(
                f"decoded frame ({len(payload)}B) exceeds slot capacity ({slot.capacity}B)"
            )

        memory = slot.memory()
        memory[: len(payload)] = payload

        return DecodeOutcome(
            dimensions=self._dimensions,
            bytes_written=len(payload),
            decode_quality=(
                DecodeQuality.KEYFRAME if packet.is_keyframe else DecodeQuality.DELTA
            ),
            blur=self._declared_blur(active),
            exposure=self._declared_exposure(active),
        )

    @staticmethod
    def _declared_blur(active) -> float:
        """Report the blur we injected.

        `DecodeOutcome.blur` is a measurement the platform consumes. Reporting
        the injected magnitude — rather than 0.0, or a re-measurement — is what
        lets an engineer confirm the value reached the crop quality gate intact.
        """
        from .faults import Scenario

        for spec in active:
            if spec.scenario is Scenario.BLUR:
                return min(1.0, spec.number("radius", 3.0) / 10.0)
        return 0.0

    @staticmethod
    def _declared_exposure(active) -> str:
        from .faults import Scenario

        for spec in active:
            if spec.scenario is Scenario.LOW_LIGHT:
                return "under"
        return "ok"

    def reset(self) -> None:
        self.reset_calls += 1
        self._count = 0

    def close(self) -> None:
        return None
