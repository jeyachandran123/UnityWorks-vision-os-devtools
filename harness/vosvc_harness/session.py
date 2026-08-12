"""Validation sessions — one media asset driven through one Vision OS.

**The pipeline runs on its own thread, with its own event loop.**

This is not a performance tweak; it is a correctness requirement discovered the
hard way. M9 invokes `UnderstanderPort.understand_batch` *synchronously*. With a
real VLM behind that port, one call is 5–26 s of blocking HTTP — and when the
pipeline shared a loop with the HTTP API, that single call froze acquisition,
detection, tracking **and** every REST route together. A 24-frame replay
processed two frames.

Separating the loops restores the platform's own model: understanding is allowed
to be slow, and its slowness applies backpressure to perception without stopping
the world. The API stays responsive because it is genuinely a different plane.

A session owns: an assembled stack, a replay cursor, a fault ledger, a tap bus,
and a frame ledger. It owns **no facts**. Every observation, object and metric it
serves came from Vision OS, and the session's job is to drive frames in and let
the taps carry results out.

**The pump.** Vision OS runs on a virtual clock, so nothing advances unless the
harness advances it. `_pump` steps the clock and yields to the event loop,
exactly as the platform's own integration suite does. This is what makes replay
deterministic: the pipeline's timing is a function of the pump, not of how busy
the machine was, so two runs over the same media produce the same observations
(V13) and the console can prove it via `verify_replay`.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .assembly import AssembledStack, VisionOsUnavailableError, build_stack
from .contract import encode
from .sources.adapters import ReplayCursor
from .sources.decoding import DecodedFrame
from .sources.faults import FaultLedger, FaultSpec, Scenario
from .taps import EventBusTap, FrameLedger, MetricsTap, TapBus


@dataclass(slots=True)
class SessionSpec:
    """What the console asked for."""

    media_id: str
    camera_id: str = "cam-validation"
    tenant_id: str = "t-eng"
    site_id: str = "site-eng"
    semantics: str = "archival"
    target_fps: float = 12.0
    deterministic: bool = True
    autostart: bool = False
    rtsp: bool = False


class Session:
    """One validation run.

    Lifecycle: `created` → `booting` → `ready` → (`playing` ⇄ `paused`) →
    `ended` | `failed`. Every transition is published on the `transport` channel,
    so the console never has to poll to learn what the session is doing.
    """

    def __init__(
        self,
        spec: SessionSpec,
        frames: list[DecodedFrame],
        *,
        media_name: str,
        tap_history: int = 20_000,
    ) -> None:
        self.session_id = f"s-{uuid.uuid4().hex[:12]}"
        self.spec = spec
        self.media_name = media_name
        self.frames = frames
        self.created_at_ns = time.time_ns()

        self.cursor = ReplayCursor(loop=False)
        self.ledger = FaultLedger()
        self.taps = TapBus(history=tap_history)
        self.frame_ledger = FrameLedger()
        self.metrics_tap = MetricsTap(self.taps)
        self.bus_tap = EventBusTap(self.taps)

        self.stack: AssembledStack | None = None
        self.state = "created"
        self.error: str | None = None
        self.transport_history: list[dict[str, Any]] = []
        self._pump_task: asyncio.Task | None = None
        self._boot_error: str | None = None
        self._subscription: Any = None

        # The pipeline's own thread and loop. See the module docstring: a real
        # VLM blocks for seconds inside M9, and that must not reach the API.
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stopping = threading.Event()

        # Every platform event that reaches the console also reaches the fault
        # ledger, which is how an injected scenario is validated against the
        # architectural response it is supposed to provoke. Wired as a tap
        # subscriber rather than inline so the ledger sees exactly what the
        # engineer sees — no separate path that could disagree.
        self.taps.subscribe(self._note_event_for_faults)

    # --- lifecycle ---------------------------------------------------------- #

    # --- threaded lifecycle --------------------------------------------------- #

    async def start(self) -> None:
        """Boot the pipeline on its own thread and wait for it to be ready.

        The caller's loop is never blocked by the pipeline after this returns —
        every subsequent frame, detection and model call happens on the worker.
        """
        if self._thread is not None:
            return

        self._thread = threading.Thread(target=self._run_loop, name=f"pipeline-{self.session_id}", daemon=True)
        self._thread.start()

        # Boot includes a synchronous VLM warm-up on first use. Waiting in an
        # executor keeps the API loop free while it happens, so a slow model load
        # shows as a session in `booting` rather than as a hung server.
        await asyncio.get_running_loop().run_in_executor(None, self._ready.wait, 600.0)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._lifecycle())
        except Exception as exc:  # noqa: BLE001 - the thread must report, never vanish
            self.error = f"{type(exc).__name__}: {exc}"
            self._set_state("failed")
        finally:
            self._ready.set()
            try:
                loop.close()
            finally:
                self._loop = None

    async def _lifecycle(self) -> None:
        await self.boot()
        self._ready.set()
        if self.state == "failed":
            return

        self._ensure_pump()
        while not self._stopping.is_set():
            await asyncio.sleep(0.05)

        await self._shutdown_inner()

    def _submit(self, coro):
        """Run a coroutine on the pipeline loop from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return None
        return asyncio.run_coroutine_threadsafe(coro, loop)

    async def boot(self) -> None:
        self._set_state("booting")
        try:
            self.stack = build_stack(
                frames=self.frames,
                camera_id=self.spec.camera_id,
                tenant_id=self.spec.tenant_id,
                site_id=self.spec.site_id,
                target_fps=self.spec.target_fps,
                semantics=self.spec.semantics,
                cursor=self.cursor,
                ledger=self.ledger,
                on_frame=self._on_frame_emitted,
                rtsp=self.spec.rtsp,
            )
        except VisionOsUnavailableError as exc:
            self.error = str(exc)
            self._boot_error = str(exc)
            self._set_state("failed")
            self.taps.publish("transport", "error", {"code": "INTERNAL", "message": str(exc)})
            return

        stack = self.stack
        self.bus_tap.attach(stack.platform.bus)
        if not self.bus_tap.attached:
            # Explicit, not silent. The console shows the Architecture Events
            # panel as UNAVAILABLE with this reason attached rather than as empty.
            self.taps.publish(
                "transport",
                "capability.gap",
                {
                    "capability": "architecture_events",
                    "reason": self.bus_tap.attach_error,
                },
            )

        # M14 subscription fan-out — the real, public observation stream.
        self._attach_observation_stream(stack)

        await stack.detection.start()
        await stack.detection_runtime.start()
        await stack.system.boot()

        self._set_state("ready")
        if self.spec.autostart:
            await self.play()

    def _attach_observation_stream(self, stack: AssembledStack) -> None:
        """Subscribe to M14 exactly as any external consumer would.

        Not a back door: `ObservationApi.subscribe` is the public streaming
        contract, the same one a Cognitive OS integration will use. The console
        therefore validates the delivery path that production will actually
        depend on, gaps and all.
        """
        try:
            from app.vision_os.core.model.api import DeliveryPolicy, Principal, Scope

            self._subscription = stack.api.subscribe(
                Principal(subject="console", tenant_id=self.spec.tenant_id),
                Scope(tenant_id=self.spec.tenant_id),
                policy=DeliveryPolicy(),
            )
        except Exception as exc:  # noqa: BLE001
            self._subscription = None
            self.taps.publish(
                "transport",
                "capability.gap",
                {"capability": "observation_stream", "reason": f"{type(exc).__name__}: {exc}"},
            )

    async def shutdown(self) -> None:
        """Stop the pipeline thread and wait for it to drain."""
        self._stopping.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            await asyncio.get_running_loop().run_in_executor(None, thread.join, 60.0)
            self._thread = None
            return
        # No worker thread (unit tests drive the session directly).
        await self._shutdown_inner()

    async def _shutdown_inner(self) -> None:
        await self.pause()
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._pump_task = None

        if self.stack is not None:
            try:
                await self.stack.detection.stop()
                await self.stack.system.shutdown()
            except Exception as exc:  # noqa: BLE001 - shutdown must not mask the run
                self.taps.publish("transport", "error", {"code": "INTERNAL", "message": str(exc)})
        self.bus_tap.detach()
        self._set_state("ended")

    # --- transport ---------------------------------------------------------- #

    # Transport commands are plain attribute writes on `cursor`, so they are safe
    # to issue from the API thread while the pipeline thread reads them. They
    # deliberately do **not** create asyncio tasks: the pump belongs to the
    # pipeline loop, and scheduling onto the caller's loop is what coupled the
    # two planes in the first place.

    async def play(self) -> None:
        self.cursor.playing = True
        self._set_state("playing")
        self._note_transport("play", {})

    async def pause(self) -> None:
        self.cursor.playing = False
        if self.state == "playing":
            self._set_state("paused")
        self._note_transport("pause", {"frame_index": self.cursor.index})

    async def step(self, count: int = 1) -> None:
        """Advance exactly `count` frames while paused.

        Implemented as a budget the source consumes rather than as a direct
        cursor write, so a step travels the whole pipeline — acquisition through
        state — exactly as a played frame does. A step that just moved the index
        would show the engineer a frame the platform never processed.
        """
        self.cursor.playing = False
        self.cursor.step_budget += max(1, count)
        self._set_state("paused")
        self._note_transport("step", {"count": count})

    async def seek(self, frame_index: int) -> None:
        target = max(0, min(int(frame_index), len(self.frames)))
        self.cursor.index = target
        self.cursor.exhausted = False
        self._note_transport("seek", {"frame_index": target})

    async def set_speed(self, speed: float) -> None:
        self.cursor.speed = max(0.05, min(float(speed), 32.0))
        self._note_transport("speed", {"speed": self.cursor.speed})

    async def restart(self) -> None:
        """Tear the pipeline down and bring it back — the `restart` scenario.

        A genuine restart, not a cursor reset: the platform detaches its
        pipelines and re-attaches them, and an engineer can watch it record the
        restart window as a coverage observation with `status=blind`, which is
        07_STATE §9.3's requirement that a deployment be *"visible in the record"*.
        """
        self._note_transport("restart", {})
        self.ledger.note_injection(Scenario.RESTART, self.cursor.index, "session restart")

        # Runs on the pipeline loop: tearing down and re-attaching the platform
        # from the API thread would touch runtime state the platform expects to
        # own on a single loop.
        future = self._submit(self._restart_inner())
        if future is not None:
            await asyncio.get_running_loop().run_in_executor(None, future.result, 600.0)
        else:
            await self._restart_inner()

    async def _restart_inner(self) -> None:
        await self._shutdown_inner()
        self.cursor.index = 0
        self.cursor.exhausted = False
        self.frame_ledger.entries.clear()
        self.state = "created"
        await self.boot()
        self._ensure_pump()

    async def record_restart_gap(self) -> int:
        """Ask the platform to publish the restart window as coverage."""
        if self.stack is None:
            return 0

        async def inner() -> int:
            try:
                return await self.stack.system.record_restart_gap()
            except Exception:  # noqa: BLE001
                return 0

        future = self._submit(inner())
        if future is None:
            return await inner()
        return await asyncio.get_running_loop().run_in_executor(None, future.result, 60.0)

    # --- faults -------------------------------------------------------------- #

    def arm_fault(self, spec: FaultSpec) -> None:
        # Stamp the arming point so the verdict can distinguish an architectural
        # response *to this fault* from one that had already happened.
        spec.armed_at_seq = self.taps.sequence
        self.ledger.arm(spec)
        self.taps.publish(
            "transport",
            "fault.armed",
            {
                "scenario": spec.scenario.value,
                "stage": spec.scenario.stage,
                "at_frame": spec.at_frame,
                "duration_frames": spec.duration_frames,
                "params": dict(spec.params),
            },
        )

    def clear_faults(self, scenario: Scenario | None = None) -> None:
        self.ledger.clear(scenario)
        self.taps.publish(
            "transport",
            "fault.cleared",
            {"scenario": scenario.value if scenario else "all"},
        )

    # --- the pump ------------------------------------------------------------ #

    def _ensure_pump(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """Advance the virtual clock and drain the async seams.

        Steps the clock by one frame interval, yields to let the pipeline run,
        then drains the tracking→registry bridge. The drain is explicit because
        that seam schedules its hand-off rather than awaiting it, *"so that
        synthesis latency never lands on a lower layer's critical path"*.
        """
        from app.vision_os.core.model.timebase import Duration

        interval_ms = 1000.0 / max(self.spec.target_fps, 0.1)
        last_sweep = 0.0

        #: Pump cycles to run after the source goes quiet.
        #:
        #: A frame is emitted at acquisition and then has to travel detection →
        #: tracking → registry → synthesis → state, and every one of those hops
        #: needs the clock advanced and the loop yielded. Stopping the instant
        #: the step budget empties would leave the frame halfway down the
        #: pipeline, and the engineer inspecting it would see a detection with no
        #: object — a pipeline bug that was really a pump bug.
        settle_cycles = 12
        settle = 0

        try:
            while True:
                stack = self.stack
                if stack is None:
                    return

                producing = self.cursor.playing or self.cursor.step_budget > 0
                if producing:
                    settle = settle_cycles
                elif settle > 0:
                    settle -= 1
                else:
                    await asyncio.sleep(0.02)
                    continue

                if self.cursor.exhausted:
                    self.cursor.playing = False
                    self._set_state("paused")
                    self._note_transport("end_of_media", {"frame_index": self.cursor.index})
                    await asyncio.sleep(0.02)
                    continue

                stack.clock.advance(Duration.from_millis(interval_ms))
                for _ in range(8):
                    await asyncio.sleep(0)

                drained = await stack.bridge.drain()
                if drained:
                    for _ in range(6):
                        await asyncio.sleep(0)

                # The bus is a pull surface: nothing arrives unless we drain it.
                self.bus_tap.drain()
                self._drain_subscription()

                now = time.monotonic()
                if now - last_sweep > 0.5:
                    last_sweep = now
                    self.metrics_tap.sweep(stack.platform.metrics)
                    self._publish_health()

                if self.cursor.playing:
                    await asyncio.sleep(max(0.0, interval_ms / 1000.0 / max(self.cursor.speed, 0.01)))
                else:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the pump must report, never vanish
            self.error = f"{type(exc).__name__}: {exc}"
            self.taps.publish("transport", "error", {"code": "INTERNAL", "message": self.error})
            self._set_state("failed")

    def _drain_subscription(self) -> None:
        """Pull whatever M14 has queued for us and publish it, gaps included."""
        subscription = getattr(self, "_subscription", None)
        if subscription is None:
            return
        for reader in ("drain", "poll", "take", "messages"):
            fn = getattr(subscription, reader, None)
            if not callable(fn):
                continue
            try:
                messages = fn()
            except Exception:  # noqa: BLE001
                return
            if not messages:
                return
            for message in messages:
                kind = type(message).__name__
                channel = "observation"
                type_ = "observation"
                if kind == "Gap":
                    type_ = "gap"
                elif kind == "Heartbeat":
                    type_ = "heartbeat"
                elif kind == "CoverageChange":
                    type_, channel = "coverage.change", "health"
                elif kind == "StateDeltaMessage":
                    type_, channel = "state.delta", "state"
                self.taps.publish(channel, type_, encode(message), frame_index=self.cursor.index)
            return

    def _publish_health(self) -> None:
        if self.stack is None:
            return
        try:
            self.taps.publish("health", "health.report", encode(self.stack.system.health()))
        except Exception:  # noqa: BLE001
            pass

    # --- taps ---------------------------------------------------------------- #

    def _on_frame_emitted(self, index: int, frame: DecodedFrame) -> None:
        active = [s.scenario.value for s in self.ledger.active(index)]
        entry = self.frame_ledger.record(index, frame, faults=active)
        self.taps.publish("acquisition", "layer.tap", entry, frame_index=index)

    def _note_event_for_faults(self, record) -> None:
        if record.type == "event":
            event_type = record.payload.get("event_type")
            if event_type:
                self.ledger.note_event(str(event_type), record.seq)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.taps.publish(
            "transport",
            "session.state",
            {
                "session_id": self.session_id,
                "state": state,
                "frame_index": self.cursor.index,
                "frame_count": len(self.frames),
                "playing": self.cursor.playing,
                "speed": self.cursor.speed,
                "error": self.error,
            },
        )

    def _note_transport(self, action: str, detail: dict[str, Any]) -> None:
        record = {
            "action": action,
            "at_ns": time.time_ns(),
            "frame_index": self.cursor.index,
            **detail,
        }
        self.transport_history.append(record)
        self.taps.publish("transport", "transport.command", record)

    # --- views ---------------------------------------------------------------- #

    def describe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "error": self.error,
            "media_id": self.spec.media_id,
            "media_name": self.media_name,
            "camera_id": self.spec.camera_id,
            "tenant_id": self.spec.tenant_id,
            "semantics": self.spec.semantics,
            "target_fps": self.spec.target_fps,
            "deterministic": self.spec.deterministic,
            "rtsp": self.spec.rtsp,
            "frame_count": len(self.frames),
            "frame_index": self.cursor.index,
            "playing": self.cursor.playing,
            "speed": self.cursor.speed,
            "exhausted": self.cursor.exhausted,
            "created_at_ns": self.created_at_ns,
            "events_attached": self.bus_tap.attached,
            "events_unavailable_reason": self.bus_tap.attach_error,
            "taps": self.taps.stats(),
            "faults": self.ledger.verdict(),
        }

    def verify_replay(self) -> list[dict[str, Any]]:
        """V13 proof, from the platform's own verifier.

        Returns Vision OS's `ReplayReport` values unmodified. The harness does
        not compute a verdict, grade the result, or soften a mismatch — a
        mismatch invalidates every recovery guarantee in 07_STATE §9.1 and the
        console says so.
        """
        if self.stack is None:
            return []
        reports = self.stack.system.verify_replay()
        return [encode(report) for report in reports]


class SessionRegistry:
    """Every live session. Bounded by nothing but the operator's patience."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def add(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    async def remove(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.shutdown()
        return True

    async def shutdown_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.shutdown()
        self._sessions.clear()
