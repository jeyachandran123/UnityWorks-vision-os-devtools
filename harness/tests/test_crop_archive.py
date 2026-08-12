"""The crop archive keeps both halves of M8's accounting.

A crop tells you what was examined. The **skips** tell you what was not, and
why — and without them a viewer cannot draw a complete frame. An object with no
crop would render as a blank space indistinguishable from a bug, when in fact it
is the platform working exactly as designed: M8 crops what a demand asked for,
and cropping everything is the cost model the architecture exists to avoid.

These tests use plain stand-ins rather than importing Vision OS. The sink is
duck-typed by the runtime that calls it, so the contract under test is the shape
of the object handed over, and a stand-in states that shape in one screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from vosvc_harness.crops import MAX_SKIPS, CropArchive


@dataclass(frozen=True)
class _Reason:
    """Stands in for `SkipReason`, which is an enum with a `.value`."""

    value: str


@dataclass(frozen=True)
class _Skip:
    object_id: str
    reason: _Reason
    detail: str = ""


@dataclass(frozen=True)
class _FrameRef:
    frame_seq: int


@dataclass(frozen=True)
class _Evaluation:
    frame_ref: _FrameRef | None = None
    requests: tuple = ()
    skipped: tuple[_Skip, ...] = field(default=())


@pytest.fixture()
def archive(tmp_path: Path) -> CropArchive:
    return CropArchive(tmp_path, session_id="cam-test")


def test_records_the_reason_a_candidate_was_not_cropped(archive: CropArchive) -> None:
    archive.accept(
        _Evaluation(
            frame_ref=_FrameRef(83),
            skipped=(_Skip("tie-1", _Reason("no_demand"), "no demand covers class tie"),),
        ),
        (),
    )

    index = archive.index()
    assert index["skips_by_object"]["tie-1"] == [
        {"frame_seq": 83, "reason": "no_demand", "detail": "no demand covers class tie"}
    ]
    assert index["skipped"] == 1


def test_the_skip_is_scoped_to_the_frame_it_happened_on(archive: CropArchive) -> None:
    for seq in (83, 84):
        archive.accept(
            _Evaluation(frame_ref=_FrameRef(seq), skipped=(_Skip("tie-1", _Reason("no_demand")),)),
            (),
        )

    frames = [entry["frame_seq"] for entry in archive.index()["skips_by_object"]["tie-1"]]
    assert frames == [83, 84]


def test_the_same_skip_repeated_on_one_frame_is_recorded_once(archive: CropArchive) -> None:
    evaluation = _Evaluation(
        frame_ref=_FrameRef(83), skipped=(_Skip("tie-1", _Reason("no_demand")),)
    )
    archive.accept(evaluation, ())
    archive.accept(evaluation, ())

    assert len(archive.index()["skips_by_object"]["tie-1"]) == 1


def test_an_evaluation_with_no_skips_records_nothing(archive: CropArchive) -> None:
    archive.accept(_Evaluation(frame_ref=_FrameRef(83)), ())
    assert archive.index()["skips_by_object"] == {}


def test_a_missing_frame_reference_is_kept_rather_than_invented(archive: CropArchive) -> None:
    # A skip with no frame is still a fact about that object. Dropping it would
    # make the account of the frame look complete when it is not.
    archive.accept(_Evaluation(skipped=(_Skip("tie-1", _Reason("no_demand")),)), ())
    assert archive.index()["skips_by_object"]["tie-1"][0]["frame_seq"] is None


def test_an_unrecognised_reason_survives_verbatim(archive: CropArchive) -> None:
    # `SkipReason` is closed today and the platform may extend it. A future value
    # must reach the viewer as itself, not as a repr and not as "unknown".
    archive.accept(
        _Evaluation(frame_ref=_FrameRef(1), skipped=(_Skip("obj-1", _Reason("some_future_reason")),)),
        (),
    )
    assert archive.index()["skips_by_object"]["obj-1"][0]["reason"] == "some_future_reason"


def test_a_malformed_evaluation_never_breaks_attention(archive: CropArchive) -> None:
    # 10_RELIABILITY: a failing sink is a sink problem. Attention must not stop
    # because the console's accounting choked on a shape it did not expect.
    archive.accept(object(), ())
    archive.accept(_Evaluation(frame_ref=_FrameRef(1), skipped=(object(),)), ())  # type: ignore[arg-type]
    assert archive.index()["skips_by_object"] == {}


def test_skip_records_stay_bounded(archive: CropArchive) -> None:
    for seq in range(MAX_SKIPS + 50):
        archive.accept(
            _Evaluation(frame_ref=_FrameRef(seq), skipped=(_Skip("obj-1", _Reason("no_demand")),)),
            (),
        )

    kept = archive.index()["skips_by_object"]["obj-1"]
    assert len(kept) == MAX_SKIPS
    # The oldest frames are evicted, so what remains is the recent history a
    # viewer is actually looking at.
    assert kept[-1]["frame_seq"] == MAX_SKIPS + 49


def test_disposing_a_session_drops_its_skips(archive: CropArchive) -> None:
    archive.accept(
        _Evaluation(frame_ref=_FrameRef(1), skipped=(_Skip("obj-1", _Reason("no_demand")),)), ()
    )
    archive.dispose()
    assert archive.index()["skips_by_object"] == {}
