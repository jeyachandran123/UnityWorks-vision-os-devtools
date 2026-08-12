"""Video decoding backends — and an honest account of which ones exist.

Three backends, tried in order: **PyAV**, **OpenCV**, then nothing. The third is
not a failure mode; it is a declared capability level. A harness with no codec
library serves synthetic and frame-folder sources and reports `mp4: unavailable`
through `GET /capabilities`.

That distinction is invariant **V8** applied to the tool itself: an engineer must
be able to tell *"this container has no objects in it"* from *"this harness
cannot open this container"*. Returning zero frames for an MP4 we cannot decode
would conflate exactly those two claims — and a validation console that
mis-reports its own blindness cannot be trusted to report the platform's.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

CONTAINER_SUFFIXES = frozenset({".mp4", ".avi", ".mkv", ".mov", ".webm", ".m4v"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".ppm"})


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """One frame, already in BGR24, ready to become a `SourcePacket` payload."""

    index: int
    payload: bytes
    width: int
    height: int
    pts_ms: int
    is_keyframe: bool = True


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """What a backend could determine about a file without decoding all of it."""

    frame_count: int
    width: int
    height: int
    fps: float
    duration_ms: float
    backend: str
    seekable: bool = True


class DecodeUnavailableError(RuntimeError):
    """No backend can open this file.

    Distinct from "the file is empty". The caller surfaces this as a capability
    gap, never as an empty result.
    """


# --- backend detection ------------------------------------------------------ #


def _try_av():
    try:
        import av  # type: ignore

        return av
    except Exception:  # noqa: BLE001 - absence is a normal, reportable state
        return None


def _try_cv2():
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:  # noqa: BLE001
        return None


def available_backends() -> tuple[str, ...]:
    found = []
    if _try_av() is not None:
        found.append("pyav")
    if _try_cv2() is not None:
        found.append("opencv")
    found.append("raw")  # always present: synthetic + frame folders + .raw
    return tuple(found)


def probe_only(path: Path) -> MediaProbe:
    """Read a container's metadata **without decoding its frames**.

    Decoding is the expensive part by orders of magnitude: 456 frames of 720p is
    ~1.2 GB of BGR24 and several seconds of CPU, and a library with four uploaded
    videos would spend gigabytes discovering files nobody has asked to replay
    yet. Metadata comes from the container header.

    Raises:
        DecodeUnavailableError: nothing can open this file. Distinct from "the
            file is empty" — the caller reports a capability gap, never an empty
            result.
    """
    suffix = path.suffix.lower()

    if suffix == ".raw":
        stem_parts = path.stem.split("x")
        try:
            width, height, count = (int(stem_parts[i]) for i in range(3))
        except (ValueError, IndexError) as exc:
            raise DecodeUnavailableError(
                f"'{path.name}' must be named <width>x<height>x<count>.raw"
            ) from exc
        return MediaProbe(count, width, height, 25.0, count * 40.0, "raw")

    if suffix in IMAGE_SUFFIXES:
        return MediaProbe(1, 0, 0, 25.0, 40.0, "raw")

    av = _try_av()
    if av is not None and suffix in CONTAINER_SUFFIXES:
        try:
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                fps = float(stream.average_rate or 25.0)
                frames = int(stream.frames or 0)
                duration_s = (
                    float(stream.duration * stream.time_base) if stream.duration else 0.0
                )
                if not frames and duration_s:
                    frames = int(duration_s * fps)
                return MediaProbe(
                    frame_count=frames or 0,
                    width=int(stream.codec_context.width),
                    height=int(stream.codec_context.height),
                    fps=fps,
                    duration_ms=(duration_s or (frames / max(fps, 1e-6))) * 1000.0,
                    backend="pyav",
                )
        except Exception as exc:  # noqa: BLE001 - fall through to OpenCV
            last: Exception | None = exc
    else:
        last = None

    cv2 = _try_cv2()
    if cv2 is not None and suffix in CONTAINER_SUFFIXES:
        capture = cv2.VideoCapture(str(path))
        try:
            if capture.isOpened():
                fps = float(capture.get(cv2.CAP_PROP_FPS)) or 25.0
                frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
                return MediaProbe(
                    frame_count=frames,
                    width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    fps=fps,
                    duration_ms=(frames / max(fps, 1e-6)) * 1000.0,
                    backend="opencv",
                )
        finally:
            capture.release()

    raise DecodeUnavailableError(
        f"no backend could open '{path.name}' (suffix '{suffix}'); "
        f"available backends: {', '.join(available_backends())}"
        + (f"; last error: {last}" if last else "")
    )


def can_decode(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES or suffix == ".raw":
        return True
    if suffix in CONTAINER_SUFFIXES:
        return _try_av() is not None or _try_cv2() is not None
    return False


# --- readers ---------------------------------------------------------------- #


class VideoReader:
    """Decodes a container to BGR24 frames.

    Deliberately **eager and bounded**: frames are decoded once, into memory, up
    to `max_frames`. A validation session replays the same frames repeatedly —
    forwards, backwards, stepped, and again for a regression comparison — and
    re-decoding on every scrub would make timing measurements a property of the
    decoder rather than of Vision OS.

    Bounded because an unbounded decode of a 4-hour CCTV file is a memory leak
    with a plausible excuse.
    """

    __slots__ = ("_frames", "_probe", "path")

    def __init__(self, path: Path, *, max_frames: int = 3600, stride: int = 1) -> None:
        self.path = path
        frames, probe = _decode_all(path, max_frames=max_frames, stride=max(1, stride))
        self._frames = frames
        self._probe = probe

    @property
    def probe(self) -> MediaProbe:
        return self._probe

    @property
    def frames(self) -> list[DecodedFrame]:
        return self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self) -> Iterator[DecodedFrame]:
        return iter(self._frames)

    def at(self, index: int) -> DecodedFrame | None:
        if 0 <= index < len(self._frames):
            return self._frames[index]
        return None


def _decode_all(
    path: Path, *, max_frames: int, stride: int
) -> tuple[list[DecodedFrame], MediaProbe]:
    suffix = path.suffix.lower()

    if suffix == ".raw":
        return _decode_raw(path, max_frames=max_frames)
    if suffix in IMAGE_SUFFIXES:
        frames, probe = _decode_images([path], max_frames=max_frames, stride=1)
        return frames, probe

    av = _try_av()
    if av is not None and suffix in CONTAINER_SUFFIXES:
        try:
            return _decode_with_av(av, path, max_frames=max_frames, stride=stride)
        except Exception as exc:  # noqa: BLE001 - fall through to the next backend
            last = exc
    else:
        last = None

    cv2 = _try_cv2()
    if cv2 is not None and suffix in CONTAINER_SUFFIXES:
        try:
            return _decode_with_cv2(cv2, path, max_frames=max_frames, stride=stride)
        except Exception as exc:  # noqa: BLE001
            last = exc

    raise DecodeUnavailableError(
        f"no backend could open '{path.name}' (suffix '{suffix}'); "
        f"available backends: {', '.join(available_backends())}"
        + (f"; last error: {last}" if last else "")
    )


def _decode_with_av(av, path: Path, *, max_frames: int, stride: int):
    frames: list[DecodedFrame] = []
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        fps = float(stream.average_rate or 25.0)
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
        raw_index = 0
        for frame in container.decode(stream):
            if raw_index % stride == 0:
                array = frame.to_ndarray(format="bgr24")
                frames.append(
                    DecodedFrame(
                        index=len(frames),
                        payload=array.tobytes(),
                        width=array.shape[1],
                        height=array.shape[0],
                        pts_ms=int(len(frames) * (1000.0 / max(fps, 1e-6))),
                        is_keyframe=bool(frame.key_frame),
                    )
                )
                if len(frames) >= max_frames:
                    break
            raw_index += 1

    if not frames:
        raise DecodeUnavailableError(f"'{path.name}' contained no decodable video frames")

    return frames, MediaProbe(
        frame_count=len(frames),
        width=frames[0].width,
        height=frames[0].height,
        fps=fps / stride,
        duration_ms=len(frames) * (1000.0 / max(fps / stride, 1e-6)),
        backend="pyav",
    )


def _decode_with_cv2(cv2, path: Path, *, max_frames: int, stride: int):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise DecodeUnavailableError(f"OpenCV could not open '{path.name}'")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS)) or 25.0
        frames: list[DecodedFrame] = []
        raw_index = 0
        while len(frames) < max_frames:
            ok, image = capture.read()
            if not ok:
                break
            if raw_index % stride == 0:
                frames.append(
                    DecodedFrame(
                        index=len(frames),
                        payload=image.tobytes(),
                        width=int(image.shape[1]),
                        height=int(image.shape[0]),
                        pts_ms=int(len(frames) * (1000.0 / max(fps, 1e-6))),
                    )
                )
            raw_index += 1
    finally:
        capture.release()

    if not frames:
        raise DecodeUnavailableError(f"'{path.name}' contained no decodable video frames")

    return frames, MediaProbe(
        frame_count=len(frames),
        width=frames[0].width,
        height=frames[0].height,
        fps=fps / stride,
        duration_ms=len(frames) * (1000.0 / max(fps / stride, 1e-6)),
        backend="opencv",
    )


def _decode_images(paths: list[Path], *, max_frames: int, stride: int):
    """Frame-folder source. PNG/JPEG need a codec; BMP and PPM do not.

    BMP and PPM are handled by hand precisely so that the frame-folder source
    works with **zero** optional dependencies — it is the reference input for
    deterministic replay, and a reference path that needs a wheel to work is not
    much of a reference.
    """
    cv2 = _try_cv2()
    frames: list[DecodedFrame] = []
    for path in paths[: max_frames * stride : stride]:
        suffix = path.suffix.lower()
        if suffix == ".ppm":
            frame = _read_ppm(path, len(frames))
        elif suffix == ".bmp":
            frame = _read_bmp(path, len(frames))
        elif cv2 is not None:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            frame = DecodedFrame(
                index=len(frames),
                payload=image.tobytes(),
                width=int(image.shape[1]),
                height=int(image.shape[0]),
                pts_ms=len(frames) * 40,
            )
        else:
            raise DecodeUnavailableError(
                f"'{path.name}' needs an image codec; install the 'cv' extra, or use "
                f"BMP/PPM which decode without one"
            )
        frames.append(frame)

    if not frames:
        raise DecodeUnavailableError("no readable images in folder")

    return frames, MediaProbe(
        frame_count=len(frames),
        width=frames[0].width,
        height=frames[0].height,
        fps=25.0,
        duration_ms=len(frames) * 40.0,
        backend="raw",
    )


def _read_ppm(path: Path, index: int) -> DecodedFrame:
    """Binary P6 PPM → BGR24."""
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise DecodeUnavailableError(f"'{path.name}' is not a binary P6 PPM")
    fields: list[int] = []
    offset = 2
    while len(fields) < 3:
        while offset < len(data) and data[offset : offset + 1].isspace():
            offset += 1
        if data[offset : offset + 1] == b"#":
            while offset < len(data) and data[offset] != 0x0A:
                offset += 1
            continue
        start = offset
        while offset < len(data) and not data[offset : offset + 1].isspace():
            offset += 1
        fields.append(int(data[start:offset]))
    offset += 1
    width, height, _maxval = fields
    rgb = data[offset : offset + width * height * 3]
    bgr = bytearray(len(rgb))
    bgr[0::3] = rgb[2::3]
    bgr[1::3] = rgb[1::3]
    bgr[2::3] = rgb[0::3]
    return DecodedFrame(index, bytes(bgr), width, height, index * 40)


def _read_bmp(path: Path, index: int) -> DecodedFrame:
    """Uncompressed 24-bit BMP → BGR24 (top-down)."""
    data = path.read_bytes()
    if not data.startswith(b"BM"):
        raise DecodeUnavailableError(f"'{path.name}' is not a BMP")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    width = struct.unpack_from("<i", data, 18)[0]
    height = struct.unpack_from("<i", data, 22)[0]
    bits = struct.unpack_from("<H", data, 28)[0]
    if bits != 24:
        raise DecodeUnavailableError(f"'{path.name}' is {bits}-bit; only 24-bit BMP is read directly")
    flip = height > 0
    height = abs(height)
    row_bytes = width * 3
    padded = (row_bytes + 3) & ~3
    rows = []
    for y in range(height):
        start = pixel_offset + y * padded
        rows.append(data[start : start + row_bytes])
    if flip:
        rows.reverse()
    return DecodedFrame(index, b"".join(rows), width, height, index * 40)


def _decode_raw(path: Path, *, max_frames: int):
    """A `.raw` sidecar: `<width>x<height>x<count>.raw`, packed BGR24.

    The dependency-free interchange format for deterministic replay fixtures.
    """
    stem = path.stem
    try:
        width_s, height_s, count_s = stem.split("x")[:3]
        width, height, count = int(width_s), int(height_s), int(count_s)
    except (ValueError, IndexError) as exc:
        raise DecodeUnavailableError(
            f"'{path.name}' must be named <width>x<height>x<count>.raw"
        ) from exc

    data = path.read_bytes()
    stride = width * height * 3
    frames = [
        DecodedFrame(i, data[i * stride : (i + 1) * stride], width, height, i * 40)
        for i in range(min(count, max_frames))
        if len(data) >= (i + 1) * stride
    ]
    if not frames:
        raise DecodeUnavailableError(f"'{path.name}' held no complete frames")
    return frames, MediaProbe(
        frame_count=len(frames),
        width=width,
        height=height,
        fps=25.0,
        duration_ms=len(frames) * 40.0,
        backend="raw",
    )


def synthetic_frames(
    *, count: int = 120, width: int = 96, height: int = 96, seed: int = 7
) -> tuple[list[DecodedFrame], MediaProbe]:
    """A deterministic moving-target sequence. No dependencies, no I/O.

    Exists so the whole console — every panel, every report, every test — is
    exercisable on a machine with no codec library and no CCTV footage. The
    motion is a linear ramp rather than anything random, so two runs produce
    byte-identical frames and V13 can actually be asserted.
    """
    frames: list[DecodedFrame] = []
    stride = width * height * 3
    for index in range(count):
        buffer = bytearray(stride)
        background = (index * 3 + seed) % 64
        for i in range(0, stride, 3):
            buffer[i] = background
            buffer[i + 1] = background
            buffer[i + 2] = background

        box_w, box_h = width // 4, height // 3
        x0 = int((width - box_w) * (index % max(count, 1)) / max(count - 1, 1))
        y0 = height // 3
        for y in range(y0, min(y0 + box_h, height)):
            row = y * width * 3
            for x in range(x0, min(x0 + box_w, width)):
                offset = row + x * 3
                buffer[offset] = 40
                buffer[offset + 1] = 200
                buffer[offset + 2] = 220
        frames.append(DecodedFrame(index, bytes(buffer), width, height, index * 40))

    return frames, MediaProbe(
        frame_count=count,
        width=width,
        height=height,
        fps=25.0,
        duration_ms=count * 40.0,
        backend="synthetic",
    )
