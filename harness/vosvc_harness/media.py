"""Media assets — uploaded and discovered video.

Holds files and decoded frame sets. Owns no Vision OS state and produces no
facts: a media asset is an *input*, and the only thing this module decides is
whether a file can be opened at all.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sources.decoding import (
    CONTAINER_SUFFIXES,
    DEFAULT_SAMPLE_FPS,
    IMAGE_SUFFIXES,
    MAX_SAMPLED_FRAMES,
    DecodedFrame,
    DecodeUnavailableError,
    MediaProbe,
    VideoReader,
    _decode_images,
    available_backends,
    can_decode,
    probe_only,
    synthetic_frames,
)

SAFE_SUFFIXES = CONTAINER_SUFFIXES | IMAGE_SUFFIXES | {".raw"}


@dataclass(slots=True)
class MediaAsset:
    """One replayable input.

    **Frames are decoded lazily.** `probe` is filled from the container header at
    discovery; `_frames` stays `None` until someone opens a session.

    This was not the original design and the original was wrong: eager decoding
    turned "list the media library" into gigabytes of BGR24. Four uploaded HD
    videos — 1,500 frames at ~2.7 MB each — cost roughly 4 GB before a single
    session existed, on a machine with 15.5 GB total that also has to hold a
    5.8 GB VLM.
    """

    media_id: str
    name: str
    kind: str  # video_file | frame_folder | synthetic | rtsp_replay
    path: Path | None
    probe: MediaProbe | None = None
    error: str | None = None
    created_at_ns: int = field(default_factory=time.time_ns)
    #: The memory backstop, in sampled frames. Not a duration limit — see
    #: `sample_fps`, which is what decides how much of a video is covered.
    max_frames: int = MAX_SAMPLED_FRAMES
    #: How often the video is sampled for analysis. The number of frames an
    #: asset yields is its **duration** times this rate, so a longer video simply
    #: produces more of them.
    sample_fps: float = DEFAULT_SAMPLE_FPS
    _frames: list[DecodedFrame] | None = field(default=None, repr=False)

    @property
    def usable(self) -> bool:
        """Openable — **not** "already decoded".

        A probe that succeeded means the container is readable. Requiring decoded
        frames here would make every asset unusable until something forced the
        decode, which is the eager behaviour this class exists to avoid.
        """
        return self.error is None and (self.probe is not None or bool(self._frames))

    @property
    def decoded(self) -> bool:
        return self._frames is not None

    def frames(self) -> list[DecodedFrame]:
        """Decode on first use, then cache.

        Raises:
            DecodeUnavailableError: the file probed clean but will not decode.
                Surfaced rather than returning an empty list, because a session
                over zero frames looks like a camera that saw nothing.
        """
        if self._frames is None:
            self._frames = self._decode()
        return list(self._frames)

    def release(self) -> None:
        """Drop decoded pixels, keep the metadata."""
        self._frames = None

    def _decode(self) -> list[DecodedFrame]:
        if self.kind == "frame_folder" and self.path is not None:
            images = sorted(p for p in self.path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
            frames, _ = _decode_images(images, max_frames=self.max_frames, stride=1)
            return frames
        if self.path is None:
            raise DecodeUnavailableError(f"'{self.name}' has no backing file to decode")
        reader = VideoReader(self.path, max_frames=self.max_frames, sample_fps=self.sample_fps)
        # The decoder walked the whole container and knows what was actually
        # taken from it. Replacing the header probe with that keeps `frame_count`
        # and `duration_ms` describing the frames a session will really replay —
        # the header's numbers describe the source, and are kept beside them as
        # `source_fps` / `source_frame_count`.
        self.probe = reader.probe
        return reader.frames

    def to_wire(self) -> dict[str, Any]:
        return {
            "media_id": self.media_id,
            "name": self.name,
            "kind": self.kind,
            "path": str(self.path) if self.path else None,
            "usable": self.usable,
            "decoded": self.decoded,
            "error": self.error,
            "created_at_ns": self.created_at_ns,
            "sample_fps": self.sample_fps,
            "probe": (
                {
                    "frame_count": self.probe.frame_count,
                    "width": self.probe.width,
                    "height": self.probe.height,
                    "fps": self.probe.fps,
                    "duration_ms": self.probe.duration_ms,
                    "backend": self.probe.backend,
                    "seekable": self.probe.seekable,
                    "source_fps": self.probe.source_fps,
                    "source_frame_count": self.probe.source_frame_count,
                    "truncated": self.probe.truncated,
                }
                if self.probe
                else None
            ),
        }


class MediaLibrary:
    """The set of things an engineer can replay."""

    def __init__(self, root: Path, *, max_frames: int = MAX_SAMPLED_FRAMES) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._assets: dict[str, MediaAsset] = {}
        self._max_frames = max_frames
        self._seed_synthetic()

    # --- construction ------------------------------------------------------- #

    def _seed_synthetic(self) -> None:
        """Always offer at least one input.

        A console that cannot demonstrate itself on a fresh checkout is a console
        nobody will trust enough to debug with. The synthetic sequence needs no
        codec, no footage and no network.
        """
        frames, probe = synthetic_frames(count=150, width=96, height=96)
        asset = MediaAsset(
            media_id="m-synthetic",
            name="synthetic-moving-target",
            kind="synthetic",
            path=None,
            probe=probe,
            _frames=frames,
        )
        self._assets[asset.media_id] = asset

    def discover(self) -> list[MediaAsset]:
        """Index anything already sitting in the media root."""
        for path in sorted(self.root.iterdir()) if self.root.exists() else []:
            if path.is_dir():
                self._register_folder(path)
            elif path.suffix.lower() in SAFE_SUFFIXES:
                if not any(a.path == path for a in self._assets.values()):
                    self.register_file(path)
        return self.all()

    def register_file(self, path: Path, *, name: str | None = None) -> MediaAsset:
        media_id = f"m-{uuid.uuid4().hex[:10]}"
        asset = MediaAsset(
            media_id=media_id,
            name=name or path.name,
            kind="video_file",
            path=path,
        )
        if not can_decode(path):
            asset.error = (
                f"no decoding backend can open '{path.suffix}'; available backends: "
                f"{', '.join(available_backends())}. Install the 'av' or 'cv' extra."
            )
        else:
            # Header only. Frames arrive when a session asks for them.
            try:
                asset.probe = probe_only(path)
            except DecodeUnavailableError as exc:
                asset.error = str(exc)
            except Exception as exc:  # noqa: BLE001
                asset.error = f"{type(exc).__name__}: {exc}"

        self._assets[media_id] = asset
        return asset

    def _register_folder(self, path: Path) -> MediaAsset | None:
        images = sorted(
            p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
        )
        if not images:
            return None
        if any(a.path == path for a in self._assets.values()):
            return None
        media_id = f"m-{uuid.uuid4().hex[:10]}"
        asset = MediaAsset(
            media_id=media_id,
            name=path.name,
            kind="frame_folder",
            path=path,
            max_frames=self._max_frames,
            # A folder's frame count is knowable without opening any image.
            probe=MediaProbe(
                frame_count=len(images),
                width=0,
                height=0,
                fps=25.0,
                duration_ms=len(images) * 40.0,
                backend="raw",
            ),
        )
        self._assets[media_id] = asset
        return asset

    def save_upload(self, filename: str, data: bytes) -> MediaAsset:
        safe = Path(filename).name
        if Path(safe).suffix.lower() not in SAFE_SUFFIXES:
            media_id = f"m-{uuid.uuid4().hex[:10]}"
            asset = MediaAsset(
                media_id=media_id,
                name=safe,
                kind="video_file",
                path=None,
                error=(
                    f"'{Path(safe).suffix}' is not an accepted media suffix; "
                    f"accepted: {', '.join(sorted(SAFE_SUFFIXES))}"
                ),
            )
            self._assets[media_id] = asset
            return asset

        destination = self.root / f"{uuid.uuid4().hex[:8]}-{safe}"
        destination.write_bytes(data)
        return self.register_file(destination, name=safe)

    # --- access -------------------------------------------------------------- #

    def get(self, media_id: str) -> MediaAsset | None:
        return self._assets.get(media_id)

    def all(self) -> list[MediaAsset]:
        return sorted(self._assets.values(), key=lambda a: a.created_at_ns)

    def remove(self, media_id: str) -> bool:
        asset = self._assets.pop(media_id, None)
        if asset is None:
            return False
        if asset.path and asset.path.is_file() and asset.path.parent == self.root:
            try:
                asset.path.unlink()
            except OSError:
                pass
        elif asset.path and asset.path.is_dir() and asset.path.parent == self.root:
            shutil.rmtree(asset.path, ignore_errors=True)
        return True

    def capabilities(self) -> dict[str, Any]:
        """What this harness can actually open — stated, not implied."""
        backends = available_backends()
        containers = _try_containers(backends)
        return {
            "backends": list(backends),
            "containers": containers,
            "images": sorted(IMAGE_SUFFIXES),
            "always_available": ["synthetic", "frame_folder(bmp,ppm)", ".raw"],
        }


def _try_containers(backends: tuple[str, ...]) -> dict[str, bool]:
    decodable = "pyav" in backends or "opencv" in backends
    return {suffix: decodable for suffix in sorted(CONTAINER_SUFFIXES)}
