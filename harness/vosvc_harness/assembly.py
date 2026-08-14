"""Assemble a real Vision OS through its own composition roots.

**This is the only module in the repository that imports Vision OS.** Everything
it touches is a public composition root — `build_platform`, `build_*_layer`,
`assemble` — the same functions the platform's own end-to-end suite calls. The
harness constructs no Vision OS module by hand and reads no module's internals.

Two seams are assigned through a private attribute (`_sink`, `_admitted_consumer`).
That is not the harness taking liberties: those are the documented Flow 3/4 and
Flow 2 seams, and Vision OS's own integration tests assign them exactly this way,
each annotated `# noqa: SLF001 - the declared seam`. They are the composition
root's job, and here the harness *is* the composition root. Both are marked below
so a future reader can find them in one search.

Nothing here modifies Vision OS. Every file under `backend/app/vision_os/` is
read-only to this process.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sources.adapters import ReplayCursor, ReplayFileSource, RtspReplaySource, ValidationDecoder
from .crops import CropArchive
from .sources.faults import FaultLedger


#: Providers that call a real model. Naming one of these is a statement that
#: constants are not acceptable, so a failure to bind it fails the session
#: rather than degrading to the constant head (see `_select_understander`).
_REAL_PROVIDERS = frozenset({"nvidia", "ollama"})


class VisionOsUnavailableError(RuntimeError):
    """Vision OS could not be imported.

    Surfaced to the console as a capability gap with the import error attached,
    never as an empty pipeline. A console showing "0 detections" because the
    platform never loaded would be lying about the platform.
    """


def default_vision_os_root() -> Path:
    """Where Vision OS lives, absent an explicit setting.

    Resolved here rather than only in `HarnessConfig` so that `build_stack` is
    self-sufficient. A session that could only boot when some earlier caller had
    primed `sys.path` would fail in exactly one place — a unit test — and the
    failure would look like "Vision OS is missing" rather than "nobody called
    the setup function".
    """
    import os

    override = os.environ.get("VOSVC_VISION_OS_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "backend"


_resolved_root: Path | None = None


def ensure_importable(vision_os_root: Path | None = None) -> Path:
    """Put the backend on `sys.path` so `app.vision_os` resolves.

    Idempotent, and remembers the root it was given so later callers need not
    repeat it. Path manipulation rather than installation, because the harness
    must never require a change to the backend's packaging — a dependency edge
    from Vision OS's repo to this one is exactly the coupling this tool exists to
    avoid.
    """
    global _resolved_root

    root = Path(vision_os_root) if vision_os_root is not None else (
        _resolved_root or default_vision_os_root()
    )
    resolved = root.resolve()
    _resolved_root = resolved
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
    return resolved


def probe_vision_os(vision_os_root: Path | None = None) -> dict[str, Any]:
    """Report whether Vision OS is importable, and what it offers.

    Never raises. The console renders the failure rather than showing a blank
    screen, because "the harness cannot load Vision OS" and "Vision OS produced
    nothing" are different facts (V8).
    """
    root = ensure_importable(vision_os_root)
    try:
        from app.vision_os.core.model.api import API_VERSION, SUPPORTED_MAJORS
        from app.vision_os.kernel.metrics.names import ALL_METRIC_NAMES

        return {
            "available": True,
            "api_version": API_VERSION,
            "supported_majors": sorted(SUPPORTED_MAJORS),
            "metric_names": len(ALL_METRIC_NAMES),
            "root": str(root),
        }
    except Exception as exc:  # noqa: BLE001 - the failure is the answer
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "root": str(root),
        }


# --- configuration ---------------------------------------------------------- #


def build_config_document(
    *,
    camera_id: str,
    tenant_id: str,
    site_id: str,
    width: int,
    height: int,
    target_fps: float,
    semantics: str,
) -> dict:
    """A Flow 1–8 configuration document for one replay camera.

    Shaped to the platform's **closed** config schema — an unknown key is
    rejected at load, which is one of the four gates that keep business logic out
    (00_CHARTER §4). Every value here is an engineering-validation choice, and
    the ones that matter are commented.
    """
    frame_bytes = width * height * 3
    history_ms, slots = _buffer_sizing(frame_bytes=frame_bytes, target_fps=target_fps)
    return {
        "platform": {
            "deployment_profile": "embedded",
            # Virtual: nothing in the pipeline observes wall time, which is the
            # single prerequisite for deterministic replay (V13).
            "clock_mode": "virtual",
        },
        "buffer": {
            "slots_per_camera": slots,
            "bytes_per_slot": frame_bytes,
            "lease_deadline_ms": 2000,
            "history_window_ms": history_ms,
        },
        "scheduler": {"global_budget_fps": 1000.0, "drop_alarm_window_ms": 1000},
        "source": {
            "reconnect_backoff_initial_ms": 10,
            "reconnect_backoff_max_ms": 200,
            "stall_watchdog_ms": 2000,
        },
        "health": {
            "aggregation_interval_ms": 100,
            "report_timeout_ms": 1000,
            # Low so the freeze scenario actually trips the silent-failure
            # detector inside a short validation run.
            "frozen_frame_threshold": 3,
            "hysteresis_samples": 1,
        },
        "runtime": {"attach_stagger_ms": 0, "drain_timeout_ms": 2000},
        "profiles": [
            {
                "profile_id": "validation",
                "target_fps": target_fps,
                "max_in_flight": 4,
                "inference_width": 640,
                "inference_height": 640,
            }
        ],
        "regions": [
            {
                "region_id": "full-frame",
                "label": "full-frame",
                "vertices": [[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]],
                "frame_of_reference": "normalized",
            }
        ],
        "cameras": [
            {
                "camera_id": camera_id,
                "tenant_id": tenant_id,
                "site_id": site_id,
                "uri": f"replay://{camera_id}",
                "transport": "memory",
                "source_semantics": semantics,
                "profile_id": "validation",
                "width": width,
                "height": height,
            }
        ],
        "detection": {
            "enabled": True,
            "confidence_threshold": 0.25,
            "max_batch_size": 4,
            "batch_max_wait_ms": 0,
            "inference_timeout_ms": 2000,
            "queue_capacity": 32,
        },
        "models": {"warmup_enabled": True, "allow_cpu_fallback": True},
        "tracking": {
            "enabled": True,
            "tracker_id": "tracker.sort",
            "min_hits_to_confirm": 2,
            "max_coast_frames": 4,
            "max_lost_frames": 8,
        },
        "registry": {
            "enabled": True,
            "min_observations_to_confirm": 2,
            "persistence_enabled": False,
        },
        "cropping": {
            "enabled": True,
            "understanding_calls_per_hour": 360_000.0,
            # 224, not 64. This is the image the model is asked about, and a
            # 64x64 person is a smudge — the attribute quality a VLM can reach
            # is bounded by what it was shown. It is also what a reviewer sees
            # in the console, so the same number governs accuracy and
            # explicability.
            "crop_width": 224,
            "crop_height": 224,
            "min_scale_pixels": 24.0,
            # A privacy decision, recorded where privacy decisions belong.
            #
            # `ephemeral` (the default) means "lives only long enough to be
            # analysed" and the console's archive refuses to write it. `evidence`
            # means the crop may be retained for a bounded TTL so a claim can be
            # audited later, which is exactly what showing it in a review UI is.
            # A deployment that must not retain imagery sets `never_persist` and
            # nothing is written, whatever the UI would like.
            "retention_mode": "evidence",
        },
        "understanding": {
            "enabled": True,
            "timeout_ms": 1000,
            "max_retries": 1,
            "max_concurrency": 2,
            "max_batch_size": 4,
            "cache_capacity": 64,
        },
        "synthesis": {"enabled": True, "suppression_policy": "suppression.exact"},
        "state": {"enabled": True, "max_objects_per_partition": 256},
        "storage": {"evidence_store": "evidence.memory"},
        "api": {"enabled": True, "authorizer": "authz.static"},
        "taxonomy": [{"class_id": "person"}],
        "detectors": [],
    }


#: Ceiling on frame-buffer memory for one validation camera.
#:
#: 512 MB is generous for an engineering workstation and hard for a laptop to
#: notice. It exists because the alternative — sizing purely from the history
#: window — puts a 1080p replay at 6.2 MB per slot, and a 10-second window at
#: 25 fps would then reserve 1.5 GB before a single frame was decoded.
_BUFFER_BUDGET_BYTES = 512 * 1024 * 1024


def _buffer_sizing(*, frame_bytes: int, target_fps: float) -> tuple[int, int]:
    """Pick a history window and slot count that can actually coexist.

    The pool must hold every frame the history window promises, or the source
    blocks on an exhausted pool and the replay stalls a few frames in — which is
    exactly what happened the first time this was written with a fixed 6 slots
    and a 10-second window. At 12 fps that window needs 120 slots; it had 6, and
    `buffer.pool_pressure` fired on frame 7.

    Two seconds of history is enough for the Crop Manager to reach back for a
    frame after a trigger (its pins are short-lived), and the slot count is then
    derived from it rather than guessed. Where the budget cannot cover two
    seconds, the **window is shortened to match the slots** rather than the slots
    silently under-serving the window: a buffer that promises more history than
    it can hold is a stall waiting for a busy moment.
    """
    headroom = 8
    affordable = max(16, _BUFFER_BUDGET_BYTES // max(frame_bytes, 1))

    desired_seconds = 2.0
    needed = int(target_fps * desired_seconds) + headroom
    slots = min(needed, affordable, 512)

    usable_seconds = max(0.25, (slots - headroom) / max(target_fps, 0.1))
    history_ms = int(min(desired_seconds, usable_seconds) * 1000)
    return history_ms, max(16, slots)


# --- the attribute vocabulary ------------------------------------------------ #
#
# Understanding is bound with a **specialized head**, not a VLM.
#
# 06_PORTS makes the point that a static head and a VLM satisfy the same port and
# produce the same registered attribute, and that *"nothing downstream can tell
# which answered, except by reading the provenance that says so."* That is what
# makes this honest rather than a mock: the console exercises the real M8→M9→M11
# path — trigger, gate, route, coerce, validate, attach — and only the model at
# the far end is cheap.
#
# It also keeps the console runnable on a laptop with no GPU. Swapping in a real
# VLM is a `understanders=[...]` change here and nothing else.

# --- the semantic vocabulary -------------------------------------------------- #
#
# There is none here, and that is the change.
#
# This block used to declare `POSTURE`, `_COLOURS`, `_FURNITURE`, `_ACTIVITIES`
# and a `build_attribute_registry` that wrote them into the platform. Every one
# of those is a domain decision, and a domain decision compiled into a developer
# tool is a tool that needs a release to serve a second customer.
#
# They now live in a policy document (`backend/config/policies/*.json`), loaded
# through `adapters/configuration/semantic_policy.py`. `posture` and `hairnet`
# are equally foreign to this file, which is the property that makes the next
# use case a file rather than a patch.


def load_policies(env: dict | None = None) -> tuple:
    """Every active semantic policy, in declaration order.

    ``VISION_SEMANTIC_POLICY`` accepts a **comma-separated list** of documents. A
    single path is the degenerate case, so every configuration written before
    this keeps working unchanged and no new environment variable is introduced.

    A list is needed because a policy carries **one** subject-class scope, and
    the two use cases this demo runs concern different subjects: kitchen PPE is
    asked about `person`, and object corroboration is asked about the classes a
    closed-set detector is least able to name. Forcing both into one document
    would demand a head covering of a toothbrush.
    """
    import os

    from app.vision_os.adapters.configuration.semantic_policy import (
        POLICY_ENV,
        SemanticPolicy,
    )

    source = os.environ if env is None else env
    raw = str(source.get(POLICY_ENV, "")).strip()
    if not raw:
        return ()
    return tuple(
        SemanticPolicy.from_file(part.strip())
        for part in raw.split(",")
        if part.strip()
    )


def build_attribute_registry(policies=()) -> object:
    """The registered vocabulary — whatever the active policies declare.

    Registration still goes through the platform's own `AttributeRegistry`, so
    every entry passes the neutrality gate: an enum without a domain is refused,
    a missing justification is refused. A policy may *ask* for a concept; only
    the registry can *grant* it.

    With no policy the registry is empty, and that is a working configuration:
    no attributes means no demand, no crops for understanding and no model
    calls, while detection, tracking, the registry, Vision State and the
    Observation API run exactly as before.
    """
    from app.vision_os.perception.registry.attributes import AttributeRegistry

    registry = AttributeRegistry()
    for policy in policies:
        if policy is not None:
            policy.register_attributes(registry)
    return registry


def _register_policy_demands(cropping, policies, warnings: list[str]) -> None:
    """Raise **one demand per active policy**.

    Separate demands, not one merged demand, because that is what keeps the two
    use cases independent: kitchen PPE and object corroboration have different
    subjects, different freshness and different budgets, and the Crop Manager
    resolves the strictest freshness per attribute across whichever demands
    happen to cover an object. Merging them would apply one policy's cadence to
    the other's attributes.
    """
    for policy in policies:
        _register_policy_demand(cropping, policy, warnings)


def _register_policy_demand(cropping, policy, warnings: list[str]) -> None:
    """Raise the active policy's demand, so the Crop Manager has a reason to look.

    Without a demand the Crop Manager triggers nothing and the Understanding
    Engine is never called — which is **correct**: V7 forbids spending a model
    call on an attribute nobody asked for, and 11_PERFORMANCE puts the resulting
    saving at 166×.

    That correctness is also why the console once looked broken. An engineer
    opening it saw `Crop Manager: silent` and `Understanding: silent` and
    reasonably concluded something was wrong, when the platform was behaving
    exactly as designed for a deployment with no consumers.

    **Freshness, triggers and budget come from the policy document**, not from
    here and not from the adapter. With fifteen to twenty people in frame, those
    three values are the difference between a demo and a bill, and they belong
    where a deployment can change them without a release.

    Failure is recorded as a warning rather than raised: a session with no
    attributes is still a completely valid session for validating detection,
    tracking, identity and state.
    """
    if policy is None:
        return
    try:
        registry = getattr(cropping, "demands", None)
        if registry is None:
            warnings.append("no demand registry on the cropping layer; attributes stay off")
            return

        runtime = getattr(cropping, "runtime", None)
        now = runtime._clock.now() if hasattr(runtime, "_clock") else None  # noqa: SLF001
        registry.register(
            policy.build_demand(subscriber="validation-console", now=now),
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - attributes are optional, the session is not
        warnings.append(f"could not raise the policy demand: {type(exc).__name__}: {exc}")


def _select_understander(warnings: list[str], policy=None, policies=()):
    """Ask Vision OS for the configured understander, or fall back **loudly**.

    **This function names no provider**, and that is the point. It used to
    contain the selection itself — an `if nvidia`, an `if static`, a Qwen
    constructor — which meant the console knew what a hosted endpoint was, what
    an API key was for, and which of the two was cheaper. None of that is a
    developer tool's business, and every new provider would have edited this
    file.

    Selection now belongs to `adapters/understanding/providers.py`, where the
    platform keeps its closed factory table. The harness supplies the attributes
    it wants produced and a place to find credentials; a bound P15 adapter comes
    back. `VOSVC_UNDERSTANDER` is still honoured as this console's override, but
    it is passed *through* as an opaque name rather than interpreted here.

    What remains is the console's actual job: check the adapter can answer,
    warm it if it says it can be warmed, and record what happened. Both checks
    are made by capability (`hasattr`) rather than by type, so an adapter that
    needs neither simply does not offer them.

    `10_RELIABILITY` §7.2 rule 1: *"A fallback is never silent."* The reason
    travels to the UI and the model panel names whichever adapter is actually
    bound — a demo showing "NVIDIA Vision" while a constant answered would be the
    single most dishonest thing this application could do.
    """
    import os

    from app.vision_os.adapters.configuration import (
        ProviderConfigurationError,
        build_understander,
    )
    from app.vision_os.adapters.configuration.understander_providers import (
        resolve_provider_name,
    )
    from app.vision_os.adapters.understanding import StaticAttributeHead
    from app.vision_os.core.model.ids import AttributeKey

    if policy is None:
        # The pass-through. No semantic use case is configured, so no adapter is
        # bound, no demand exists to route, and no model is called. Detection,
        # tracking, the registry, Vision State and the Observation API are
        # unaffected — this is a platform without a use case, not a broken one.
        return None, "none (no semantic policy configured)"

    # The union across every active policy: one adapter answers all of them, and
    # an adapter that declared only the first policy's keys would make every
    # other policy's attributes an unroutable capability gap.
    active = tuple(policies) or ((policy,) if policy else ())
    producible = tuple(
        dict.fromkeys(key for p in active for key in p.attribute_keys)
    )

    # What the constant head would answer, if it comes to that. Taken from the
    # policy's first requirement because only the policy knows a legal value —
    # a value outside the registered domain is rejected by the validator, and
    # the pipeline then produces nothing while appearing to run.
    fallback_key = producible[0] if producible else None
    first = policy.requirements[0] if policy and policy.requirements else None
    fallback_value = first.values[0] if first and first.values else ""

    # `VOSVC_UNDERSTANDER` remains honoured as the console's own override, but it
    # is passed *through* rather than interpreted: the harness does not know that
    # "nvidia" means a hosted endpoint, and must not learn.
    forced = os.environ.get("VOSVC_UNDERSTANDER", "").strip().lower() or None

    # Which provider the deployment actually asked for. Resolved before any
    # fallback, so a demo can insist on a real model.
    requested = (forced or resolve_provider_name(os.environ)).strip().lower()

    def fall_back(reason: str):
        """The constant head, and the reason it was reached.

        10_RELIABILITY §7.2 rule 1: *"A fallback is never silent."* The reason
        travels to the UI, and the model panel names whichever adapter is
        actually bound — a console showing "NVIDIA Vision" while a constant
        answered would be the single most dishonest thing it could do.

        **When a real model was explicitly named, there is no fallback at all.**
        A constant head answers one attribute with a fixed value; feed that to a
        compliance rule and it produces a confident violation out of nothing.
        Loud is not enough for that failure — a demo audience does not read the
        warnings panel — so the session fails instead.
        """
        if requested in _REAL_PROVIDERS:
            raise VisionOsUnavailableError(
                f"provider '{requested}' was requested and is unavailable: {reason}. "
                f"Refusing to fall back to the constant-answer head: it produces "
                f"fixed values that are indistinguishable from model output "
                f"downstream, and a compliance rule evaluating them would report "
                f"violations that nothing observed. Fix the provider, or set "
                f"VISION_UNDERSTANDER_PROVIDER=static to accept constants knowingly."
            )
        warnings.append(reason)
        # The head answers with one attribute; which one, and with what
        # value, is the policy's to say. Without a policy there is nothing to
        # answer about, and the terminal understander reports that honestly
        # rather than inventing a field.
        if fallback_key is None:
            # Nothing to answer about. `build_understanding_layer` documents
            # that an empty understander list is legal, and an empty list is
            # more truthful than a terminal adapter pretending to offer a
            # capability nobody asked for.
            return None, f"none ({reason})"
        return (
            StaticAttributeHead(attribute=fallback_key, value=fallback_value),
            f"static (fallback: {reason})",
        )

    try:
        adapter, note = build_understander(
            producible=producible,
            provider=forced,
            # The Atlas backend's .env is where this deployment already keeps its
            # NVIDIA credentials. Reading them there beats copying a key into the
            # console, where the second copy is the one nobody rotates.
            env_file=default_vision_os_root() / ".env",
            # The constant head answers with a fixed value, and that value has to
            # be a member of the attribute's registered domain — which only this
            # module knows, because this module registered it. `"standing"` is a
            # legal `posture`; a generic default such as `"unknown"` is not, and
            # the validator would reject every field while the pipeline appeared
            # to run.
            defaults={"VISION_STATIC_VALUE": fallback_value},
        )
    except ProviderConfigurationError as exc:
        return fall_back(f"understander not configured ({exc})")

    # Probe and warm by *capability*, not by provider.
    #
    # Asking `hasattr` rather than `isinstance` is what keeps this function
    # provider-agnostic: a future adapter that needs no warming simply does not
    # offer the method, and nothing here has to learn its name.
    probe = getattr(adapter, "probe", None)
    if callable(probe):
        health = probe()
        if not health.get("available"):
            return fall_back(
                f"{note} is unavailable ({health.get('error')}). Attributes would "
                f"be constants, not model output."
            )

    residency = adapter.capabilities().data_residency
    if residency != "local":
        # Stated at binding because it is a privacy decision, not a performance
        # one: crops are about to leave this machine.
        warnings.append(
            f"{note} bound with data_residency={residency} — crops leave this host."
        )

    # Warm **synchronously, before binding**.
    #
    # `build_understanding_layer` runs the P15 conformance kit against this
    # adapter, and that kit makes real `understand()` calls. Against a cold local
    # model each one is 138–210 s, which is why boot once measured 347 s: the
    # gate was paying the model-load cost twice over. Warming first turns those
    # same gate calls into ~6 s each — paid once, up front, and *measured*.
    warm = getattr(adapter, "warm", None)
    if callable(warm):
        warmed = warm()
        if warmed.get("warmed"):
            warnings.append(
                f"{note} warmed in {warmed['elapsed_ms'] / 1000:.1f}s (one-off)"
            )
        else:
            warnings.append(f"{note} warm-up failed: {warmed.get('error')}")

    # NOTE: `mark_binding_complete()` is deliberately **not** called here. The
    # conformance kit runs inside `build_understanding_layer`, which happens
    # after this function returns, so marking now would count the gate's probes
    # as perception work and make the platform look more expensive than a naive
    # per-frame pipeline — the opposite of true. The caller marks it once the
    # layer is built.
    return adapter, note


def build_prompt_pack(policies=()):
    """The M10 stand-in — **one prompt per policy**, rendered from its document.

    `StaticPromptProvider` already takes a tuple of templates, and the engine
    resolves the one whose `applies_to` covers the object's class. So two use
    cases with different subjects need no plumbing beyond handing over two
    templates: a person crop gets the PPE question and a candidate-object crop
    gets the identity question, and neither prompt ever sees the other's crop.

    A prompt declaring an unregistered key is refused **at load** (the ceiling's
    second gate), so a policy whose prompt and attributes disagree fails before
    it costs a model call.

    The wording is the policy's. What this function contributes is the plumbing:
    a `StaticPromptProvider` holding the rendered, version-pinned template that
    P17 hands to the engine, which hands it to whichever adapter is bound.
    """
    from app.vision_os.adapters.understanding import StaticPromptProvider

    if not policies:
        # No prompt, because there is nothing to ask about. The engine treats an
        # absent prompt as a capability gap and says so, rather than inventing a
        # question.
        return StaticPromptProvider(())

    return StaticPromptProvider(
        tuple(p.build_prompt_template() for p in policies if p is not None)
    )


# --- the Flow 3/4 bridge ---------------------------------------------------- #


class _TrackingToRegistry:
    """Bridges tracking's synchronous sink to the registry's async seam.

    Flow 3's runtime takes a synchronous sink; the registry's seam is async, so
    the hand-off is scheduled rather than awaited. In a distributed deployment
    this same edge becomes a queue — the shape of the seam is what matters.

    Reimplemented here rather than imported from the platform's test suite:
    depending on Vision OS's tests would couple this repo to code that carries no
    stability promise, and the bridge is six lines.
    """

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self.pending: list = []

    def __call__(self, outcome) -> None:
        if outcome.failed:
            return
        self.pending.append(outcome)

    async def drain(self) -> int:
        drained = 0
        while self.pending:
            outcome = self.pending.pop(0)
            update = _update_from(outcome)
            if update is not None:
                await self._runtime.on_tracked(outcome.camera_id, update)
                drained += 1
        return drained


def _update_from(outcome):
    """Rebuild a `TrackUpdate`, **carrying the real frame reference**.

    `TrackingOutcome.frame_ref` is a string in the platform's own format,
    `camera/e{epoch}/f{seq}`, and it must survive this hop intact.

    An earlier version fabricated `FrameSeq(len(outcome.tracks))` here. It
    type-checked, it ran, and it quietly broke the entire attention path: the
    Crop Manager triggered correctly, asked the Frame Buffer for frame 1 every
    single time, and got nothing — 63 `cropping.frame_unavailable` in a 76-frame
    run. No error was raised anywhere, because "the frame is gone" is a normal
    condition the platform is designed to absorb. The only symptom was that the
    Understanding panel stayed empty forever.

    That metric's docstring is exactly right about why it exists: it *"diagnoses
    pin TTL and buffer depth rather than leaving a hole nobody can explain."*
    Here it diagnosed a caller passing a fictional frame number.
    """
    from app.vision_os.core.model.track import TrackUpdate

    if not outcome.tracks:
        return None

    frame_ref = _parse_frame_ref(outcome.frame_ref, outcome.camera_id)
    if frame_ref is None:
        return None

    return TrackUpdate(
        camera_id=outcome.camera_id,
        frame_ref=frame_ref,
        tracker_epoch=outcome.tracker_epoch,
        active=outcome.tracks,
    )


def _parse_frame_ref(raw, camera_id):
    """`"cam-1/e1/f42"` → `FrameRef`. Returns `None` rather than guessing.

    A fabricated frame reference points the Crop Manager at a frame that never
    existed, which is worse than declining to hand the update over at all.
    """
    from app.vision_os.core.model.ids import FrameRef, FrameSeq, StreamEpoch

    if isinstance(raw, FrameRef):
        return raw

    text = str(raw or "")
    parts = text.rsplit("/", 2)
    if len(parts) != 3:
        return None

    camera, epoch_part, seq_part = parts
    try:
        epoch = int(epoch_part.lstrip("e"))
        seq = int(seq_part.lstrip("f"))
    except ValueError:
        return None

    return FrameRef(type(camera_id)(camera) if camera else camera_id, StreamEpoch(epoch), FrameSeq(seq))


# --- assembled stack -------------------------------------------------------- #


@dataclass(slots=True)
class AssembledStack:
    """Everything the session needs to drive and observe one Vision OS."""

    system: Any
    platform: Any
    detection: Any
    tracking: Any
    registry_layer: Any
    cropping: Any
    understanding: Any
    #: The P15 adapter actually bound. The model panel names *this*, never the
    #: adapter the demo hoped for.
    understander: Any
    understanding_note: str
    #: Retained crops for this session, if the deployment's retention policy
    #: allows any to be written. Empty is a legitimate state, not a fault.
    crop_archive: Any
    detection_runtime: Any
    bridge: _TrackingToRegistry
    clock: Any
    cursor: ReplayCursor
    ledger: FaultLedger
    source: Any
    decoder: Any
    warnings: list[str] = field(default_factory=list)
    #: Every active semantic policy, in declaration order. Held so the console
    #: can report which use cases are live without re-reading the environment.
    policies: tuple = ()
    #: The corroboration rules actually bound, or `None`. Reported for the same
    #: reason: a demo claiming verification is on must be able to prove it.
    verification: Any = None

    @property
    def api(self):
        return self.system.api

    @property
    def state(self):
        return self.system.state


def build_stack(
    *,
    frames,
    camera_id: str,
    tenant_id: str,
    site_id: str,
    target_fps: float,
    semantics: str,
    cursor: ReplayCursor,
    ledger: FaultLedger,
    on_frame=None,
    rtsp: bool = False,
    crop_root: Path | str | None = None,
) -> AssembledStack:
    """Boot L0–L7 over a replay source. Public composition roots only.

    Raises:
        VisionOsUnavailableError: Vision OS could not be imported or assembled.
            Raised rather than degraded, because a validation console attached to
            a platform that is not running would validate nothing while looking
            like it was.
    """
    ensure_importable()
    try:
        from app.vision_os.adapters.configuration import InMemoryConfigSource
        from app.vision_os.adapters.configuration.detector_providers import build_detector
        from app.vision_os.adapters.configuration.detector_providers import (
            label_space_view,
        )
        from app.vision_os.adapters.configuration.semantic_policy import POLICY_ENV
        from app.vision_os.adapters.configuration.verification_rules import (
            load_verification_rules,
        )
        from app.vision_os.adapters.exposure.authorization import full_grant, read_only_grant
        from app.vision_os.adapters.exposure.authorization import StaticAuthorizer
        from app.vision_os.adapters.models import InMemoryArtifactStore, ScriptedRuntime
        from app.vision_os.adapters.persistence import InMemoryEvidenceStore
        from app.vision_os.adapters.registry import InMemoryObjectStore
        from app.vision_os.adapters.synthesis import InMemoryObservationLog
        from app.vision_os.acquisition import SourceBindings
        from app.vision_os.adapters.acquisition import ArrivalTimeClockSync, NoMaskPolicy
        from app.vision_os.bootstrap import build_platform
        from app.vision_os.conformance import platform_registry
        from app.vision_os.core.model.api import CapabilitySummary
        from app.vision_os.adapters.understanding import StaticAttributeHead
        from app.vision_os.core.model.frame import FrameDimensions
        from app.vision_os.core.model.ids import AttributeKey, CameraId, ClassId, TenantId
        from app.vision_os.perception.cropping import CapabilityView
        from app.vision_os.understanding_bootstrap import build_understanding_layer
        from app.vision_os.cropping_bootstrap import build_cropping_layer
        from app.vision_os.detection_bootstrap import build_detection_layer
        from app.vision_os.kernel.clock import VirtualClock
        from app.vision_os.kernel.config import ConfigLayer
        from app.vision_os.perception.detection import DetectionRuntime
        from app.vision_os.registry_bootstrap import build_registry_layer
        from app.vision_os.system import assemble
        from app.vision_os.tracking_bootstrap import build_tracking_layer
    except Exception as exc:  # noqa: BLE001
        raise VisionOsUnavailableError(
            f"Vision OS is not importable: {type(exc).__name__}: {exc}"
        ) from exc

    if not frames:
        raise VisionOsUnavailableError("no frames were decoded; nothing to replay")

    first = frames[0]
    width, height = int(first.width), int(first.height)
    dimensions = FrameDimensions(width=width, height=height, colour_space="bgr24")
    warnings: list[str] = []

    clock = VirtualClock()
    document = build_config_document(
        camera_id=camera_id,
        tenant_id=tenant_id,
        site_id=site_id,
        width=width,
        height=height,
        target_fps=target_fps,
        semantics=semantics,
    )

    source_cls = RtspReplaySource if rtsp else ReplayFileSource
    source = source_cls(
        frames,
        clock=clock,
        cursor=cursor,
        ledger=ledger,
        interpacket_ms=1000.0 / max(target_fps, 0.1),
        on_frame=on_frame,
    )
    decoder = ValidationDecoder(dimensions=dimensions, ledger=ledger, cursor=cursor)

    def bindings_factory(camera):
        return SourceBindings(
            source=source,
            decoder=decoder,
            privacy=NoMaskPolicy(),
            clock_sync=ArrivalTimeClockSync(),
        )

    # --- detection --------------------------------------------------------- #
    #
    # The real detector is bound. Which one is a configuration fact resolved by
    # `adapters/configuration/detector_providers.py`, exactly as the understander
    # is — this module names no model and contains no inference logic.
    #
    # It used to bind `ReferenceDetector` with a single scripted box, on the
    # reasoning that a validation console must certify the *pipeline* on a laptop
    # with no GPU. That reasoning held for a contract test and failed for
    # everything else: a fixed rectangle labelled `person` on every frame means
    # tracking follows a fiction, every crop shows the same region, and the VLM
    # answers questions about pixels nobody chose. The scripted detector remains
    # available (`VISION_DETECTOR_PROVIDER=reference`) for fixtures that need
    # deterministic synthetic detections, and it is never the default.
    #
    # The declaration is written into the document **before** the platform is
    # built, because `build_platform` reads configuration once at construction —
    # mutating the document afterwards would leave the running platform bound to
    # a detector list it never saw.
    bound_detector = build_detector(clock=clock)
    warnings.append(f"detector bound: {bound_detector.note}")

    # The active semantic policy, or None. A use case arrives as a document; this
    # module names no attribute, no enum value and no scene. `None` is supported
    # and means exactly what it says — no attributes registered, no demand
    # raised, therefore no model calls, with detection, tracking, state and the
    # Observation API running unchanged.
    policies = load_policies()
    policy = policies[0] if policies else None
    if not policies:
        warnings.append(
            "no semantic policy configured; running without attribute understanding "
            f"(set {POLICY_ENV} to a policy document to enable it)"
        )
    for active in policies:
        warnings.append(
            f"semantic policy: {active.policy_id}@{active.version} "
            f"({len(active.requirements)} attributes on "
            f"{list(active.object_classes) or 'any class'}, "
            f"freshness {active.freshness_ms}ms)"
        )

    # The corroboration rules, or None. `None` means this deployment verifies
    # nothing: `build_cropping_layer` then returns the unwrapped trigger policy
    # and behaves exactly as it did before the seam existed.
    verification = load_verification_rules()
    if verification is not None and verification.rules:
        warnings.append(
            f"verification rules: {len(verification.rules)} rule(s) governing "
            f"{sorted(str(k) for k in verification.governed_attributes)}"
        )

    # The artifact hash is the real file's `blake2b:<hex>` — the platform's own
    # `compute_hash` format. A mismatch fails closed and is never retried into
    # success, because loading unverified weights is a supply-chain event rather
    # than a network glitch (12_SECURITY §6). Registering the actual weights
    # makes that check mean something; the previous binding hashed a placeholder
    # string and verified nothing.
    artifacts = InMemoryArtifactStore()
    artifact_uri = "mem://vosvc-detector.bin"
    if bound_detector.artifact_path:
        artifacts.put(artifact_uri, Path(bound_detector.artifact_path).read_bytes())
    else:
        artifacts.put(artifact_uri, b"reference")

    document["detectors"] = [
        bound_detector.declaration(detector_id="vosvc-primary", artifact_uri=artifact_uri)
    ]
    # The taxonomy must name every class the detector can map, or the platform
    # rejects the mapping at load — which is the check working, and the reason
    # these two are derived from one source rather than typed twice.
    document["taxonomy"] = [{"class_id": str(class_id)} for class_id in bound_detector.classes]

    platform = build_platform(
        config_sources={ConfigLayer.SITE: InMemoryConfigSource(document)},
        bindings_factory=bindings_factory,
        clock=clock,
        conformance=platform_registry(),
    )

    person = ClassId("person")

    def detector_factory(_declaration):
        """Hand back the detector resolved above.

        Built once, before `build_platform`, because the same instance has to
        satisfy the declaration written into the config document — and because
        opening an ONNX session per call would reload 12 MB of weights on every
        camera attach.
        """
        return bound_detector.detector

    # Registered through the platform's own registry, which applies the
    # neutrality gate to each one. A policy asks; the registry decides.
    attributes = build_attribute_registry(policies)
    registry_layer = build_registry_layer(platform, store=InMemoryObjectStore())

    # The Crop Manager must be told what the platform can currently produce.
    # Without this it has no reason to trigger a crop, because it would be
    # spending budget on an attribute nothing can answer (V7).
    policy_keys = frozenset(
        key for active in policies for key in active.attribute_keys
    )

    # The declared Flow 5 seam. `build_cropping_layer` takes a `crop_sink`, and
    # this is the console using it for what it is for: the crops M8 hands to M9
    # are the same objects, retained rather than re-extracted.
    crop_archive = CropArchive(
        crop_root or (Path(__file__).resolve().parents[2] / "crops"),
        session_id=camera_id,
    )
    cropping = build_cropping_layer(
        platform,
        registry_layer,
        capabilities=CapabilityView(
            registered_attributes=policy_keys,
            producible_attributes=policy_keys,
            producible_classes=frozenset(bound_detector.classes),
            observed_cameras=frozenset({CameraId(camera_id)}),
        ),
        crop_sink=crop_archive.accept,
        # What the bound detector can and cannot name, handed to M8 so a trigger
        # policy can *act* on the capability the detector already declares — not
        # merely report it to a consumer afterwards. Derived at the one place
        # that holds both the detector and its declaration, so the two cannot
        # drift.
        label_space=label_space_view(bound_detector),
        # The corroboration rules. `None` leaves the trigger policy unwrapped.
        verification=verification,
        attach=True,
    )

    understander, understanding_note = _select_understander(warnings, policy, policies)
    understanding = build_understanding_layer(
        platform,
        cropping,
        # Empty when no policy is configured — legal, and documented as such
        # by `build_understanding_layer`.
        understanders=[understander] if understander is not None else [],
        prompts=build_prompt_pack(policies),
        attributes=attributes,
        attach=True,
    )

    # The conformance gate has now run. Everything the adapter does from here is
    # perception work, and only that is counted in the economy figure.
    if understander is not None and hasattr(understander, "mark_binding_complete"):
        understander.mark_binding_complete()

    tracking = build_tracking_layer(platform, tracking_sink=None)
    detection = build_detection_layer(
        platform,
        detector_factory=detector_factory,
        artifacts=artifacts,
        runtimes=(ScriptedRuntime(),),
        detection_consumer=tracking.runtime,
    )

    tenant = TenantId(tenant_id)
    system = assemble(
        platform,
        registry_layer=registry_layer,
        cropping=cropping,
        understanding=understanding,
        detection=detection,
        tracking=tracking,
        # The builder needs the same vocabulary the registry gates against, or
        # it would reject at the final gate every attribute understanding just
        # produced — the ceiling's third gate firing on a legitimate value.
        attributes=attributes,
        log=InMemoryObservationLog(),
        evidence=InMemoryEvidenceStore(),
        authorizer=StaticAuthorizer(
            [read_only_grant("console", tenant), full_grant("engineer", tenant)]
        ),
        # `gaps` carries what the bound detector *cannot* do, alongside what it
        # can. For a closed-set model that is the load-bearing half: a consumer
        # reading `producible_classes` alone learns the eighty words available
        # and not that everything else in the world is reported as whichever of
        # those eighty is nearest. The detector declares it; this composition
        # root forwards it; nothing interprets it on the way.
        capabilities=CapabilitySummary(
            taxonomy_version="1",
            producible_classes=bound_detector.classes,
            gaps=bound_detector.capability_gaps(),
            models_in_use=(
                (
                    bound_detector.adapter_id,
                    bound_detector.model_id,
                    bound_detector.model_version,
                ),
            ),
        ),
    )

    bridge = _TrackingToRegistry(registry_layer.runtime)
    tracking.runtime._sink = bridge  # noqa: SLF001 - the declared Flow 3/4 seam

    detection_runtime = DetectionRuntime(
        clock=platform.clock,
        bus=platform.bus,
        metrics=platform.metrics,
        health=platform.health,
        engine=detection.engine,
        consumer=tracking.runtime,
    )
    platform.runtime._admitted_consumer = detection_runtime  # noqa: SLF001 - the declared Flow 2 seam

    _register_policy_demands(cropping, policies, warnings)

    return AssembledStack(
        system=system,
        platform=platform,
        detection=detection,
        tracking=tracking,
        registry_layer=registry_layer,
        cropping=cropping,
        understanding=understanding,
        understander=understander,
        understanding_note=understanding_note,
        policies=policies,
        verification=verification,
        crop_archive=crop_archive,
        detection_runtime=detection_runtime,
        bridge=bridge,
        clock=clock,
        cursor=cursor,
        ledger=ledger,
        source=source,
        decoder=decoder,
        warnings=warnings,
    )
