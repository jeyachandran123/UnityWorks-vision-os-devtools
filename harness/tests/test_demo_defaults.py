"""The demo configures itself, and the images actually arrive.

Two regressions live here, and they had the same shape: a setting that existed,
was documented, and was never *set* in the process that needed it.

**The frame stayed black.** `VOSVC_SERVE_FRAMES` defaults to off in
`HarnessConfig` — correct for a library — and was only ever turned on by
`start-platform.ps1`. Anyone running `python -m vosvc_harness` directly, which is
what the README shows, got a console whose Frame-by-Frame page rendered bounding
boxes over an empty panel and said the picture was not being shown. It was
telling the truth; nobody had asked for the pictures.

**The defaults landed too late.** The first attempt applied them inside
`create_app`, after `HarnessConfig` had already been constructed. `HarnessConfig`
reads the environment in its field `default_factory`, so the variables were set
and the object everything consults still reported them off. They belong in
`load_config`, before the object exists — which is what these tests pin.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from vosvc_harness.app import create_app
from vosvc_harness.config import HarnessConfig, load_config
from vosvc_harness.defaults import apply_demo_defaults

IMAGERY = ("VOSVC_SERVE_FRAMES", "VOSVC_ALLOW_EVIDENCE")

#: Every variable `apply_demo_defaults` can set.
#:
#: All of them, not just the two under test. `apply_demo_defaults` writes to the
#: real environment, and this file is the only one in the suite that calls it —
#: so this fixture is the only thing standing between it and every other test.
#: Leaving `VISION_UNDERSTANDER_PROVIDER=nvidia` behind put a remote model
#: behind the shared integration stack and dropped its replay from 20+ frames to
#: 6, which surfaced as a buffer-pressure failure three files away.
TOUCHED = (
    *IMAGERY,
    "VISION_SEMANTIC_POLICY",
    "VISION_VERIFICATION_RULES",
    "COMPLIANCE_RULES",
    "VISION_UNDERSTANDER_PROVIDER",
)


@pytest.fixture
def clean_env():
    """Run with the imagery flags unset, and restore the whole set afterwards."""
    saved = {name: os.environ.get(name) for name in TOUCHED}
    for name in IMAGERY:
        os.environ.pop(name, None)
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestDefaultsReachTheConfigObject:
    def test_load_config_turns_the_pictures_on(self, clean_env) -> None:
        """The regression. Setting the variable is not enough — the object that
        everything else reads has to see it."""
        config = load_config()

        assert config.serve_frames is True
        assert config.allow_evidence is True

    def test_an_operator_choice_is_never_overridden(self, clean_env) -> None:
        """`setdefault`, not assignment. A deployment that turned imagery off
        stays off — otherwise the flags would be impossible to configure, which
        is the opposite problem."""
        os.environ["VOSVC_SERVE_FRAMES"] = "0"

        config = load_config()

        assert config.serve_frames is False
        assert config.allow_evidence is True

    def test_constructing_the_config_directly_still_gets_library_defaults(
        self, clean_env
    ) -> None:
        """`HarnessConfig()` bypasses the demo defaults on purpose.

        A test asking for the library's own posture must get it, and the rest of
        this suite depends on that: it builds its own config and expects frame
        serving off.
        """
        assert HarnessConfig().serve_frames is False

    def test_it_reports_what_it_filled_in(self, clean_env) -> None:
        """A console that configured itself silently would be as confusing as
        one that configured nothing."""
        applied = apply_demo_defaults(env={})

        assert "VOSVC_SERVE_FRAMES" in applied
        assert "VOSVC_ALLOW_EVIDENCE" in applied


class TestTheFrameEndpointReturnsImageBytes:
    def test_a_default_harness_serves_a_decodable_frame(self, clean_env) -> None:
        """End to end: no config passed, real bytes back, and they are an image.

        The status code alone is not the assertion. A 200 carrying a JSON error,
        or a truncated header, renders in a browser as exactly the black panel
        this test exists to prevent — so the bytes are decoded here.
        """
        app = create_app()
        with TestClient(app) as client:
            health = client.get("/api/v1/health").json()["harness"]
            assert health["serve_frames"] is True
            assert health["allow_evidence"] is True

            media = client.get("/api/v1/media").json()["media"]
            usable = [m for m in media if m.get("usable")]
            if not usable:
                pytest.skip("no usable media in this checkout")

            created = client.post(
                "/api/v1/sessions",
                json={"media_id": usable[0]["media_id"], "target_fps": 6.0},
            ).json()
            session_id = created["session_id"]
            try:
                response = client.get(
                    f"/api/v1/sessions/{session_id}/frames/0?purpose=regression-test"
                )

                assert response.status_code == 200
                assert response.headers["content-type"] == "image/bmp"
                # `BM` — the header a browser needs. Raw pixels with an image
                # content-type would pass a status check and render as nothing.
                assert response.content[:2] == b"BM"
                assert len(response.content) > 54
            finally:
                client.delete(f"/api/v1/sessions/{session_id}")

    def test_a_purpose_is_still_required(self, clean_env) -> None:
        """Enabling the pictures did not weaken the gate in front of them."""
        app = create_app()
        with TestClient(app) as client:
            media = client.get("/api/v1/media").json()["media"]
            usable = [m for m in media if m.get("usable")]
            if not usable:
                pytest.skip("no usable media in this checkout")

            created = client.post(
                "/api/v1/sessions",
                json={"media_id": usable[0]["media_id"], "target_fps": 6.0},
            ).json()
            session_id = created["session_id"]
            try:
                response = client.get(f"/api/v1/sessions/{session_id}/frames/0")

                assert response.status_code == 403
                assert response.json()["code"] == "FORBIDDEN"
            finally:
                client.delete(f"/api/v1/sessions/{session_id}")
