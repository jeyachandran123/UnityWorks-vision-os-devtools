"""The Observation API projection — nine routes, zero interpretation.

Each route builds the platform's own request objects from JSON, calls the public
`ObservationApi` method, and encodes the result. **No route filters, sorts,
aggregates, or defaults a value the platform left absent.** Where a field is
required by the platform (a purpose for evidence, a tenant for a scope), it is
required here too — supplying a default would be this harness making a decision
that 12_SECURITY reserves for the caller.
"""

from __future__ import annotations

from typing import Any

from fastapi import Body, Path as PathParam, Query, Response

from ..contract import encode


def register(app, harness) -> None:
    config = harness.config

    def _api(session_id: str | None):
        """The `ObservationApi` of a booted session, or `None`.

        A query with no platform behind it returns an explicit unavailability
        rather than an empty result — a consumer must never receive a thin
        answer without the information needed to interpret it (V8).
        """
        session = _resolve(harness, session_id)
        if session is None or session.stack is None:
            return None, None
        return session.stack.api, session

    def _unavailable(reason: str) -> dict[str, Any]:
        return {
            "code": "NOT_FOUND",
            "message": reason,
            "retryable": False,
            "details": {"hint": "open a session first: POST /api/v1/sessions"},
            "request_id": "",
        }

    # --- scope and filter construction ------------------------------------- #

    def _scope(raw: dict[str, Any] | None):
        from app.vision_os.core.model.api import Scope
        from app.vision_os.core.model.ids import CameraId, RegionId, SiteId, TenantId

        raw = raw or {}
        return Scope(
            tenant_id=TenantId(raw.get("tenant_id") or config.tenant_id),
            site_ids=tuple(SiteId(s) for s in raw.get("site_ids", ())),
            camera_ids=tuple(CameraId(c) for c in raw.get("camera_ids", ())),
            region_ids=tuple(RegionId(r) for r in raw.get("region_ids", ())),
        )

    def _state_filter(raw: dict[str, Any] | None):
        from app.vision_os.core.model.api import AttributePredicate, StateFilter
        from app.vision_os.core.model.ids import AttributeKey, ClassId, ObjectId
        from app.vision_os.core.model.visual_object import LifecycleState

        raw = raw or {}
        kwargs: dict[str, Any] = {}
        if raw.get("class_ids"):
            kwargs["class_ids"] = tuple(ClassId(c) for c in raw["class_ids"])
        if raw.get("lifecycle"):
            kwargs["lifecycle"] = tuple(LifecycleState(v) for v in raw["lifecycle"])
        if raw.get("object_ids"):
            kwargs["object_ids"] = tuple(ObjectId(o) for o in raw["object_ids"])
        if raw.get("min_confidence") is not None:
            kwargs["min_confidence"] = float(raw["min_confidence"])
        if raw.get("attributes"):
            kwargs["attributes"] = tuple(
                AttributePredicate(
                    key=AttributeKey(p["key"]),
                    equals=p.get("equals"),
                    present=bool(p.get("present", True)),
                )
                for p in raw["attributes"]
            )
        return StateFilter(**kwargs)

    def _options(raw: dict[str, Any] | None):
        from app.vision_os.core.model.api import QueryOptions
        from app.vision_os.core.model.ids import AttributeKey

        raw = raw or {}
        include = raw.get("include_attributes", None)
        return QueryOptions(
            # `None` means all; `[]` means none. Preserved rather than
            # normalized — normalizing would silently change the answer.
            include_attributes=(
                None if include is None else tuple(AttributeKey(k) for k in include)
            ),
            include_spatial=bool(raw.get("include_spatial", True)),
            include_trajectory=bool(raw.get("include_trajectory", False)),
            include_provenance=bool(raw.get("include_provenance", True)),
            limit=max(1, int(raw.get("limit", 100))),
            cursor=raw.get("cursor"),
        )

    def _window(raw: dict[str, Any]):
        from app.vision_os.core.model.api import TimeWindow
        from app.vision_os.core.model.timebase import Instant

        return TimeWindow(
            start=Instant(int(raw.get("start_ns", 0))),
            end=Instant(int(raw.get("end_ns", 0))),
        )

    def _observation_filter(raw: dict[str, Any] | None):
        from app.vision_os.core.model.api import ObservationFilter
        from app.vision_os.core.model.ids import AttributeKey, ClassId, ObjectId
        from app.vision_os.core.model.observation import ObservationType

        raw = raw or {}
        kwargs: dict[str, Any] = {}
        if raw.get("observation_types"):
            kwargs["observation_types"] = tuple(
                ObservationType(t) for t in raw["observation_types"]
            )
        if raw.get("object_ids"):
            kwargs["object_ids"] = tuple(ObjectId(o) for o in raw["object_ids"])
        if raw.get("class_ids"):
            kwargs["class_ids"] = tuple(ClassId(c) for c in raw["class_ids"])
        if raw.get("attribute_keys"):
            kwargs["attribute_keys"] = tuple(AttributeKey(k) for k in raw["attribute_keys"])
        if raw.get("min_confidence") is not None:
            kwargs["min_confidence"] = float(raw["min_confidence"])
        if raw.get("producer"):
            kwargs["producer"] = str(raw["producer"])
        kwargs["include_superseded"] = bool(raw.get("include_superseded", False))
        return ObservationFilter(**kwargs)

    # --- routes -------------------------------------------------------------- #

    @app.post("/api/v1/state/query")
    async def query_state(payload: dict = Body(default_factory=dict)) -> Any:
        api, _ = _api(payload.get("session_id"))
        if api is None:
            return _unavailable("no booted session to query state from")
        result = api.query_state(
            harness.principal(),
            _scope(payload.get("scope")),
            filter_=_state_filter(payload.get("filter")),
            options=_options(payload.get("options")),
        )
        return encode(result)

    @app.post("/api/v1/observations/query")
    async def query_observations(payload: dict = Body(default_factory=dict)) -> Any:
        api, _ = _api(payload.get("session_id"))
        if api is None:
            return _unavailable("no booted session to query observations from")
        return encode(
            api.query_observations(
                harness.principal(),
                _scope(payload.get("scope")),
                _window(payload.get("window") or {}),
                filter_=_observation_filter(payload.get("filter")),
                cursor=payload.get("cursor"),
                limit=int(payload.get("limit", 100)),
            )
        )

    @app.get("/api/v1/objects/{object_id}")
    async def get_object(
        object_id: str = PathParam(...),
        session_id: str | None = Query(default=None),
        include_trajectory: bool = Query(default=True),
    ) -> Any:
        api, _ = _api(session_id)
        if api is None:
            return _unavailable("no booted session to read an object from")
        from app.vision_os.core.model.api import QueryOptions
        from app.vision_os.core.model.ids import ObjectId

        return encode(
            api.get_object(
                harness.principal(),
                ObjectId(object_id),
                options=QueryOptions(include_trajectory=include_trajectory),
            )
        )

    @app.post("/api/v1/coverage")
    async def coverage(payload: dict = Body(default_factory=dict)) -> Any:
        api, _ = _api(payload.get("session_id"))
        if api is None:
            return _unavailable("no booted session to report coverage for")
        window = payload.get("window")
        return encode(
            api.coverage(
                harness.principal(),
                _scope(payload.get("scope")),
                _window(window) if window else None,
            )
        )

    @app.get("/api/v1/evidence/{blob_ref}")
    async def get_evidence(
        blob_ref: str = PathParam(...),
        purpose: str = Query(default=""),
        observation_id: str = Query(default=""),
        session_id: str | None = Query(default=None),
    ) -> Any:
        """Evidence retrieval — separately authorized, and purpose-bound.

        `purpose` is required and has **no default**. The platform rejects an
        empty purpose, and the harness does not paper over that: 12_SECURITY §5.4
        records the purpose with the actor so imagery access is attributable
        rather than invisible, and a defaulted purpose would erase exactly that
        property.
        """
        if not config.allow_evidence:
            return {
                "code": "FORBIDDEN",
                "message": (
                    "evidence retrieval is disabled on this harness "
                    "(VOSVC_ALLOW_EVIDENCE=0); it is a deployment decision, not a "
                    "console one"
                ),
                "retryable": False,
                "details": {},
                "request_id": "",
            }
        api, _ = _api(session_id)
        if api is None:
            return _unavailable("no booted session to read evidence from")
        from app.vision_os.core.model.ids import BlobRef

        return encode(
            api.get_evidence(
                harness.principal(),
                BlobRef(blob_ref),
                purpose=purpose,
                observation_id=observation_id,
            )
        )

    # --- demands: the only inbound path ------------------------------------- #

    @app.get("/api/v1/demands")
    async def list_demands(session_id: str | None = Query(default=None)) -> Any:
        api, _ = _api(session_id)
        if api is None:
            return {"demands": [], "unavailable": "no booted session"}
        try:
            return {"demands": encode(api.list_demands(harness.principal()))}
        except Exception as exc:  # noqa: BLE001 - an unconfigured registry is a normal state
            return {"demands": [], "unavailable": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/v1/demands")
    async def register_demand(payload: dict = Body(default_factory=dict)) -> Any:
        """Register a demand — influence, never a write.

        > §M14: *"a demand changes what the platform chooses to compute and
        > cannot change any published fact."*

        This is the only route in the whole harness that changes Vision OS
        behaviour, and it changes attention, not state.
        """
        api, _ = _api(payload.get("session_id"))
        if api is None:
            return _unavailable("no booted session to register a demand with")

        from app.vision_os.core.model.demand import Demand
        from app.vision_os.core.model.ids import AttributeKey, ClassId

        demand = Demand(
            subscriber=str(payload.get("subscriber", "console")),
            class_id=ClassId(payload.get("class_id", "person")),
            required_attributes=tuple(
                AttributeKey(k) for k in payload.get("required_attributes", ())
            ),
        )
        return encode(api.register_demand(harness.principal(), demand))

    @app.delete("/api/v1/demands/{demand_id}")
    async def revoke_demand(
        demand_id: str = PathParam(...), session_id: str | None = Query(default=None)
    ) -> Response:
        api, _ = _api(session_id)
        if api is not None:
            from app.vision_os.core.model.ids import DemandId

            api.revoke_demand(harness.principal(), DemandId(demand_id))
        return Response(status_code=204)


def _resolve(harness, session_id: str | None):
    if session_id:
        return harness.sessions.get(session_id)
    booted = [s for s in harness.sessions.all() if s.stack is not None]
    return booted[0] if booted else None
