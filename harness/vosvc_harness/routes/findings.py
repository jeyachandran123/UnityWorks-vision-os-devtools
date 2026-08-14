"""Compliance findings — read-only, evaluated on demand.

Two endpoints and no writes. `GET /compliance/status` says whether rules are
loaded and which attributes they depend on; `POST /compliance/evaluate` runs
them against current Vision State and returns structured findings.

**Evaluation happens here, not in the browser.** The UI receives findings and
renders them; it never sees a rule, never compares a value, and never decides
whether something is compliant. That boundary is the point of the whole design:
a verdict a UI computed is a verdict nobody can audit, because the reasoning
lived in a bundle that has since been redeployed.

**Findings are recomputed, not accumulated.** A finding is a pure function of
(rule set, observation, now), so recomputing from current state is always correct
and needs no invalidation. A cached finding would need invalidating on every
attribute change, and a stale violation on screen is worse than a slow one.

Evidence travels as a reference. Resolving one goes through the existing
`GET /evidence/{ref}` path, under its own privilege and with a declared purpose —
this route neither embeds imagery nor weakens the gate in front of it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Body, Query

from ..contract import encode


def register(app, harness) -> None:
    def _session(session_id: str | None):
        from .observation_api import _resolve

        session = _resolve(harness, session_id)
        if session is None or session.stack is None:
            return None
        return session

    def _unavailable(reason: str) -> dict[str, Any]:
        """No platform behind the question — stated, never answered as empty.

        A consumer receiving `findings: []` cannot tell "nothing is wrong" from
        "nothing was evaluated", and those are opposite facts (V8).
        """
        return {
            "code": "NOT_FOUND",
            "message": reason,
            "retryable": False,
            "details": {"hint": "open a session first: POST /api/v1/sessions"},
            "request_id": "",
        }

    @app.get("/api/v1/compliance/status")
    async def compliance_status(session_id: str | None = Query(default=None)) -> Any:
        """Whether this session can evaluate rules, and what they depend on."""
        session = _session(session_id)
        if session is None:
            return _unavailable("no booted session to report compliance for")
        if session.compliance is None:
            return {
                "enabled": False,
                "reason": "the compliance layer failed to attach to this session",
                "rule_count": 0,
                "rules": [],
                "required_attributes": [],
                "capability_gaps": [],
            }

        # What the platform can actually produce right now, so a rule depending
        # on something unproducible is reported as a capability gap instead of
        # returning UNKNOWN forever with no explanation.
        producible = tuple(
            str(key)
            for policy in session.stack.policies
            for key in policy.attribute_keys
        )
        return encode(session.compliance.describe(producible).to_wire())

    @app.post("/api/v1/compliance/evaluate")
    async def compliance_evaluate(payload: dict = Body(default_factory=dict)) -> Any:
        """Evaluate every rule against current state. **Reads only.**

        `now` comes from the session's own clock, which is virtual during
        replay. Using wall time here would stamp findings with an instant the
        pipeline never saw and make a replay irreproducible — the determinism the
        evaluator guarantees is only worth having if its caller preserves it.
        """
        session = _session(payload.get("session_id"))
        if session is None:
            return _unavailable("no booted session to evaluate compliance against")
        if session.compliance is None:
            return {
                "available": False,
                "reason": "the compliance layer failed to attach to this session",
                "findings": [],
                "summary": {
                    "total": 0,
                    "compliant": 0,
                    "violation": 0,
                    "unknown": 0,
                    "not_applicable": 0,
                },
            }

        now = session.stack.clock.now()
        limit = int(payload.get("limit", 200))
        return encode(session.compliance.evaluate(now=now, limit=limit))
