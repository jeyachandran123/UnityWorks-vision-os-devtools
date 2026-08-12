"""Session lifecycle, replay transport, failure injection, frame serving.

Harness-owned. Nothing here writes to Vision State — these routes drive a
*source* the platform consumes through P1, which is categorically different from
writing a fact. The platform cannot tell a transport command from a camera that
happened to go quiet, and that is the point.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import Body, Path as PathParam, Query, Response
from fastapi.responses import JSONResponse

from ..session import Session, SessionSpec
from ..sources.faults import FaultSpec, Scenario


def register(app, harness) -> None:
    config = harness.config

    @app.get("/api/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {"sessions": [s.describe() for s in harness.sessions.all()]}

    @app.post("/api/v1/sessions")
    async def create_session(payload: dict = Body(default_factory=dict)) -> Any:
        media_id = str(payload.get("media_id", "m-synthetic"))
        asset = harness.media.get(media_id)
        if asset is None:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "NOT_FOUND",
                    "message": f"no media asset '{media_id}'",
                    "retryable": False,
                    "details": {},
                    "request_id": "",
                },
            )
        if not asset.usable:
            # A capability gap, stated. Not an empty session that looks booted.
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_SCOPE",
                    "message": asset.error or "media asset produced no frames",
                    "retryable": False,
                    "details": {"media_id": media_id},
                    "request_id": "",
                },
            )

        spec = SessionSpec(
            media_id=media_id,
            camera_id=str(payload.get("camera_id", "cam-validation")),
            tenant_id=str(payload.get("tenant_id", config.tenant_id)),
            site_id=str(payload.get("site_id", config.site_id)),
            semantics=str(payload.get("semantics", "archival")),
            target_fps=float(payload.get("target_fps", 12.0)),
            deterministic=bool(payload.get("deterministic", True)),
            autostart=bool(payload.get("autostart", False)),
            rtsp=bool(payload.get("rtsp", False)),
        )
        # Decode happens **here**, not at discovery — this is the first moment
        # anyone has actually asked to replay this file. It can fail even though
        # the header probed clean, and that failure is reported rather than
        # becoming a session over zero frames (which would look like a camera
        # that saw nothing).
        try:
            frames = asset.frames()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_SCOPE",
                    "message": f"{asset.name} probed clean but would not decode: {exc}",
                    "retryable": False,
                    "details": {"media_id": media_id},
                    "request_id": "",
                },
            )

        limit = int(payload.get("max_frames", 0) or 0)
        if limit > 0:
            frames = frames[:limit]

        session = Session(
            spec,
            frames,
            media_name=asset.name,
            tap_history=config.tap_history,
        )
        harness.sessions.add(session)
        # Boots on the pipeline's own thread. With a real VLM bound, warm-up and
        # conformance gating take tens of seconds; the API loop stays free
        # throughout, so the console can poll `GET /sessions/{id}` and watch the
        # state move `booting` → `ready` instead of hanging on this request.
        await session.start()
        return session.describe()

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str = PathParam(...)) -> Any:
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)
        return session.describe()

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str = PathParam(...)) -> Response:
        await harness.sessions.remove(session_id)
        return Response(status_code=204)

    # --- transport ------------------------------------------------------------ #

    @app.post("/api/v1/sessions/{session_id}/transport")
    async def transport(
        session_id: str = PathParam(...), payload: dict = Body(default_factory=dict)
    ) -> Any:
        """play · pause · step · seek · speed · restart · loop

        Every command runs the frame through the **whole** pipeline. A `step`
        does not move a playhead over pre-computed results; it lets exactly one
        frame through acquisition and waits for it to reach state. That is the
        only way an engineer inspecting frame 412 is looking at what Vision OS
        actually did with frame 412.
        """
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)

        action = str(payload.get("action", "")).lower()
        if action == "play":
            await session.play()
        elif action == "pause":
            await session.pause()
        elif action == "step":
            await session.step(int(payload.get("count", 1)))
        elif action == "seek":
            await session.seek(int(payload.get("frame_index", 0)))
        elif action == "speed":
            await session.set_speed(float(payload.get("speed", 1.0)))
        elif action == "restart":
            await session.restart()
        elif action == "record_restart_gap":
            published = await session.record_restart_gap()
            return {**session.describe(), "coverage_observations_published": published}
        elif action == "loop":
            session.cursor.loop = bool(payload.get("loop", True))
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_SCOPE",
                    "message": f"unknown transport action '{action}'",
                    "retryable": False,
                    "details": {
                        "known": [
                            "play", "pause", "step", "seek", "speed",
                            "restart", "record_restart_gap", "loop",
                        ]
                    },
                    "request_id": "",
                },
            )
        return session.describe()

    # --- frames ---------------------------------------------------------------- #

    @app.get("/api/v1/sessions/{session_id}/frames/{index}")
    async def get_frame(
        session_id: str = PathParam(...),
        index: int = PathParam(...),
        purpose: str = Query(default=""),
    ) -> Response:
        """Serve one decoded frame — off by default (V12, CONTRACT §4.3).

        When enabled, `purpose` is required and recorded, mirroring evidence
        access. An engineering tool may see pixels; it may not do so quietly.
        """
        if not config.serve_frames:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "FORBIDDEN",
                    "message": (
                        "frame serving is disabled (VOSVC_SERVE_FRAMES=0). Pixels stay "
                        "local by default (V12); enabling this is a deployment decision."
                    ),
                    "retryable": False,
                    "details": {},
                    "request_id": "",
                },
            )
        if not purpose:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "FORBIDDEN",
                    "message": "frame access requires a declared purpose",
                    "retryable": False,
                    "details": {},
                    "request_id": "",
                },
            )
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)
        if not (0 <= index < len(session.frames)):
            return _not_found(f"frame {index}")

        frame = session.frames[index]
        body = _to_bmp(frame.payload, frame.width, frame.height)
        return Response(
            content=body,
            media_type="image/bmp",
            headers={
                "Cache-Control": "no-store",
                "X-VOS-Frame-Index": str(index),
                "X-VOS-Purpose": purpose,
            },
        )

    @app.get("/api/v1/sessions/{session_id}/frames")
    async def frame_ledger(
        session_id: str = PathParam(...),
        offset: int = Query(default=0),
        limit: int = Query(default=500),
    ) -> Any:
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)
        entries = session.frame_ledger.entries
        return {
            "total": len(entries),
            "offset": offset,
            "entries": entries[offset : offset + max(1, min(limit, 5000))],
            "frame_count": len(session.frames),
        }

    # --- faults ------------------------------------------------------------------ #

    @app.get("/api/v1/sessions/{session_id}/faults")
    async def list_faults(session_id: str = PathParam(...)) -> Any:
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)
        return {
            "armed": session.ledger.verdict(),
            "scenarios": [
                {"scenario": s.value, "stage": s.stage} for s in Scenario
            ],
        }

    @app.post("/api/v1/sessions/{session_id}/faults")
    async def arm_fault(
        session_id: str = PathParam(...), payload: dict = Body(default_factory=dict)
    ) -> Any:
        session = harness.sessions.get(session_id)
        if session is None:
            return _not_found(session_id)

        if payload.get("clear"):
            name = payload.get("scenario")
            session.clear_faults(Scenario(name) if name else None)
            return {"armed": session.ledger.verdict()}

        try:
            scenario = Scenario(str(payload.get("scenario", "")))
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "INVALID_SCOPE",
                    "message": f"unknown scenario '{payload.get('scenario')}'",
                    "retryable": False,
                    "details": {"known": [s.value for s in Scenario]},
                    "request_id": "",
                },
            )

        if scenario is Scenario.RESTART:
            await session.restart()
            return {"armed": session.ledger.verdict(), "restarted": True}

        session.arm_fault(
            FaultSpec(
                scenario=scenario,
                at_frame=int(payload.get("at_frame", session.cursor.index)),
                duration_frames=int(payload.get("duration_frames", 0)),
                params={k: v for k, v in (payload.get("params") or {}).items()},
            )
        )
        return {"armed": session.ledger.verdict()}

    def _not_found(what: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "code": "NOT_FOUND",
                "message": f"no such resource: {what}",
                "retryable": False,
                "details": {},
                "request_id": "",
            },
        )


def _to_bmp(bgr: bytes, width: int, height: int) -> bytes:
    """Wrap raw BGR24 in a 24-bit BMP header.

    BMP because it needs no encoder: the harness must be able to show a frame on
    a machine with no codec library, or the Live Video panel would be the one
    part of the console that requires a wheel to work. Rows are written
    bottom-up, which is BMP's native order.
    """
    row_bytes = width * 3
    padded = (row_bytes + 3) & ~3
    padding = b"\x00" * (padded - row_bytes)
    rows = [bgr[y * row_bytes : (y + 1) * row_bytes] + padding for y in range(height)]
    rows.reverse()
    pixels = b"".join(rows)

    file_size = 54 + len(pixels)
    header = bytearray(54)
    header[0:2] = b"BM"
    header[2:6] = file_size.to_bytes(4, "little")
    header[10:14] = (54).to_bytes(4, "little")
    header[14:18] = (40).to_bytes(4, "little")
    header[18:22] = width.to_bytes(4, "little", signed=True)
    header[22:26] = height.to_bytes(4, "little", signed=True)
    header[26:28] = (1).to_bytes(2, "little")
    header[28:30] = (24).to_bytes(2, "little")
    header[34:38] = len(pixels).to_bytes(4, "little")
    return bytes(header) + pixels


def frame_data_url(bgr: bytes, width: int, height: int) -> str:
    return "data:image/bmp;base64," + base64.b64encode(_to_bmp(bgr, width, height)).decode("ascii")
