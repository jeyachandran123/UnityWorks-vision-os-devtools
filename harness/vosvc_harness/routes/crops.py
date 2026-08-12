"""Retained crop retrieval — the image a model was actually asked about.

Gated exactly like frame serving, and for the same reason: reading what the
camera *reported* and looking at the *picture* are different permissions
(12_SECURITY §5.3). Both endpoints require a declared purpose, and both refuse
outright when the deployment has not enabled evidence access.

This route reads. It never produces a crop, never re-extracts one from a frame,
and never sends one anywhere — the images it serves were written by the Flow 5
sink from the crops M8 handed to M9, and only when the retention policy allowed
it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Path as PathParam
from fastapi import Query
from fastapi.responses import JSONResponse, Response


def register(app, harness) -> None:
    config = harness.config

    def _forbidden(message: str) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "code": "FORBIDDEN",
                "message": message,
                "retryable": False,
                "details": {},
                "request_id": "",
            },
        )

    def _archive(session_id: str | None):
        session = harness.sessions.get(session_id) if session_id else None
        if session is None:
            sessions = list(harness.sessions.all()) if hasattr(harness.sessions, "all") else []
            session = sessions[-1] if sessions else None
        stack = getattr(session, "stack", None) if session else None
        return getattr(stack, "crop_archive", None)

    @app.get("/api/v1/crops")
    async def list_crops(session_id: str | None = Query(default=None)) -> Any:
        """What was retained, and what was refused.

        The refusal counts are reported rather than hidden: a viewer seeing no
        images needs to know whether none were produced or whether the retention
        policy declined to keep them, and those are different facts.
        """
        archive = _archive(session_id)
        if archive is None:
            return {"available": False, "reason": "no booted session", "by_object": {}}
        return {"available": True, **archive.index()}

    @app.get("/api/v1/crops/{crop_id}")
    async def get_crop(
        crop_id: str = PathParam(...),
        purpose: str = Query(default=""),
        session_id: str | None = Query(default=None),
    ) -> Response:
        if not config.allow_evidence:
            return _forbidden(
                "crop retrieval is disabled on this harness "
                "(VOSVC_ALLOW_EVIDENCE=0); it is a deployment decision, not a "
                "console one"
            )
        if not purpose:
            return _forbidden(
                "crop access requires a declared purpose, recorded with your "
                "identity — a defaulted purpose would erase exactly that"
            )

        archive = _archive(session_id)
        if archive is None:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "NOT_FOUND",
                    "message": "no booted session to read crops from",
                    "retryable": False,
                    "details": {},
                    "request_id": "",
                },
            )

        payload = archive.bytes_of(crop_id)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "NOT_FOUND",
                    "message": (
                        f"no retained crop '{crop_id}'. It may never have been "
                        f"written: a crop whose retention is ephemeral or "
                        f"never_persist is not kept, by policy"
                    ),
                    "retryable": False,
                    "details": {"crop_id": crop_id},
                    "request_id": "",
                },
            )

        return Response(
            content=payload,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-VOS-Crop-Id": crop_id,
                "X-VOS-Purpose": purpose,
            },
        )
