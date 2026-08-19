"""M7 and M9 must share one AttributeRegistry instance.

The Semantic Ceiling is only canonical if there is exactly one of it. Phase 6.8
measured what happens when there is not: the composition root registered every
policy attribute into a registry handed only to Understanding, built the object
registry with a default empty one, and M7 then refused **308 of 308** attributes
M9 had successfully produced —

    AttributeRejectedError: attribute 'head_covering' is not registered

Nothing downstream could tell. The understanding layer reported zero failures,
the sink reported zero failures, and the platform silently re-asked the VLM for
an answer it already had, on every frame, forever. Freshness, staleness,
confidence refresh and quality refresh were all unreachable for the whole of
Phases 6.1-6.8 because of it.

These tests fail if the two layers are ever given separate registries again.
"""

from __future__ import annotations

import pytest

from vosvc_harness.assembly import build_stack
from vosvc_harness.sources.decoding import synthetic_frames


@pytest.fixture(scope="module")
def stack():
    import os

    os.environ["VOSVC_UNDERSTANDER"] = "static"
    return build_stack(
        frames=synthetic_frames(count=4, width=64, height=64)[0],
        camera_id="cam-registry", tenant_id="t-eng",
        site_id="s-eng", target_fps=4.0,
        semantics="archival", cursor=None, ledger=None,
    )


def _registry_of(layer):
    """The AttributeRegistry a layer validates against, via its public surface."""
    for holder in (layer, getattr(layer, "engine", None), getattr(layer, "registry", None)):
        if holder is None:
            continue
        for name in ("_attribute_registry", "attributes", "attribute_registry"):
            found = getattr(holder, name, None)
            if found is not None and hasattr(found, "require"):
                return found
    return None


class TestSharedRegistry:
    def test_m7_has_an_attribute_registry_at_all(self, stack) -> None:
        assert _registry_of(stack.registry_layer) is not None

    def test_m7_and_m9_share_the_same_instance(self, stack) -> None:
        """Identity, not equality.

        Two registries holding equal definitions would drift the moment a policy
        is reloaded on one side, and the drift would surface as an
        `AttributeRejectedError` for an attribute the operator can see declared
        in their own policy file.
        """
        m7 = _registry_of(stack.registry_layer)
        m9 = _registry_of(stack.understanding)
        if m9 is None:
            pytest.skip("understanding layer does not expose its registry")
        assert m7 is m9

    def test_m7_accepts_every_attribute_the_active_policies_declared(self, stack) -> None:
        """The regression that matters.

        Derived from the stack's own policies rather than a hard-coded key, so
        the test asserts the real invariant — *whatever this deployment
        declared, M7 accepts* — instead of a kitchen-specific string.
        """
        registry = _registry_of(stack.registry_layer)
        declared = [str(k) for policy in stack.policies for k in policy.attribute_keys]
        if not declared:
            pytest.skip("no semantic policy configured in this environment")
        for key in declared:
            registry.require(key)  # raises AttributeRejectedError if unregistered

    def test_m7_still_refuses_an_undeclared_attribute(self, stack) -> None:
        """The fix is *give M7 the right registry*, never *make M7 permissive*.

        The neutrality gate is the outermost ring of Semantic Ceiling
        enforcement; weakening it to make write-back pass would trade a silent
        re-computation defect for a silent correctness one.
        """
        registry = _registry_of(stack.registry_layer)
        with pytest.raises(Exception):
            registry.require("definitely_not_a_declared_attribute")


class TestWriteBackAudit:
    def test_the_composition_exposes_why_attributes_were_discarded(self, stack) -> None:
        """A silent `except` cost a whole diagnosis in Phase 6.7.

        The write-back sink must account for every result it drops, so that
        "nothing was stored" is always distinguishable from "everything
        succeeded".
        """
        audit = stack.writeback_audit
        assert set(audit) >= {"applied", "rejected", "no_object_id", "failed_outcome", "reasons"}

    def test_rejections_carry_their_reason(self, stack) -> None:
        assert isinstance(stack.writeback_audit["reasons"], dict)
