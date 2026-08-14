"""The Validation Harness — HTTP/WS transport over Vision OS's public API.

This is the P32 transport adapter the platform anticipated:

> *"The day an HTTP adapter is written, it will implement the same three members
> and the API will not change."*

It did not change. Every read route below dispatches into the route table
Vision OS builds itself (`routes_for`), and translates shapes. It filters no
result, aggregates none, and decides nothing about what a consumer should see —
obligation **T2**.

**There is no write path.** Not disabled, not gated: absent. Search this module
for a call that mutates Vision State and there is nothing to find, because
`ObservationApi` exposes none to call.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import Body, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .assembly import ensure_importable, probe_vision_os
from .config import HarnessConfig, load_config
from .contract import WIRE_MAJOR, WIRE_VERSION, encode, encode_error, status_for
from .media import MediaLibrary
from .routes import (
    architecture,
    crops,
    findings,
    model,
    observation_api,
    reports,
    sessions,
    streams,
)
from .session import SessionRegistry


class Harness:
    """Process-wide state. One media library, one session registry."""

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        ensure_importable(config.vision_os_root)
        self.vision_os = probe_vision_os(config.vision_os_root)
        self.media = MediaLibrary(config.media_root)
        self.sessions = SessionRegistry()

    def principal(self, subject: str = "engineer"):
        """The Vision OS principal a console request acts as.

        External identity exists **only** at this boundary — 12_SECURITY §5.1:
        *"There is no ambient user context inside the pipeline, which means no
        pipeline component can accidentally make an authorization decision."*
        Nothing constructed here travels downward.
        """
        from app.vision_os.core.model.api import Principal

        return Principal(subject=subject, tenant_id=self.config.tenant_id)


def create_app(config: HarnessConfig | None = None) -> FastAPI:
    # `load_config()` applies the demo defaults before reading the environment.
    # A caller that built its own `HarnessConfig` has already decided, and this
    # does not second-guess it.
    config = config or load_config()
    harness = Harness(config)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        harness.media.discover()
        yield
        await harness.sessions.shutdown_all()

    app = FastAPI(
        title="Vision OS Validation Harness",
        version=WIRE_VERSION,
        description=(
            "HTTP/WebSocket transport over the public Vision OS Observation API, "
            "plus P1/P2 replay adapters. Read-only with respect to Vision State."
        ),
        lifespan=lifespan,
    )
    app.state.harness = harness

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def version_header(request: Request, call_next):
        """Negotiate the major, and reject rather than guess.

        §M14: *"Reject with the supported set; never guess."* An unsupported
        major fails here, at the edge, with the supported set attached.
        """
        accepted = request.headers.get("X-VOS-Accept-Major")
        if accepted is not None:
            try:
                major = int(accepted)
            except ValueError:
                major = -1
            if major != WIRE_MAJOR:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "UNSUPPORTED_VERSION",
                        "message": f"schema major {accepted} is not served",
                        "retryable": False,
                        "details": {"supported": [WIRE_MAJOR]},
                        "request_id": "",
                    },
                )
        response = await call_next(request)
        response.headers["X-VOS-Major"] = str(WIRE_MAJOR)
        return response

    async def _render(request: Request, exc: Exception) -> JSONResponse:
        body = encode_error(exc, request_id=request.headers.get("X-Request-Id", ""))
        return JSONResponse(status_code=status_for(body["code"]), content=body)

    # A typed platform error is a **client-visible contract outcome**, not a
    # server fault. `WindowTooLargeError` means "narrow your window"; letting it
    # surface as a 500 with a traceback would send an integrator hunting through
    # platform logs for what is really a documented policy bound (09_API §8).
    #
    # Registered on the base class so every current and future platform error is
    # rendered with its stable `code`, without this file enumerating them.
    try:
        from app.vision_os.core.errors import VisionOSError

        @app.exception_handler(VisionOSError)
        async def vision_os_errors(request: Request, exc: VisionOSError):
            return await _render(request, exc)

    except Exception:  # noqa: BLE001 - Vision OS absent; the generic handler covers it
        pass

    @app.exception_handler(Exception)
    async def platform_errors(request: Request, exc: Exception):
        """Render every remaining failure as the platform's own envelope.

        A transport that let an exception escape would make the console's error
        handling depend on Vision OS's internal exception hierarchy, and 09_API
        §8's stable `code` exists precisely so it does not.
        """
        return await _render(request, exc)

    # --- health and capability ---------------------------------------------- #

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        sessions_health = []
        for session in harness.sessions.all():
            entry = {"session_id": session.session_id, "state": session.state}
            if session.stack is not None:
                with contextlib.suppress(Exception):
                    entry["vision_os"] = encode(session.stack.system.health())
            sessions_health.append(entry)

        return {
            "harness": {
                "status": "serving",
                "wire_version": WIRE_VERSION,
                "wire_major": WIRE_MAJOR,
                "serve_frames": config.serve_frames,
                "allow_evidence": config.allow_evidence,
                "stream_queue_capacity": config.stream_queue_capacity,
            },
            "vision_os": harness.vision_os,
            "media": harness.media.capabilities(),
            "sessions": sessions_health,
        }

    @app.get("/api/v1/capabilities")
    async def capabilities(session_id: str | None = Query(default=None)) -> Any:
        """Live capability, from the platform (09_API §5.2).

        *"Capability is live state, not documentation."* Without a session there
        is no platform to ask, and the harness says so rather than returning an
        empty summary that would read as "produces nothing".
        """
        session = _first_session(harness, session_id)
        if session is None or session.stack is None:
            return {
                "unavailable": "no booted session; capability is live state and there is no live platform to report",
                "media": harness.media.capabilities(),
            }
        from app.vision_os.core.model.api import Scope

        return encode(
            session.stack.api.capabilities(
                harness.principal(), Scope(tenant_id=config.tenant_id)
            )
        )

    # --- media ---------------------------------------------------------------- #

    @app.get("/api/v1/media")
    async def list_media() -> dict[str, Any]:
        return {
            "media": [a.to_wire() for a in harness.media.discover()],
            "capabilities": harness.media.capabilities(),
        }

    @app.post("/api/v1/media")
    async def upload_media(file: UploadFile = File(...)) -> dict[str, Any]:
        data = await file.read()
        if len(data) > config.max_upload_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "code": "INVALID_SCOPE",
                    "message": f"upload exceeds {config.max_upload_bytes} bytes",
                    "retryable": False,
                    "details": {"bytes": len(data)},
                    "request_id": "",
                },
            )
        asset = harness.media.save_upload(file.filename or "upload.bin", data)
        return asset.to_wire()

    @app.delete("/api/v1/media/{media_id}")
    async def delete_media(media_id: str) -> Response:
        harness.media.remove(media_id)
        return Response(status_code=204)

    # --- mounted route groups -------------------------------------------------- #

    observation_api.register(app, harness)
    sessions.register(app, harness)
    streams.register(app, harness)
    architecture.register(app, harness)
    reports.register(app, harness)
    model.register(app, harness)
    findings.register(app, harness)
    # Retained crop retrieval. Reads only, gated like frame serving.
    crops.register(app, harness)

    @app.post("/api/v1/replay/verify")
    async def verify_replay(payload: dict = Body(default_factory=dict)) -> dict[str, Any]:
        """V13 — prove every partition rebuilds identically from the log.

        Runs the platform's own verifier and returns its reports unmodified.
        `mismatches > 0` is a Vision OS failure, and the console reports it as
        one: the metric's own note says a non-zero value *"invalidates every
        recovery guarantee in 07_STATE section 9.1."*
        """
        session = _first_session(harness, payload.get("session_id"))
        if session is None:
            return {"available": False, "reason": "no session"}
        if session.stack is None:
            return {
                "available": False,
                "session_id": session.session_id,
                "reason": (
                    f"session did not boot ({session.error}); there is no projection "
                    f"to verify"
                ),
            }

        reports_ = session.verify_replay()
        if not reports_:
            # Zero partitions verified is NOT zero mismatches. Reporting
            # `deterministic: true` here would certify a run that never
            # happened — the worst thing a validation tool can do, and exactly
            # what this endpoint did before an end-to-end smoke test caught it.
            return {
                "available": False,
                "session_id": session.session_id,
                "reason": "no partition produced a replay report; nothing was verified",
            }

        mismatches = sum(int(r.get("mismatches", 0) or 0) for r in reports_)
        return {
            "available": True,
            "session_id": session.session_id,
            "reports": reports_,
            "partitions_verified": len(reports_),
            "mismatches": mismatches,
            "deterministic": mismatches == 0,
        }

    return app


def _first_session(harness: Harness, session_id: str | None):
    if session_id:
        return harness.sessions.get(session_id)
    booted = [s for s in harness.sessions.all() if s.stack is not None]
    return booted[0] if booted else None


async def _noop() -> None:
    await asyncio.sleep(0)
