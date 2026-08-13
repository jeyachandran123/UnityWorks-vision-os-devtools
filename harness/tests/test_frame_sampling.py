"""A video is sampled by rate across its whole length, never capped by count.

### The bug these tests exist to prevent

The console asked for `max_frames: 120` and the harness honoured it by truncating
to the **first** 120 decoded frames. Decoding took every source frame, so a 30 fps
clip was cut at four seconds — and nothing said so. The timeline simply ended,
which looks exactly like a video that finished.

The property that replaces it is a rate: two frames per second across the entire
duration. The number of analysis frames is therefore a *consequence* of how long
the video is, and the tests below assert that consequence at six durations, the
last of which produces 240 frames precisely because 120 must no longer be a
ceiling anywhere.

### Why these use real encoded video

The sampling stride is derived from the frame rate a container reports, which is
knowable only by opening it. A test over hand-built frame lists would assert the
arithmetic and miss the thing that actually broke — and would have missed that
the reported clip is 30 fps rather than the 24 everyone assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vosvc_harness.media import MediaLibrary
from vosvc_harness.sources.decoding import (
    DEFAULT_SAMPLE_FPS,
    MAX_SAMPLED_FRAMES,
    VideoReader,
    sampling_stride,
)

av = pytest.importorskip("av", reason="encoding a fixture video needs PyAV")
np = pytest.importorskip("numpy")

SOURCE_FPS = 24
SIZE = 32


def write_video(path: Path, *, seconds: float, fps: int = SOURCE_FPS) -> Path:
    """A real container of a known length. Content is irrelevant; timing is not."""
    count = int(round(seconds * fps))
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = SIZE, SIZE, "yuv420p"
        for index in range(count):
            image = np.full((SIZE, SIZE, 3), index % 255, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


# --- the requirement, at six durations ------------------------------------------- #


@pytest.mark.parametrize(
    ("seconds", "expected_frames"),
    [
        (5, 10),
        (10, 20),
        (13, 26),
        (30, 60),
        (60, 120),
        # The one that matters most. 240 > 120, so a passing assertion here is
        # proof that 120 is not a maximum anywhere in the path.
        (120, 240),
    ],
)
def test_sampled_frame_count_follows_video_duration(
    tmp_path: Path, seconds: int, expected_frames: int
) -> None:
    path = write_video(tmp_path / f"{seconds}s.mp4", seconds=seconds)
    reader = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS)

    # One frame of slack: an encoder may land a frame either side of the last
    # sampling instant. Ten frames of slack would let the old bug through.
    assert abs(len(reader.frames) - expected_frames) <= 1, (
        f"{seconds}s at {SOURCE_FPS} fps should sample about {expected_frames} frames, "
        f"got {len(reader.frames)}"
    )


def test_the_reported_clip_samples_its_whole_length(tmp_path: Path) -> None:
    """The exact shape of the clip that showed a pen and stopped early.

    30 fps rather than 24, which is worth pinning: the video was assumed to be
    24 fps and is not, and a stride hardcoded for 24 would sample it at 2.5 fps.
    The stride is read from the container, so the rate holds either way.
    """
    path = write_video(tmp_path / "reported.mp4", seconds=13.83, fps=30)
    reader = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS)

    assert reader.probe.source_frame_count == 415
    assert reader.probe.source_fps == pytest.approx(30.0, abs=0.1)
    assert len(reader.frames) == 28
    assert reader.frames[0].pts_ms == 0
    # Reaches ~13.5s. The failure put the last analysis frame at about 4 seconds.
    assert reader.frames[-1].pts_ms >= 13_000
    assert reader.frames[-1].pts_ms > 4_000


# --- rate, not count -------------------------------------------------------------- #


def test_sampling_holds_at_two_frames_per_second(tmp_path: Path) -> None:
    path = write_video(tmp_path / "rate.mp4", seconds=30)
    reader = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS)

    assert reader.probe.fps == pytest.approx(DEFAULT_SAMPLE_FPS, abs=0.05)
    assert reader.probe.source_fps == pytest.approx(SOURCE_FPS, abs=0.05)


def test_the_stride_is_computed_from_the_source_rate() -> None:
    assert sampling_stride(24.0, 2.0) == 12
    assert sampling_stride(30.0, 2.0) == 15
    assert sampling_stride(60.0, 2.0) == 30
    # 29.97 rounds to 15 (1.998 fps) rather than truncating to 14 (2.14 fps).
    assert sampling_stride(29.97, 2.0) == 15
    # A source slower than the sampling rate is taken whole rather than upsampled.
    assert sampling_stride(1.0, 2.0) == 1
    # No rate asked for, or none knowable: every frame, exactly as before.
    assert sampling_stride(24.0, None) == 1
    assert sampling_stride(0.0, 2.0) == 1


def test_sampling_rate_is_configurable_rather_than_fixed(tmp_path: Path) -> None:
    path = write_video(tmp_path / "cfg.mp4", seconds=10)
    assert abs(len(VideoReader(path, sample_fps=1.0).frames) - 10) <= 1
    assert abs(len(VideoReader(path, sample_fps=4.0).frames) - 40) <= 1


# --- timestamps ------------------------------------------------------------------- #


def test_every_sampled_frame_keeps_its_true_source_timestamp(tmp_path: Path) -> None:
    """0.0s, 0.5s, 1.0s ... measured from the recording, not from the sample index.

    The ordinal was used here before, so each sampled frame claimed to be one
    *source* frame period after the last and a 30-second video reported itself
    as 2.5 seconds long.
    """
    path = write_video(tmp_path / "stamps.mp4", seconds=10)
    frames = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS).frames

    assert [frame.pts_ms for frame in frames[:6]] == [0, 500, 1000, 1500, 2000, 2500]
    for position, frame in enumerate(frames):
        assert frame.pts_ms == pytest.approx(position * 500, abs=21)


def test_timestamps_are_monotonic_and_distinct(tmp_path: Path) -> None:
    path = write_video(tmp_path / "monotonic.mp4", seconds=60, fps=30)
    frames = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS).frames

    stamps = [frame.pts_ms for frame in frames]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_frame_numbering_runs_past_120_without_resetting(tmp_path: Path) -> None:
    path = write_video(tmp_path / "numbering.mp4", seconds=120)
    frames = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS).frames

    assert [frame.index for frame in frames] == list(range(len(frames)))
    assert frames[-1].index >= 239


def test_processing_reaches_the_end_of_the_video(tmp_path: Path) -> None:
    for seconds in (5, 30, 120):
        path = write_video(tmp_path / f"end-{seconds}.mp4", seconds=seconds)
        reader = VideoReader(path, sample_fps=DEFAULT_SAMPLE_FPS)
        last_ms = reader.frames[-1].pts_ms
        # The final sample sits within one sampling interval of the last frame.
        assert last_ms >= (seconds * 1000) - 600, f"{seconds}s video stopped at {last_ms}ms"
        assert reader.probe.duration_ms == pytest.approx(seconds * 1000, rel=0.02)
        assert reader.probe.truncated is False


# --- the ceiling is a memory bound, and it announces itself ------------------------ #


def test_the_memory_ceiling_is_a_bound_not_a_duration_limit(tmp_path: Path) -> None:
    # 30 minutes at 2 fps. Every duration in the requirement fits well inside it.
    assert MAX_SAMPLED_FRAMES == 3600
    assert MAX_SAMPLED_FRAMES / DEFAULT_SAMPLE_FPS >= 20 * 60

    path = write_video(tmp_path / "capped.mp4", seconds=30)
    reader = VideoReader(path, max_frames=8, sample_fps=DEFAULT_SAMPLE_FPS)
    assert len(reader.frames) == 8
    # A shortened video that claims to be whole is the failure being removed.
    assert reader.probe.truncated is True


# --- through the media library, the way a session gets its frames ------------------ #


def test_a_registered_asset_yields_the_whole_sampled_video(tmp_path: Path) -> None:
    library = MediaLibrary(tmp_path / "media")
    path = write_video(tmp_path / "asset.mp4", seconds=60)
    asset = library.register_file(path)

    frames = asset.frames()
    assert abs(len(frames) - 120) <= 1
    assert asset.sample_fps == DEFAULT_SAMPLE_FPS

    wire = asset.to_wire()
    assert wire["probe"]["frame_count"] == len(frames)
    assert wire["probe"]["source_frame_count"] == 60 * SOURCE_FPS
    assert wire["probe"]["truncated"] is False
    # Both numbers are reported: what was analysed, and what the file holds.
    assert wire["probe"]["fps"] == pytest.approx(2.0, abs=0.05)
    assert wire["probe"]["source_fps"] == pytest.approx(24.0, abs=0.05)


# --- no count-shaped limit survives anywhere in the path --------------------------- #


def test_no_frame_count_cap_is_hardcoded_on_the_session_path() -> None:
    """A source scan, because this bug was a literal in a call site.

    The limit was not in the decoder or the pipeline — it was `max_frames: 120`
    in the console's Start button, somewhere nobody looks when a video stops
    early. A test that only exercised the decoder would have passed throughout.
    """
    root = Path(__file__).resolve().parents[3]
    watched = [
        root / "vision_os_demo" / "src" / "layout" / "Shell.tsx",
        root / "vision_os_validation_console" / "harness" / "vosvc_harness" / "media.py",
        root
        / "vision_os_validation_console"
        / "harness"
        / "vosvc_harness"
        / "sources"
        / "decoding.py",
    ]
    offenders: list[str] = []
    for file in watched:
        if not file.exists():
            continue
        text = file.read_text(encoding="utf-8")
        # Comments explain the removed limit by name, so they are stripped first.
        code = re.sub(r"/\*[\s\S]*?\*/", "", text)
        code = re.sub(r"^\s*(//|#).*$", "", code, flags=re.MULTILINE)
        for match in re.finditer(r"max_frames\s*[:=]\s*(\d+)", code):
            if int(match.group(1)) not in (0, MAX_SAMPLED_FRAMES):
                offenders.append(f"{file.name}: {match.group(0)}")
        if re.search(r"frames\s*\[\s*:\s*120\s*\]|range\s*\(\s*120\s*\)", code):
            offenders.append(f"{file.name}: a literal 120-frame slice")

    assert offenders == [], f"a fixed frame count is back: {offenders}"
