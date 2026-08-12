"""Test-wide configuration for the harness suite.

### Why these tests bind the scripted detector

The suite drives `synthetic_frames(width=64, height=64)` — generated gradients,
not photographs. A real detector looks at them and correctly finds nothing,
because there is nothing there. Every assertion downstream of detection then
fails for the right reason and the wrong cause: "no object reached the consumer"
is true, and says nothing about whether tracking, cropping, understanding,
synthesis or the Observation API work.

So the fixtures ask for deterministic synthetic detections explicitly. That is
the scripted detector's remaining purpose, and its only one: it makes the
*pipeline* testable on any machine, with no weights, no GPU and no network,
which is what lets the letterbox inverse and the seam wiring be exercised in CI.

**It is not the default anywhere else.** `VISION_DETECTOR_PROVIDER` defaults to
`yolo`, and `tests/vision_os/detection/test_onnx_detector_binding.py` fails the
build if that ever changes. A demo or a deployment running on a fixed box would
show real machinery operating on a fiction, and nothing in the pipeline could
tell — which is precisely why the default had to move and why this override is
written down rather than assumed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: The generic appearance policy, shipped as data. The suite needs *a* semantic
#: use case to exercise the attention path — crop, understanding, attribute,
#: observation — and without one the platform correctly raises no demand and
#: calls no model, which would leave those layers untested rather than proven.
#:
#: Which policy is immaterial; that one is required is the point.
POLICY = (
    Path(__file__).resolve().parents[3]
    / "backend"
    / "config"
    / "policies"
    / "appearance.json"
)


@pytest.fixture(autouse=True, scope="session")
def _deterministic_stack() -> None:
    """Pin the detector and the policy for the whole suite.

    Session-scoped and autouse: every test here builds a stack, and a stack that
    silently used a different detector or vocabulary than its neighbours would
    make failures depend on execution order.
    """
    os.environ["VISION_DETECTOR_PROVIDER"] = "reference"
    os.environ["VISION_SEMANTIC_POLICY"] = str(POLICY)
