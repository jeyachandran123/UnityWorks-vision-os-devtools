"""Phase 7 — live CCTV as a P1 source.

Two properties dominate this file, because both fail silently and both are
expensive.

**Credentials must never escape.** A password in a log line, an exception, or an
API response is on disk and in a screen recording forever. Every string this
module can emit is asserted redacted.

**A 16-channel DVR must not become 16 pipelines.** Nothing is enabled by
default; a channel nobody named opens no socket, decodes nothing, and reaches no
VLM. Cost follows configuration, not hardware.

The DVR password is not available yet. That is deliberately not a blocker, and
these tests run without it — a camera with no credentials reports
`CREDENTIALS_MISSING` and declines to dial, which is a state, not a crash.
"""

from __future__ import annotations

import pytest

from vosvc_harness.sources.cctv import (
    REDACTED,
    CameraConfig,
    CameraCredentials,
    CameraState,
    CredentialsMissingError,
    LiveRtspSource,
    ReconnectPolicy,
    cameras_from_env,
    parse_channels,
    selected,
)

HOST = "gayatri.freemyip.com"
SECRET = "sup3r-s3cret-dvr-pw"


def camera(channel: int = 1, **kwargs) -> CameraConfig:
    payload = {
        "camera_id": f"cam-{channel:02d}",
        "name": f"Camera {channel}",
        "channel": channel,
        "host": HOST,
    }
    payload.update(kwargs)
    return CameraConfig(**payload)


def creds() -> CameraCredentials:
    return CameraCredentials(username="admin", password=SECRET)


class TestChannelSelection:
    def test_channels_parse_in_order_without_duplicates(self) -> None:
        assert parse_channels("1,2,5,7") == (1, 2, 5, 7)
        assert parse_channels("3, 3 ,1") == (3, 1)

    def test_a_typo_raises_rather_than_dropping_a_camera(self) -> None:
        """A silently skipped channel is a kitchen nobody is watching."""
        with pytest.raises(ValueError, match="not a number"):
            parse_channels("1,two,3")

    def test_nothing_is_enabled_by_default(self) -> None:
        """The DVR has 16 channels. Zero are processed until someone says so."""
        assert cameras_from_env({"CCTV_HOST": HOST}) == ()
        assert cameras_from_env({}) == ()

    def test_only_named_channels_become_cameras(self) -> None:
        cameras = cameras_from_env({"CCTV_HOST": HOST, "CCTV_CHANNELS": "1,5,7"})
        assert [c.channel for c in cameras] == [1, 5, 7]
        assert len(cameras) == 3, "16-channel DVR must not yield 16 cameras"

    def test_a_disabled_camera_is_excluded_from_the_selection(self) -> None:
        cameras = (camera(1), camera(2, enabled=False), camera(3))
        assert [c.channel for c in selected(cameras)] == [1, 3]

    def test_a_disabled_camera_never_dials(self) -> None:
        """It must hold no socket and consume nothing."""
        opened: list[str] = []
        source = LiveRtspSource(
            camera(2, enabled=False),
            credentials=creds(),
            opener=lambda url: opened.append(url),
        )
        assert source.connect() is CameraState.DISABLED
        assert opened == []


class TestCredentialSafety:
    def test_the_real_url_carries_the_password(self) -> None:
        assert SECRET in camera().rtsp_url(creds())

    def test_the_redacted_url_never_does(self) -> None:
        redacted = camera().redacted_url(creds())
        assert SECRET not in redacted
        assert REDACTED in redacted
        assert HOST in redacted, "still diagnosable"

    def test_status_exposes_only_the_redacted_url(self) -> None:
        """`to_wire` feeds the console. It must be safe by construction."""
        source = LiveRtspSource(camera(), credentials=creds())
        wire = source.status.to_wire()
        assert SECRET not in str(wire)
        assert REDACTED in wire["url"]

    def test_a_connection_error_is_redacted(self) -> None:
        """Provider errors quote the URL they failed on — password included."""

        def exploding(url: str):
            raise OSError(f"failed to open {url}")

        source = LiveRtspSource(camera(), credentials=creds(), opener=exploding)
        assert source.connect() is CameraState.ERROR
        assert SECRET not in source.status.last_error
        assert REDACTED in source.status.last_error

    def test_credentials_repr_hides_the_password(self) -> None:
        assert SECRET not in repr(creds())

    def test_credentials_come_from_the_environment(self) -> None:
        loaded = CameraCredentials.from_env(
            {"CCTV_USERNAME": "admin", "CCTV_PASSWORD": SECRET}
        )
        assert loaded.available and loaded.password == SECRET


class TestMissingCredentials:
    """The password does not exist yet. Nothing here may depend on it."""

    def test_a_camera_without_credentials_reports_rather_than_crashes(self) -> None:
        source = LiveRtspSource(camera(), credentials=CameraCredentials())
        assert source.status.state is CameraState.CREDENTIALS_MISSING

    def test_it_declines_to_dial(self) -> None:
        """Retrying a blank password counts toward DVR account lockout."""
        opened: list[str] = []
        source = LiveRtspSource(
            camera(),
            credentials=CameraCredentials(),
            opener=lambda url: opened.append(url),
        )
        assert source.connect() is CameraState.CREDENTIALS_MISSING
        assert opened == []

    def test_missing_credentials_is_not_an_error_state(self) -> None:
        """Nothing is broken; something is absent. Retrying cannot help, and
        conflating the two sends an operator to debug a working camera."""
        source = LiveRtspSource(camera(), credentials=CameraCredentials())
        assert source.status.state is not CameraState.ERROR

    def test_building_a_url_without_credentials_raises_clearly(self) -> None:
        with pytest.raises(CredentialsMissingError, match="CCTV_USERNAME"):
            camera().rtsp_url(CameraCredentials())


class TestUrlConstruction:
    def test_sub_stream_is_the_default(self) -> None:
        assert camera().stream_type == "sub"
        assert "subtype=1" in camera().redacted_url()

    def test_main_stream_is_selectable(self) -> None:
        assert "subtype=0" in camera(stream_type="main").redacted_url()

    def test_the_channel_reaches_the_url(self) -> None:
        assert "channel=7" in camera(7).redacted_url()

    def test_an_unknown_stream_type_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="stream_type"):
            camera(stream_type="ultra")

    def test_channel_numbering_starts_at_one(self) -> None:
        with pytest.raises(ValueError, match="channel"):
            camera(0)


class TestCameraState:
    def test_a_configured_camera_starts_not_live(self) -> None:
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        assert not source.status.state.is_live

    def test_a_successful_open_is_connected(self) -> None:
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        assert source.connect() is CameraState.CONNECTED
        assert source.status.state.is_live

    def test_a_received_frame_moves_it_to_running(self) -> None:
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        source.connect()
        source.note_frame(timestamp_ms=1234.5)
        assert source.status.state is CameraState.RUNNING
        assert source.status.frames_received == 1

    def test_capture_time_is_the_sources_own(self) -> None:
        """Freshness ages against capture time. Stamping arrival here would make
        every observation look newer than it is."""
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        source.connect()
        source.note_frame(timestamp_ms=98_765.0)
        assert source.status.last_frame_ms == 98_765.0


class TestReconnect:
    def test_a_drop_moves_to_reconnecting_and_counts(self) -> None:
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        source.connect()
        source.note_disconnect("stream ended")
        assert source.status.state is CameraState.RECONNECTING
        assert source.status.reconnects == 1

    def test_backoff_grows_and_is_bounded(self) -> None:
        """An unbounded retry loop against a DVR is a denial of service against
        your own kitchen."""
        policy = ReconnectPolicy(initial_ms=500, multiplier=2.0, max_ms=8_000)
        assert policy.delay_for(1) == 500
        assert policy.delay_for(2) == 1_000
        assert policy.delay_for(20) == 8_000, "capped"

    def test_indefinite_retry_is_patient_not_a_loop(self) -> None:
        policy = ReconnectPolicy(max_attempts=0)
        assert policy.should_retry(9_999)
        assert policy.delay_for(9_999) == policy.max_ms

    def test_a_reconnect_reason_is_redacted(self) -> None:
        source = LiveRtspSource(camera(), credentials=creds(), opener=lambda url: object())
        source.note_disconnect(f"lost rtsp://admin:{SECRET}@host/stream")
        assert SECRET not in source.status.last_error


class TestCameraIndependence:
    def test_one_camera_failing_leaves_another_running(self) -> None:
        """Camera 2 dying must not stop Camera 1 — §12 of the brief, and the
        reason each camera owns its own source instance."""

        def exploding(url: str):
            raise OSError("connection refused")

        good = LiveRtspSource(camera(1), credentials=creds(), opener=lambda url: object())
        bad = LiveRtspSource(camera(2), credentials=creds(), opener=exploding)

        assert good.connect() is CameraState.CONNECTED
        assert bad.connect() is CameraState.ERROR
        good.note_frame(timestamp_ms=10.0)

        assert good.status.state is CameraState.RUNNING, "camera 1 unaffected"
        assert good.status.reconnects == 0

    def test_cameras_keep_distinct_identity(self) -> None:
        """Frames must stay attributable, or tracks from two kitchens merge."""
        cameras = cameras_from_env({"CCTV_HOST": HOST, "CCTV_CHANNELS": "1,2"})
        ids = {c.camera_id for c in cameras}
        assert len(ids) == 2 and ids == {"cam-01", "cam-02"}


class TestLiveSemantics:
    def test_a_live_source_declares_itself_unseekable(self) -> None:
        """A live camera cannot be scrubbed. Claiming otherwise would let the
        platform try to protect completeness it can never deliver."""
        source = LiveRtspSource(camera(), credentials=creds())
        assert source.capabilities()["seekable"] is False

    def test_analysis_fps_is_independent_of_camera_fps(self) -> None:
        """A 25 fps stream must not become 25 fps of detection and VLM work."""
        assert camera().analysis_fps == 4.0
        assert camera(analysis_fps=2.0).analysis_fps == 2.0

    def test_a_non_positive_analysis_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="analysis_fps"):
            camera(analysis_fps=0)
