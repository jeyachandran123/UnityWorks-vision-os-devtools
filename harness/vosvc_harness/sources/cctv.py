"""Live CCTV cameras as a P1 acquisition source.

The one architectural fact this file exists to establish: **a live RTSP stream
enters Vision OS through the same seam a replayed file does.** Everything after
the source — detection, tracking, registry, cropping, understanding, freshness,
compliance — is untouched and cannot tell the difference.

`RtspReplaySource` next door is *not* this. It replays a decoded file under
live-stream semantics (`seekable=False`, `REALTIME`) so validation exercises the
code path RTSP takes. Useful, and not a camera. `LiveRtspSource` opens a socket.

### Credentials

Never in source, never in a log line, never in an API response. They are read
from the environment at composition time, and every string this module can emit
is redacted. The DVR password for this deployment is **not yet available**, and
that is deliberately not a blocker: a camera configured without one reports
`CREDENTIALS_MISSING` and refuses to dial, rather than crashing or — worse —
retrying a bad password until the DVR locks the account out.

### Selective channels

The DVR has 16 channels; roughly 5-7 are wanted. A channel that is not
explicitly enabled opens no socket, decodes nothing, and reaches no VLM. Cost
follows configuration, not hardware.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Dahua's RTSP path, correct for the XVR5116HS family and **unverified against
#: this device** — no credentials exist yet to test it. Kept in one place so a
#: single edit corrects every camera if the DVR wants a different path.
DAHUA_PATH = "/cam/realmonitor?channel={channel}&subtype={subtype}"

#: Sub-stream by default: the objective is proving live ingestion, not moving
#: the most pixels. Main stream is selectable per camera.
DEFAULT_STREAM_TYPE = "sub"

STREAM_SUBTYPE = {"main": 0, "sub": 1}

REDACTED = "***"


class CredentialsMissingError(RuntimeError):
    """Raised instead of dialling with an empty password."""


class CameraState(enum.Enum):
    """Truthful, not decorative. Every value is a state an operator can act on."""

    CONFIGURED = "configured"
    CREDENTIALS_MISSING = "credentials_missing"
    """Cannot connect and will not try. Distinct from ERROR: nothing is broken,
    something is absent, and retrying cannot help."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    DISABLED = "disabled"
    """Not in the allowlist. Holds no socket and consumes nothing."""

    @property
    def is_live(self) -> bool:
        return self in (CameraState.CONNECTED, CameraState.RUNNING)


@dataclass(frozen=True, slots=True)
class CameraCredentials:
    """Read from the environment. Never constructed from a literal in source."""

    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def available(self) -> bool:
        return bool(self.username and self.password)

    @classmethod
    def from_env(cls, env: dict | None = None) -> CameraCredentials:
        source = env if env is not None else os.environ
        return cls(
            username=source.get("CCTV_USERNAME", "").strip(),
            password=source.get("CCTV_PASSWORD", "").strip(),
        )

    def __repr__(self) -> str:
        return f"CameraCredentials(username={self.username!r}, password={REDACTED!r})"


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """One selected DVR channel.

    ``enabled`` is the allowlist. A 16-channel DVR must not become 16 decoders
    and 16 VLM budgets because nobody said otherwise.
    """

    camera_id: str
    name: str
    channel: int
    host: str
    rtsp_port: int = 554
    enabled: bool = True
    stream_type: str = DEFAULT_STREAM_TYPE
    analysis_fps: float = 4.0
    """What Vision OS analyses, not what the camera sends. A 25 fps stream does
    not become 25 fps of detection, tracking and VLM work."""

    def __post_init__(self) -> None:
        if self.channel < 1:
            raise ValueError(f"channel must be >= 1, got {self.channel}")
        if self.stream_type not in STREAM_SUBTYPE:
            raise ValueError(
                f"stream_type must be one of {sorted(STREAM_SUBTYPE)}, "
                f"got {self.stream_type!r}"
            )
        if self.analysis_fps <= 0:
            raise ValueError("analysis_fps must be positive")

    @property
    def subtype(self) -> int:
        return STREAM_SUBTYPE[self.stream_type]

    def rtsp_url(self, credentials: CameraCredentials) -> str:
        """The real URL, with the real password. **Never log the result.**

        Raises:
            CredentialsMissingError: rather than emitting a URL that would fail
                authentication and, on a Dahua, count toward account lockout.
        """
        if not credentials.available:
            raise CredentialsMissingError(
                f"camera '{self.camera_id}' has no credentials; "
                f"set CCTV_USERNAME and CCTV_PASSWORD"
            )
        path = DAHUA_PATH.format(channel=self.channel, subtype=self.subtype)
        return (
            f"rtsp://{credentials.username}:{credentials.password}"
            f"@{self.host}:{self.rtsp_port}{path}"
        )

    def redacted_url(self, credentials: CameraCredentials | None = None) -> str:
        """The form that may be logged, shown in an API response, or put in an
        error: everything an engineer needs to diagnose, nothing an attacker
        needs to connect."""
        user = credentials.username if credentials and credentials.username else "USER"
        path = DAHUA_PATH.format(channel=self.channel, subtype=self.subtype)
        return f"rtsp://{user}:{REDACTED}@{self.host}:{self.rtsp_port}{path}"


@dataclass(slots=True)
class CameraStatus:
    """What the console may show. Contains no secret by construction."""

    camera_id: str
    name: str
    channel: int
    state: CameraState = CameraState.CONFIGURED
    last_frame_ms: float | None = None
    frames_received: int = 0
    reconnects: int = 0
    last_error: str = ""
    redacted_url: str = ""

    def to_wire(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "channel": self.channel,
            "state": self.state.value,
            "last_frame_ms": self.last_frame_ms,
            "frames_received": self.frames_received,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
            "url": self.redacted_url,
        }


# --- configuration ---------------------------------------------------------- #


def parse_channels(raw: str) -> tuple[int, ...]:
    """Parse ``CCTV_CHANNELS=1,2,5,7`` into an ordered, de-duplicated allowlist.

    An unparseable entry raises rather than being skipped: a typo that silently
    dropped a camera would present as a kitchen nobody was watching.
    """
    out: list[int] = []
    for piece in (p.strip() for p in raw.split(",")):
        if not piece:
            continue
        try:
            channel = int(piece)
        except ValueError as exc:
            raise ValueError(
                f"channel list contains {piece!r}, which is not a number"
            ) from exc
        if channel < 1:
            raise ValueError(f"channel numbers start at 1, got {channel}")
        if channel not in out:
            out.append(channel)
    return tuple(out)


def cameras_from_env(env: dict | None = None) -> tuple[CameraConfig, ...]:
    """Build the selected-camera list from configuration.

    Nothing is enabled by default. A DVR with 16 channels yields **zero**
    cameras until someone names the ones they want.
    """
    source = env if env is not None else os.environ
    host = source.get("CCTV_HOST", "").strip()
    if not host:
        return ()
    channels = parse_channels(source.get("CCTV_CHANNELS", ""))
    port = int(source.get("CCTV_RTSP_PORT", "554"))
    stream = (
        source.get("CCTV_STREAM_TYPE", DEFAULT_STREAM_TYPE).strip()
        or DEFAULT_STREAM_TYPE
    )
    fps = float(source.get("CCTV_ANALYSIS_FPS", "4.0"))
    return tuple(
        CameraConfig(
            camera_id=f"cam-{channel:02d}",
            name=f"Camera {channel}",
            channel=channel,
            host=host,
            rtsp_port=port,
            enabled=True,
            stream_type=stream,
            analysis_fps=fps,
        )
        for channel in channels
    )


def selected(cameras: Sequence[CameraConfig]) -> tuple[CameraConfig, ...]:
    """Only enabled cameras. A disabled one opens no socket."""
    return tuple(c for c in cameras if c.enabled)


# --- reconnect -------------------------------------------------------------- #


@dataclass(slots=True)
class ReconnectPolicy:
    """Bounded backoff. An unbounded retry loop against a DVR is a denial of
    service against your own kitchen."""

    initial_ms: float = 500.0
    max_ms: float = 30_000.0
    multiplier: float = 2.0
    max_attempts: int = 0
    """0 retries indefinitely — but always at ``max_ms``. Patient, not a loop."""

    def delay_for(self, attempt: int) -> float:
        """Backoff for the nth attempt, capped at ``max_ms``.

        The exponent is clamped before it is evaluated: a camera that has been
        down for a day reaches attempt 9 999, and ``2.0 ** 9998`` raises
        `OverflowError` — a reconnect policy that crashes the reconnect is worse
        than no policy at all.
        """
        if attempt <= 0:
            return 0.0
        if self.multiplier <= 1.0:
            return min(self.initial_ms, self.max_ms)
        import math

        # Beyond this the result is capped anyway, so never compute it.
        ceiling = math.log(max(self.max_ms / self.initial_ms, 1.0), self.multiplier) + 1
        exponent = min(attempt - 1, int(ceiling))
        return min(self.initial_ms * (self.multiplier ** exponent), self.max_ms)

    def should_retry(self, attempt: int) -> bool:
        return self.max_attempts == 0 or attempt < self.max_attempts


# --- the source ------------------------------------------------------------- #


def _redact(text: str, credentials: CameraCredentials) -> str:
    """Strip anything secret from a string bound for a log, error or response."""
    if credentials.password:
        return text.replace(credentials.password, REDACTED)
    return text


def _open_rtsp(url: str):
    """Open with PyAV — the decoder this repository already uses.

    TCP transport and a bounded timeout: RTSP over UDP across the public
    internet loses packets, and an unbounded open would stall the session
    forever on an unreachable DVR.
    """
    import av

    return av.open(
        url,
        options={"rtsp_transport": "tcp", "stimeout": "5000000"},
        timeout=10.0,
    )


class LiveRtspSource:
    """A P1 source backed by a real RTSP stream.

    Presents the same surface ``ReplayFileSource`` does — ``capabilities``,
    ``connect``, ``close`` — so the composition root can substitute it without
    any downstream component knowing.

    ``seekable=False`` and ``REALTIME`` semantics, truthfully: a live camera
    cannot be scrubbed, and a source claiming otherwise would let the platform
    try to protect completeness it can never deliver.

    **One camera per instance.** Camera 2 failing must not disturb Camera 1, and
    the cheapest way to guarantee that is to give them nothing to share.
    """

    def __init__(
        self,
        config: CameraConfig,
        *,
        clock=None,
        credentials: CameraCredentials | None = None,
        reconnect: ReconnectPolicy | None = None,
        opener=None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._credentials = credentials or CameraCredentials.from_env()
        self._reconnect = reconnect or ReconnectPolicy()
        # Injected so the adapter is testable without a DVR. Production passes
        # nothing and gets the PyAV opener.
        self._opener = opener
        self._container = None
        self.status = CameraStatus(
            camera_id=config.camera_id,
            name=config.name,
            channel=config.channel,
            state=CameraState.CONFIGURED if config.enabled else CameraState.DISABLED,
            redacted_url=config.redacted_url(self._credentials),
        )
        if config.enabled and not self._credentials.available:
            self.status.state = CameraState.CREDENTIALS_MISSING

    @property
    def config(self) -> CameraConfig:
        return self._config

    @property
    def reconnect_policy(self) -> ReconnectPolicy:
        return self._reconnect

    def capabilities(self) -> dict:
        """Live semantics, declared honestly.

        ``seekable`` is a plain bool and must be answerable without importing
        Vision OS — a source has to be able to describe itself before the
        platform it feeds is on the path. The semantics enum is resolved lazily
        for the same reason.
        """
        semantics = "realtime"
        try:
            from app.vision_os.core.model.camera import SourceSemantics

            semantics = SourceSemantics.REALTIME
        except ImportError:
            pass
        return {"semantics": semantics, "seekable": False}

    def connect(self) -> CameraState:
        """Open the stream, or explain why not. Never raises on a missing password."""
        if not self._config.enabled:
            self.status.state = CameraState.DISABLED
            return self.status.state
        if not self._credentials.available:
            self.status.state = CameraState.CREDENTIALS_MISSING
            self.status.last_error = "CCTV_USERNAME / CCTV_PASSWORD not set"
            return self.status.state

        self.status.state = CameraState.CONNECTING
        url = self._config.rtsp_url(self._credentials)
        try:
            self._container = (self._opener or _open_rtsp)(url)
            self.status.state = CameraState.CONNECTED
            self.status.last_error = ""
        except Exception as exc:  # noqa: BLE001 - the failure is the result
            self._container = None
            self.status.state = CameraState.ERROR
            # The URL carries the password; only a redacted form may escape.
            self.status.last_error = (
                f"{type(exc).__name__}: {_redact(str(exc), self._credentials)}"
            )
        return self.status.state

    def note_frame(self, timestamp_ms: float) -> None:
        """Record a received frame. Capture time is the source's, never ours —
        freshness ages against it."""
        self.status.frames_received += 1
        self.status.last_frame_ms = timestamp_ms
        self.status.state = CameraState.RUNNING

    def note_disconnect(self, reason: str = "") -> None:
        """A drop moves this camera to RECONNECTING and touches no other."""
        self.status.state = CameraState.RECONNECTING
        self.status.reconnects += 1
        if reason:
            self.status.last_error = _redact(reason, self._credentials)

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            finally:
                self._container = None
        self.status.state = CameraState.DISCONNECTED


__all__ = [
    "DAHUA_PATH",
    "DEFAULT_STREAM_TYPE",
    "REDACTED",
    "CameraConfig",
    "CameraCredentials",
    "CameraState",
    "CameraStatus",
    "CredentialsMissingError",
    "LiveRtspSource",
    "ReconnectPolicy",
    "cameras_from_env",
    "parse_channels",
    "selected",
]
