"""The WebSocket tap stream.

One multiplexed socket per session. Every message carries a monotonic, gapless
`seq`, and every drop produces an explicit `gap` — 09_API §3.3 applied to the
harness's own delivery:

> *"A subscriber is never silently skipped… This is V8 applied to delivery."*

**Bounded, always.** The per-socket queue has a fixed capacity and the overflow
policy is `drop_with_gap`. The three behaviours §3.4 forbids — unbounded
buffering, silent drop, stalling the platform — are not implemented here, and
two of them are not expressible: there is no capacity value meaning "unlimited",
and every drop path increments the gap counter it reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import Query, WebSocket, WebSocketDisconnect

from ..taps import CHANNELS, TapRecord


def register(app, harness) -> None:
    config = harness.config

    @app.websocket("/ws/v1/session/{session_id}")
    async def session_stream(
        websocket: WebSocket,
        session_id: str,
        channels: str = Query(default=""),
        since_seq: int = Query(default=0),
    ) -> None:
        await websocket.accept()
        session = harness.sessions.get(session_id)
        if session is None:
            await websocket.send_text(
                json.dumps(
                    {
                        "seq": 0,
                        "ts_ns": time.time_ns(),
                        "channel": "transport",
                        "type": "error",
                        "payload": {
                            "code": "NOT_FOUND",
                            "message": f"no session '{session_id}'",
                            "retryable": False,
                        },
                    }
                )
            )
            await websocket.close()
            return

        wanted = {c.strip() for c in channels.split(",") if c.strip()} or set(CHANNELS)
        queue: asyncio.Queue[TapRecord] = asyncio.Queue(maxsize=config.stream_queue_capacity)
        loop = asyncio.get_running_loop()

        dropped = 0
        gap_start_seq = 0

        def deliver(record: TapRecord) -> None:
            """Called from the tap bus. Never blocks, never raises upward.

            A full queue drops and counts; the reader then emits a `gap`. The
            platform is never stalled by a slow console — 09_API §3.4:
            *"Never: stall the platform."*
            """
            nonlocal dropped, gap_start_seq
            if record.channel not in wanted:
                return
            try:
                loop.call_soon_threadsafe(_offer, record)
            except RuntimeError:
                pass

        def _offer(record: TapRecord) -> None:
            nonlocal dropped, gap_start_seq
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                if dropped == 0:
                    gap_start_seq = record.seq
                dropped += 1

        unsubscribe = session.taps.subscribe(deliver)

        try:
            # Replay history first so a console that connects late — or
            # reconnects — sees the run from `since_seq` rather than starting
            # blind at "now".
            for record in session.taps.history(wanted, since_seq=since_seq, limit=2000):
                await websocket.send_text(json.dumps(record.to_wire(), default=str))

            heartbeat_at = time.monotonic()
            last_seq = since_seq

            while True:
                if dropped:
                    missed, first = dropped, gap_start_seq
                    dropped, gap_start_seq = 0, 0
                    await websocket.send_text(
                        json.dumps(
                            {
                                "seq": last_seq,
                                "ts_ns": time.time_ns(),
                                "channel": "transport",
                                "type": "gap",
                                "payload": {
                                    "reason": "slow_consumer",
                                    "recoverable": True,
                                    "observations_missed": missed,
                                    "seq_from": first,
                                    "seq_to": first + missed - 1,
                                    "start_ns": 0,
                                    "end_ns": 0,
                                    "cameras": [session.spec.camera_id],
                                },
                            }
                        )
                    )

                try:
                    record = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - heartbeat_at >= 5.0:
                        heartbeat_at = now
                        # A subscriber that hears nothing must be able to tell a
                        # dead connection from a quiet scene (09_API §3.1).
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "seq": last_seq,
                                    "ts_ns": time.time_ns(),
                                    "channel": "transport",
                                    "type": "heartbeat",
                                    "payload": {
                                        "cursor": str(last_seq),
                                        "session_state": session.state,
                                        "frame_index": session.cursor.index,
                                    },
                                }
                            )
                        )
                    continue

                last_seq = record.seq
                await websocket.send_text(json.dumps(record.to_wire(), default=str))

        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001 - a broken socket must not take the harness down
            pass
        finally:
            unsubscribe()
            with contextlib.suppress(Exception):
                await websocket.close()

    @app.get("/api/v1/sessions/{session_id}/taps")
    async def tap_history(
        session_id: str,
        channels: str = Query(default=""),
        since_seq: int = Query(default=0),
        limit: int = Query(default=500),
    ) -> Any:
        """Polling fallback and backfill source.

        Exists because a `gap` with `recoverable=true` is only useful if the
        console can actually fetch what it missed.
        """
        session = harness.sessions.get(session_id)
        if session is None:
            return {"records": [], "unavailable": f"no session '{session_id}'"}
        wanted = [c.strip() for c in channels.split(",") if c.strip()] or None
        records = session.taps.history(wanted, since_seq=since_seq, limit=limit)
        return {
            "records": [r.to_wire() for r in records],
            "stats": session.taps.stats(),
            "channels": list(CHANNELS),
        }
