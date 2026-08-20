"""Harness configuration.

Every setting is an environment variable with a safe default. Two of them are
security-relevant and both default to **off**:

- ``VOSVC_SERVE_FRAMES`` — whether decoded pixels may leave the harness at all
  (docs/CONTRACT.md §4.3, invariant V12).
- ``VOSVC_ALLOW_EVIDENCE`` — whether the console may retrieve crop imagery
  (12_SECURITY §5.3: *"Reading 'a person was here' and viewing their image are
  categorically different acts"*).

Neither is something a console user can turn on. They are deployment decisions,
which is the only place they can honestly live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: This repository's root — `.../atlas/vision_os_validation_console`.
#: config.py → vosvc_harness → harness → vision_os_validation_console
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The Atlas workspace that contains both this repo and `backend/`.
_WORKSPACE = _REPO_ROOT.parent


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """What this harness process will and will not do."""

    host: str = "127.0.0.1"
    port: int = 8808

    #: "file" (recorded media) or "rtsp" (live CCTV). File remains the default,
    #: so every existing replay, test and comparison keeps working untouched —
    #: live is added beside it, never in place of it.
    video_source: str = field(
        default_factory=lambda: os.environ.get("VIDEO_SOURCE", "file").strip() or "file"
    )
    #: The DVR. Empty means no live cameras are configured, which is a valid
    #: state and not an error.
    cctv_host: str = field(default_factory=lambda: os.environ.get("CCTV_HOST", "").strip())
    #: Explicit allowlist, e.g. "1,2,5,7". Empty selects **nothing**: a
    #: 16-channel DVR must not become 16 pipelines by default.
    cctv_channels: str = field(
        default_factory=lambda: os.environ.get("CCTV_CHANNELS", "").strip()
    )

    #: Where the Vision OS package can be imported from. The harness adds this to
    #: ``sys.path`` and imports ``app.vision_os`` — the platform's public
    #: composition roots and nothing else. It never writes here.
    vision_os_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("VOSVC_VISION_OS_ROOT", str(_WORKSPACE / "backend"))
        )
    )

    media_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("VOSVC_MEDIA_ROOT", str(_REPO_ROOT / "media"))
        )
    )

    tenant_id: str = "t-eng"
    site_id: str = "site-eng"

    #: Serve decoded frames over HTTP. Off by default (V12 — see module docstring).
    serve_frames: bool = field(default_factory=lambda: _flag("VOSVC_SERVE_FRAMES", False))

    #: Permit `GET /evidence/{ref}`. Off by default, and separately from
    #: observation access, exactly as 12_SECURITY §5.3 separates the privileges.
    allow_evidence: bool = field(default_factory=lambda: _flag("VOSVC_ALLOW_EVIDENCE", False))

    #: Bounded, always. 09_API §3.4 forbids unbounded buffering, so there is no
    #: value of this setting that means "unlimited".
    stream_queue_capacity: int = field(
        default_factory=lambda: max(16, _int("VOSVC_STREAM_QUEUE", 2048))
    )

    #: Retained tap records per channel per session. A validation console holds
    #: history so an engineer can scrub backwards; a ring bound keeps a long soak
    #: from becoming a memory leak.
    tap_history: int = field(default_factory=lambda: max(64, _int("VOSVC_TAP_HISTORY", 20_000)))

    max_upload_bytes: int = field(
        default_factory=lambda: _int("VOSVC_MAX_UPLOAD_MB", 2048) * 1024 * 1024
    )

    metrics_sweep_ms: float = field(default_factory=lambda: _float("VOSVC_METRICS_SWEEP_MS", 500.0))

    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.environ.get(
                "VOSVC_CORS_ORIGINS", "http://localhost:5273,http://127.0.0.1:5273"
            ).split(",")
            if o.strip()
        )
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_root", Path(self.media_root))
        object.__setattr__(self, "vision_os_root", Path(self.vision_os_root))
        self.media_root.mkdir(parents=True, exist_ok=True)


def load_config() -> HarnessConfig:
    """Build the process's configuration, demo defaults first.

    The defaults must land in the environment **before** ``HarnessConfig`` is
    constructed, because its fields read the environment in their own
    ``default_factory``. Applying them afterwards sets the variables and leaves
    the object everything else consults still holding the old values — which is
    exactly the bug this call fixes: frame serving was enabled in the process and
    the config object still reported it off.

    Constructing ``HarnessConfig()`` directly bypasses this, which is what the
    test-suite wants: a test asking for the library defaults should get them.
    """
    from .defaults import apply_demo_defaults

    apply_demo_defaults()
    return HarnessConfig(
        host=os.environ.get("VOSVC_HOST", "127.0.0.1"),
        port=_int("VOSVC_PORT", 8808),
        tenant_id=os.environ.get("VOSVC_TENANT", "t-eng"),
        site_id=os.environ.get("VOSVC_SITE", "site-eng"),
    )
