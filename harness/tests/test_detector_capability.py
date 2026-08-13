"""A detector must declare what kind of vocabulary it has, not just its size.

### Why this exists

A pen was reported as `toothbrush` at 0.454 confidence. Nothing upstream was
broken: YOLOv8n on COCO has no `pen`, a closed-set classification head has no
index meaning "none of these", and the nearest of its eighty words to a thin
hand-held object is `toothbrush`. The detector gave the best answer available to
it, and every layer downstream — tracking, cropping, prompts, observations, the
UI — carried that answer as though it were an identification.

The missing fact was never the class list. It was that **nothing declared the
list to be closed**. `producible_classes` says what the detector can name;
nothing said that everything it cannot name is reported as whichever of those it
resembles most.

These tests pin the declaration, and pin that the taxonomy still comes from the
model file rather than from any constant in this repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vosvc_harness.assembly import default_vision_os_root, ensure_importable

ensure_importable()

try:
    from app.vision_os.adapters.configuration.detector_providers import (
        CLOSED_SET,
        DETECTOR_FACTORIES,
        OPEN_VOCABULARY,
        DetectorConfigurationError,
        build_detector,
    )
    from app.vision_os.kernel.clock import VirtualClock

    _AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    _AVAILABLE = False
    _REASON = f"Vision OS is not importable: {type(exc).__name__}: {exc}"

requires_vision_os = pytest.mark.skipif(not _AVAILABLE, reason="Vision OS is not importable")

WEIGHTS = default_vision_os_root() / "models" / "yolov8n.onnx"
requires_weights = pytest.mark.skipif(
    not WEIGHTS.is_file(), reason=f"detector weights are not present at {WEIGHTS}"
)


@pytest.fixture()
def bound():
    """The real YOLO binding, named explicitly.

    `conftest` pins `VISION_DETECTOR_PROVIDER=reference` for the whole suite so
    the pipeline tests get deterministic synthetic detections. These tests are
    about the *shipped* detector, so they ask for it rather than inheriting
    whatever the environment happens to say — a fixture that silently tested the
    scripted detector would assert nothing about the model in production.
    """
    return build_detector(clock=VirtualClock(), provider="yolo")


# --- the declaration --------------------------------------------------------- #


@requires_vision_os
@requires_weights
def test_the_bundled_detector_declares_itself_closed_set(bound) -> None:
    """The fact whose absence let a guess read as an identification."""
    assert bound.label_space_kind == CLOSED_SET
    assert bound.is_closed_set is True
    assert "closed-set" in bound.note


@requires_vision_os
@requires_weights
def test_the_vocabulary_is_declared_in_full(bound) -> None:
    # Not a count. A consumer that wants to say "these are the 80 words it knows"
    # needs the words, and a consumer checking whether a label could have been
    # produced needs them too.
    assert len(bound.native_labels) == 80
    assert bound.native_label_space == "coco"
    assert "toothbrush" in bound.native_labels
    assert "pen" not in bound.native_labels


@requires_vision_os
@requires_weights
def test_the_capability_gap_carries_kind_and_vocabulary(bound) -> None:
    gaps = dict(bound.capability_gaps())
    assert gaps["detector.label_space"] == "closed_set:coco:80"
    vocabulary = gaps["detector.vocabulary"].split(",")
    assert len(vocabulary) == 80
    assert vocabulary[0] == "person"


@requires_vision_os
@requires_weights
def test_the_declared_vocabulary_matches_what_the_detector_can_produce(bound) -> None:
    """The two lists must not drift; a UI trusts both."""
    produced = {str(class_id) for class_id in bound.classes}
    declared = {label.replace(" ", "_") for label in bound.native_labels}
    assert produced == declared


# --- the taxonomy still comes from the model --------------------------------- #


@requires_vision_os
@requires_weights
def test_the_class_list_is_read_from_the_graph_not_from_this_repository() -> None:
    """No COCO constant anywhere in the platform or the harness.

    A copy would mislabel every object the day a model trained on something else
    is bound — the same failure as the pen, one layer further out and harder to
    see, because it would survive swapping the model.
    """
    import onnxruntime  # noqa: PLC0415

    session = onnxruntime.InferenceSession(
        str(WEIGHTS), providers=["CPUExecutionProvider"]
    )
    declared = session.get_modelmeta().custom_metadata_map.get("names", "")
    assert "toothbrush" in declared, "the vocabulary lives in the model's own metadata"

    roots = [
        default_vision_os_root() / "app" / "vision_os",
        Path(__file__).resolve().parents[1] / "vosvc_harness",
    ]
    # Two or more COCO-specific words in one file is a class list, not prose.
    cocoish = ("fire hydrant", "parking meter", "baseball glove", "hair drier", "potted plant")
    offenders: list[str] = []
    for root in roots:
        for file in root.rglob("*.py"):
            text = file.read_text(encoding="utf-8", errors="ignore")
            if sum(1 for word in cocoish if word in text) >= 2:
                offenders.append(str(file))
    assert offenders == [], f"a COCO class list is embedded in: {offenders}"


# --- the provider seam ------------------------------------------------------- #


@requires_vision_os
def test_open_vocabulary_is_a_registered_provider_that_refuses_to_be_faked() -> None:
    """The capability is declared and unimplemented, which is the honest state.

    A closed-set model standing in for open-vocabulary detection would launder a
    nearest-neighbour guess into an identification — exactly the failure the
    whole declaration exists to surface.
    """
    assert OPEN_VOCABULARY in DETECTOR_FACTORIES

    with pytest.raises(DetectorConfigurationError) as caught:
        build_detector(clock=VirtualClock(), provider=OPEN_VOCABULARY)

    message = str(caught.value)
    assert "closed-set" in message
    assert "cannot substitute" in message


@requires_vision_os
def test_an_unknown_provider_fails_at_composition_rather_than_degrading() -> None:
    with pytest.raises(DetectorConfigurationError):
        build_detector(clock=VirtualClock(), provider="no-such-detector")


@requires_vision_os
def test_swapping_the_provider_needs_no_vision_os_change() -> None:
    """Provider abstraction: the factory table is the only thing that decides.

    Every registered provider is reachable by name through one function, and the
    platform is never told which one — `build_detection_layer` takes a factory
    and calls it. Adding an open-vocabulary adapter is an entry here and an
    adapter module, and touches no Vision OS core file.
    """
    assert set(DETECTOR_FACTORIES) >= {"yolo", "reference", OPEN_VOCABULARY}
    for name in DETECTOR_FACTORIES:
        assert callable(DETECTOR_FACTORIES[name])


@requires_vision_os
def test_the_scripted_detector_also_declares_its_label_space() -> None:
    # Every provider declares, or the field is optional in practice and a
    # consumer cannot rely on it.
    scripted = build_detector(clock=VirtualClock(), provider="reference")
    assert scripted.label_space_kind == CLOSED_SET
    assert scripted.native_labels == ("person",)


# --- confidence survives ----------------------------------------------------- #


@requires_vision_os
@requires_weights
def test_the_threshold_is_configuration_not_a_constant() -> None:
    """A wrong-but-confident label cannot be tuned away, and the test says so.

    The pen scored 0.454 as `toothbrush`. Any threshold low enough to admit real
    detections admits that too, which is why the fix is declaration rather than a
    number. The knob still exists, and remains a deployment's to set.
    """
    from app.vision_os.adapters.configuration.detector_providers import CONFIDENCE_ENV

    assert CONFIDENCE_ENV == "VISION_DETECTOR_CONFIDENCE"
    strict = build_detector(
        clock=VirtualClock(),
        provider="yolo",
        env={**dict(__import__("os").environ), CONFIDENCE_ENV: "0.9"},
    )
    assert strict.label_space_kind == CLOSED_SET
