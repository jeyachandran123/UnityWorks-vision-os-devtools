"""HTTP surface tests — the wire contract, exercised through the real app.

These drive FastAPI against a real, booted Vision OS. They found three defects
the layer-level tests could not:

- `HarnessConfig` resolved `vision_os_root` one directory too shallow, so the
  app could not import the platform while every unit test passed.
- `POST /replay/verify` returned `deterministic: true` for a session that never
  booted — zero partitions verified is not zero mismatches.
- A typed platform error escaped as a 500 with a traceback instead of the stable
  error envelope 09_API §8 requires.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from vosvc_harness.app import create_app
from vosvc_harness.assembly import probe_vision_os
from vosvc_harness.config import HarnessConfig

_probe = probe_vision_os()
requires_vision_os = pytest.mark.skipif(
    not _probe.get("available"),
    reason=f"Vision OS is not importable: {_probe.get('error')}",
)

HOUR_NS = 3600 * 10**9


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app(HarnessConfig())) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def session_id(client):
    response = client.post("/api/v1/sessions", json={"media_id": "m-synthetic", "target_fps": 12})
    body = response.json()
    assert response.status_code == 200, body
    assert body["state"] == "ready", body.get("error")

    client.post(f"/api/v1/sessions/{body['session_id']}/transport", json={"action": "play"})
    time.sleep(3.0)
    client.post(f"/api/v1/sessions/{body['session_id']}/transport", json={"action": "pause"})

    yield body["session_id"]
    client.delete(f"/api/v1/sessions/{body['session_id']}")


class TestHealthAndDefaults:
    def test_config_resolves_vision_os(self, client) -> None:
        """The path bug: `parents[2]` pointed inside this repo, not at Atlas."""
        health = client.get("/api/v1/health").json()
        assert health["vision_os"]["available"], health["vision_os"].get("error")

    def test_pixels_stay_local_by_default(self, client) -> None:
        health = client.get("/api/v1/health").json()
        assert health["harness"]["serve_frames"] is False
        assert health["harness"]["allow_evidence"] is False

    def test_decode_backends_are_declared(self, client) -> None:
        """V8 for the tool itself: it states what it can open."""
        media = client.get("/api/v1/health").json()["media"]
        assert media["backends"], "no backend declared at all"
        assert "containers" in media


class TestVersionNegotiation:
    def test_an_unsupported_major_is_rejected_with_the_supported_set(self, client) -> None:
        response = client.get("/api/v1/health", headers={"X-VOS-Accept-Major": "9"})
        assert response.status_code == 400
        assert response.json()["code"] == "UNSUPPORTED_VERSION"
        assert response.json()["details"]["supported"] == [1]

    def test_the_served_major_is_announced(self, client) -> None:
        response = client.get("/api/v1/health", headers={"X-VOS-Accept-Major": "1"})
        assert response.headers["X-VOS-Major"] == "1"


class TestPrivacyGates:
    def test_frames_are_refused_by_default(self, client, session_id) -> None:
        response = client.get(f"/api/v1/sessions/{session_id}/frames/0")
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    def test_evidence_is_refused_by_default(self, client, session_id) -> None:
        response = client.get(f"/api/v1/evidence/blob-1?purpose=x&session_id={session_id}")
        assert response.json()["code"] == "FORBIDDEN"


@requires_vision_os
class TestObservationApi:
    def test_state_query_returns_objects_with_coverage(self, client, session_id) -> None:
        body = client.post("/api/v1/state/query", json={"session_id": session_id}).json()
        assert body["objects"], "no object reached the consumer over HTTP"
        assert "coverage" in body, "coverage is returned unconditionally (V8)"

    def test_instants_are_integer_nanoseconds(self, client, session_id) -> None:
        body = client.post("/api/v1/state/query", json={"session_id": session_id}).json()
        for view in body["objects"]:
            assert isinstance(view["first_seen_ns"], int)
            assert "first_seen_ms" not in view, "an Instant was misclassified as a Duration"

    def test_is_stale_is_serialized_not_recomputed(self, client, session_id) -> None:
        body = client.post("/api/v1/state/query", json={"session_id": session_id}).json()
        assert all("is_stale" in view for view in body["objects"])

    def test_observations_page_declares_window_observability(self, client, session_id) -> None:
        body = client.post(
            "/api/v1/observations/query",
            json={"session_id": session_id, "window": {"start_ns": 0, "end_ns": HOUR_NS}},
        ).json()
        assert "window_fully_observable" in body

    def test_an_oversize_window_is_a_typed_400_not_a_500(self, client, session_id) -> None:
        """The escaped-exception bug.

        A policy bound is a client-visible contract outcome. Surfacing it as a
        500 sends an integrator hunting through platform logs for what is really
        a documented limit.
        """
        response = client.post(
            "/api/v1/observations/query",
            json={"session_id": session_id, "window": {"start_ns": 0, "end_ns": 10**18}},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "WINDOW_TOO_LARGE"
        assert body["retryable"] is False


@requires_vision_os
class TestDeterminism:
    def test_a_booted_session_verifies_clean(self, client, session_id) -> None:
        body = client.post("/api/v1/replay/verify", json={"session_id": session_id}).json()
        assert body["available"] is True
        assert body["partitions_verified"] > 0
        assert body["mismatches"] == 0
        assert body["deterministic"] is True

    def test_an_unbooted_session_is_indeterminate_not_deterministic(self, client) -> None:
        """The false-pass bug.

        Zero partitions verified is not zero mismatches. Reporting determinism
        for a session that never produced a projection certifies a run that
        never happened.
        """
        response = client.post("/api/v1/sessions", json={"media_id": "does-not-exist"})
        assert response.status_code == 404

        body = client.post("/api/v1/replay/verify", json={"session_id": "s-nonexistent"}).json()
        assert body["available"] is False
        assert body.get("deterministic") is not True


@requires_vision_os
class TestArchitectureAndReports:
    def test_architecture_reports_the_live_runtime(self, client, session_id) -> None:
        body = client.get(f"/api/v1/architecture?session_id={session_id}").json()
        assert len(body["declared_order"]) == 10
        assert len(body["invariants"]) == 13
        assert body["runtime"]["available"] is True

    def test_metrics_expose_the_closed_vocabulary(self, client, session_id) -> None:
        body = client.get(f"/api/v1/metrics?session_id={session_id}").json()
        assert body["available"] is True
        assert len(body["names"]) > 100

    @pytest.mark.parametrize(
        "kind",
        [
            "replay",
            "performance",
            "observation",
            "architecture",
            "failure",
            "latency",
            "regression",
            "summary",
        ],
    )
    def test_every_report_kind_renders(self, client, session_id, kind: str) -> None:
        response = client.get(f"/api/v1/reports/{kind}?session_id={session_id}")
        assert response.status_code == 200
        assert response.json()["kind"] == kind

    def test_an_unknown_report_kind_lists_the_known_ones(self, client, session_id) -> None:
        response = client.get(f"/api/v1/reports/nonsense?session_id={session_id}")
        assert response.status_code == 400
        assert len(response.json()["details"]["known"]) == 8


class TestFaults:
    def test_all_eleven_scenarios_are_offered(self, client, session_id) -> None:
        body = client.get(f"/api/v1/sessions/{session_id}/faults").json()
        assert len(body["scenarios"]) == 11

    def test_an_unknown_scenario_is_refused_with_the_known_set(self, client, session_id) -> None:
        response = client.post(
            f"/api/v1/sessions/{session_id}/faults", json={"scenario": "not_real"}
        )
        assert response.status_code == 400
        assert len(response.json()["details"]["known"]) == 11

    def test_an_unknown_transport_action_is_refused(self, client, session_id) -> None:
        response = client.post(
            f"/api/v1/sessions/{session_id}/transport", json={"action": "teleport"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_SCOPE"


class TestStream:
    def test_the_socket_delivers_the_documented_envelope(self, client, session_id) -> None:
        with client.websocket_connect(f"/ws/v1/session/{session_id}") as socket:
            message = socket.receive_json()
        assert {"seq", "ts_ns", "channel", "type", "payload"} <= set(message)

    def test_taps_expose_all_fifteen_channels(self, client, session_id) -> None:
        body = client.get(f"/api/v1/sessions/{session_id}/taps?limit=10").json()
        assert len(body["channels"]) == 15
        assert body["records"]
