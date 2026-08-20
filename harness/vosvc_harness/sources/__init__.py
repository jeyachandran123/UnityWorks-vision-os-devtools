"""Validation source adapters — P1/P2 implementations for recorded video."""

from .cctv import (
    CameraConfig,
    CameraCredentials,
    CameraState,
    CameraStatus,
    LiveRtspSource,
    ReconnectPolicy,
    cameras_from_env,
    parse_channels,
    selected,
)
from .adapters import (
    RAW_CODEC,
    ReplayCursor,
    ReplayFileSource,
    RtspReplaySource,
    ValidationDecoder,
)
from .decoding import (
    CONTAINER_SUFFIXES,
    IMAGE_SUFFIXES,
    DecodedFrame,
    DecodeUnavailableError,
    MediaProbe,
    VideoReader,
    available_backends,
    can_decode,
    synthetic_frames,
)
from .faults import (
    EXPECTED_RESPONSE,
    QUALITY_SCENARIOS,
    FaultLedger,
    FaultSpec,
    Scenario,
)

__all__ = [
    "CONTAINER_SUFFIXES",
    "EXPECTED_RESPONSE",
    "IMAGE_SUFFIXES",
    "QUALITY_SCENARIOS",
    "RAW_CODEC",
    "DecodeUnavailableError",
    "DecodedFrame",
    "FaultLedger",
    "FaultSpec",
    "MediaProbe",
    "ReplayCursor",
    "ReplayFileSource",
    "RtspReplaySource",
    "CameraConfig",
    "CameraCredentials",
    "CameraState",
    "CameraStatus",
    "LiveRtspSource",
    "ReconnectPolicy",
    "cameras_from_env",
    "parse_channels",
    "selected",
    "Scenario",
    "ValidationDecoder",
    "VideoReader",
    "available_backends",
    "can_decode",
    "synthetic_frames",
]
