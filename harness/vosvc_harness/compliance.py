"""The compliance layer, attached to a session.

**This module is composition, not logic.** It loads a rule set, holds an
evaluator, and calls it with what the Observation API returned. Every judgment
lives in `app.compliance`, which this file imports and does not modify.

Three properties are load-bearing and each is visible in the code below:

**The dependency runs one way.** This module imports `app.compliance` and
`app.vision_os`; neither imports it. Vision OS does not know compliance exists,
which is what keeps the platform free of a business opinion.

**Scope is narrowed, never post-filtered.** The reader queries the scope the
authorizer returned. 12_SECURITY §4.2 designs the leak out by constructing every
query already scoped, and a rule engine sitting outside the platform could
quietly reintroduce it by asking broadly and filtering afterwards.

**Evaluation reads; it never writes.** There is no path from here back into
Vision State, because `ObservationReader` exposes none.

### Why findings are computed on read rather than accumulated on write

A finding is a pure function of (rule set, observation, now). Recomputing it from
current state is therefore always correct and needs no invalidation, while a
cache of findings would need to be invalidated every time an attribute changed —
and a stale violation on screen is worse than a slow one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a deployment names its rule document. The same name `app.compliance`
#: already uses — this module adds no environment variable of its own.
RULES_ENV = "COMPLIANCE_RULES"


@dataclass(frozen=True, slots=True)
class ComplianceStatus:
    """Whether this session can evaluate rules, and why not when it cannot.

    Reported rather than inferred. A console showing "0 violations" because no
    rule set was loaded would be making the same mistake the platform's coverage
    contract exists to prevent: an empty answer that looks like a clean result.
    """

    enabled: bool
    reason: str = ""
    ruleset_version: str = ""
    rule_count: int = 0
    rules: tuple[str, ...] = ()
    required_attributes: tuple[str, ...] = ()
    capability_gaps: tuple[tuple[str, str], ...] = ()
    """`(rule_id, attribute)` for every rule depending on something no bound
    model can produce. Answered at load, so a deployment learns immediately that
    a rule can never reach a verdict here."""

    def to_wire(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "ruleset_version": self.ruleset_version,
            "rule_count": self.rule_count,
            "rules": list(self.rules),
            "required_attributes": list(self.required_attributes),
            "capability_gaps": [
                {"rule_id": r, "attribute": a} for r, a in self.capability_gaps
            ],
        }


class ComplianceLayer:
    """One rule set and one evaluator, bound to one session's API.

    Constructed per session because the API is per session; the rule set itself
    is deployment configuration and is loaded once per construction.
    """

    __slots__ = ("_evaluator", "_reader", "_rules", "_status", "_tenant")

    def __init__(self, *, api: Any, principal: Any, rules_path: str | Path | None = None):
        from app.compliance import ComplianceEvaluator, ObservationReader, RuleSet

        self._reader = ObservationReader(api, principal=principal)
        self._tenant = principal.tenant_id

        chosen = str(rules_path or os.environ.get(RULES_ENV, "")).strip()
        if not chosen:
            self._rules = RuleSet()
            self._evaluator = ComplianceEvaluator(self._rules)
            self._status = ComplianceStatus(
                enabled=False,
                reason=(
                    f"no rule set configured; set {RULES_ENV} to a rule document "
                    f"to enable compliance evaluation"
                ),
            )
            return

        try:
            self._rules = RuleSet.from_file(chosen)
        except Exception as exc:  # noqa: BLE001 - the failure is the answer
            self._rules = RuleSet()
            self._evaluator = ComplianceEvaluator(self._rules)
            self._status = ComplianceStatus(
                enabled=False,
                reason=f"rule set '{chosen}' failed to load: {type(exc).__name__}: {exc}",
            )
            return

        self._evaluator = ComplianceEvaluator(self._rules)
        self._status = ComplianceStatus(
            enabled=bool(self._rules.rules),
            reason="" if self._rules.rules else f"rule set '{chosen}' declares no rules",
            ruleset_version=self._rules.version,
            rule_count=len(self._rules.rules),
            rules=tuple(r.pinned for r in self._rules.rules),
            required_attributes=tuple(self._rules.required_attributes),
        )

    @property
    def status(self) -> ComplianceStatus:
        return self._status

    def describe(self, producible: tuple[str, ...] = ()) -> ComplianceStatus:
        """Status with capability gaps resolved against what is actually bound.

        Separate from `status` because the producible set is a property of the
        assembled stack, not of the rule document, and the layer is constructed
        before that is known.
        """
        if not self._status.enabled or not producible:
            return self._status
        from dataclasses import replace

        return replace(
            self._status,
            capability_gaps=self._rules.unproducible_against(producible),
        )

    def evaluate(self, *, now: Any, limit: int = 200) -> dict[str, Any]:
        """Read current state and evaluate every rule against every subject.

        `now` is passed in rather than read from a clock, because the platform
        runs on a virtual clock during replay and a finding stamped with wall
        time would be unreproducible — and because determinism is the evaluator's
        whole contract.
        """
        from app.vision_os.core.model.api import Scope

        if not self._status.enabled:
            return {
                "available": False,
                "reason": self._status.reason,
                "findings": [],
                "summary": _empty_summary(),
            }

        snapshot = self._reader.read(Scope(tenant_id=self._tenant), limit=limit)

        # Stable display labels, assigned by position in a stable ordering.
        # Presentation only: no evaluation reads them, and none is derived from
        # anything the platform observed about a person.
        labels = {
            str(view.object_id): f"{_title(view.class_id)} #{index + 1}"
            for index, view in enumerate(
                sorted(snapshot.objects, key=lambda v: str(v.object_id))
            )
        }

        findings = self._evaluator.evaluate(
            snapshot.objects,
            now=now,
            coverage=snapshot.coverage,
            capability_gaps=snapshot.capability_gaps,
            labels=labels,
        )

        return {
            "available": True,
            "reason": "",
            "evaluated_at_ns": int(getattr(now, "ns", 0)),
            "ruleset_version": self._status.ruleset_version,
            "subjects_read": len(snapshot.objects),
            "coverage": {
                "observable_fraction": snapshot.coverage.observable_fraction,
                "cameras_observing": snapshot.coverage.cameras_observing,
                "cameras_blind": snapshot.coverage.cameras_blind,
                "complete": snapshot.complete,
            },
            "findings": [_finding_to_wire(f) for f in findings],
            "summary": _summarize(findings),
        }


def _title(class_id: Any) -> str:
    """`person` → `Person`. A display nicety over an id, nothing more."""
    return str(class_id).replace("_", " ").replace(".", " ").title()


def _empty_summary() -> dict[str, int]:
    return {
        "total": 0,
        "compliant": 0,
        "violation": 0,
        "unknown": 0,
        "not_applicable": 0,
    }


def _summarize(findings) -> dict[str, int]:
    counts = _empty_summary()
    counts["total"] = len(findings)
    for finding in findings:
        counts[finding.state.value] = counts.get(finding.state.value, 0) + 1
    return counts


def _finding_to_wire(finding) -> dict[str, Any]:
    """One finding, flattened for transport.

    Carries evidence **references** and never imagery: resolving one requires the
    separate evidence privilege and a declared purpose, which is the UI's request
    to make under its own authorization, not this layer's to pre-empt.
    """
    return {
        "finding_id": finding.finding_id,
        "rule_id": finding.rule_id,
        "rule_version": finding.rule_version,
        "ruleset_version": finding.ruleset_version,
        "state": finding.state.value,
        "severity": finding.severity,
        "sentence": finding.describe(),
        "evaluated_at_ns": finding.evaluated_at.ns,
        "coverage_fraction": finding.coverage_fraction,
        "subject": {
            "object_id": str(finding.subject.object_id),
            "class_id": str(finding.subject.class_id),
            "camera_id": str(finding.subject.camera_id),
            "label": finding.subject.label,
        },
        "unknown_reasons": [r.value for r in finding.unknown_reasons],
        "evidence_refs": list(finding.evidence_refs),
        "conditions": [
            {
                "attribute_key": c.attribute_key,
                "operator": c.operator,
                "expected": c.expected,
                "observed": c.observed,
                "satisfied": c.satisfied,
                "unknown_reason": c.unknown_reason.value if c.unknown_reason else None,
                "observed_at_ns": c.observed_at.ns if c.observed_at else None,
                "evidence_ref": c.evidence_ref,
                "message": c.message,
            }
            for c in finding.conditions
        ],
    }


__all__ = ["RULES_ENV", "ComplianceLayer", "ComplianceStatus"]
