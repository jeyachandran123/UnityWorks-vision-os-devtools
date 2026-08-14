"""Demo defaults, applied in the process rather than only in a launch script.

Every setting here has always been configurable; none of it was ever *set*
unless an operator exported it or used `start-platform.ps1`. Running
`python -m vosvc_harness` directly — which is what the README shows, what an IDE
run configuration does, and what happens when the script is bypassed — produced a
platform with no semantic policy, no rules, and no imagery. The console then
reported that faithfully: *"no semantic policy configured"*, *"the picture is not
being shown"*, and no crops anywhere, because with no demand none were ever
taken.

That is the platform behaving correctly and the tool being unhelpfully bare. So
the defaults live here now, next to the code that needs them.

**`setdefault`, never assignment.** An operator who exported any of these keeps
their value; this only fills silence. Setting them outright would make the
console impossible to configure, which is the opposite problem.

### On the two imagery flags

`VOSVC_SERVE_FRAMES` and `VOSVC_ALLOW_EVIDENCE` default to **off** in
`HarnessConfig`, and that default is right for a library: 12_SECURITY §5.3 keeps
reading what a camera reported separate from looking at the picture, and a
platform should not hand out imagery because nobody said not to.

This module is not the library. It is the launcher for a **local engineering
console** whose entire job is showing an engineer what the platform saw, on their
own machine, against footage they supplied. Withholding the pictures there
defeats the tool. Both remain a single environment variable away from off, and
the purpose prompt still stands in front of every image.
"""

from __future__ import annotations

import os
from pathlib import Path

#: `.../atlas`, the workspace holding both this repository and `backend/`.
#: Resolved the same way `config.py` resolves it, so the two cannot disagree.
_WORKSPACE = Path(__file__).resolve().parents[3]

#: Where the shipped example documents live.
_CONFIG = _WORKSPACE / "backend" / "config"

#: The demo's use case, as documents. Not one attribute, value, class or business
#: rule appears in this file — only the paths of the files that hold them.
_POLICIES = (
    _CONFIG / "policies" / "kitchen-safety.example.json",
    _CONFIG / "policies" / "object-identity.example.json",
)
_VERIFICATION = _CONFIG / "policies" / "verification.example.json"
_RULES = _CONFIG / "rules" / "site-safety.example.json"


#: What the last call actually filled in, for the banner and `/health`.
#:
#: A console that configured itself silently would be as confusing as one that
#: configured nothing — an operator debugging a session needs to know whether a
#: setting came from their shell or from here.
APPLIED: dict[str, str] = {}


def apply_demo_defaults(env: dict[str, str] | None = None) -> dict[str, str]:
    """Fill in anything the operator did not set. Returns what this call added.

    Returned rather than silent so the banner can report which settings came from
    here and which came from the environment — a console that quietly configured
    itself would be as confusing as one that configured nothing.

    A document that is not on disk is skipped. A missing example file must not
    stop the harness booting: the platform runs perfectly well without a policy,
    and it says so.
    """
    target = os.environ if env is None else env
    applied: dict[str, str] = {}

    def default(name: str, value: str) -> None:
        if not target.get(name):
            target[name] = value
            applied[name] = value

    present = [str(path) for path in _POLICIES if path.is_file()]
    if present:
        default("VISION_SEMANTIC_POLICY", ",".join(present))

    if _VERIFICATION.is_file():
        default("VISION_VERIFICATION_RULES", str(_VERIFICATION))

    if _RULES.is_file():
        default("COMPLIANCE_RULES", str(_RULES))

    # A real model. The composition root refuses to fall back to the constant
    # head when one of these is named, so a broken provider fails the session
    # loudly instead of answering with fixed values a compliance rule would then
    # report violations from.
    default("VISION_UNDERSTANDER_PROVIDER", "nvidia")

    # The pictures. See the module docstring for why a local engineering console
    # differs from the library default here.
    default("VOSVC_SERVE_FRAMES", "1")
    default("VOSVC_ALLOW_EVIDENCE", "1")

    if env is None:
        APPLIED.update(applied)
    return applied


__all__ = ["apply_demo_defaults"]
