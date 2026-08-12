"""Retained crops, written by the declared Flow 5 sink.

### Why this exists

The UI needs to show the image a model was actually asked about. Nothing in
Vision OS writes crop bytes anywhere: M8 *decides* retention policy and the
evidence store *honours* it, but no module in the platform calls
``EvidenceStorePort.put``. `get_evidence` therefore returns 404 for every
reference an attribute carries, and the picture the model saw is unrecoverable.

This closes that gap on the **console side**, through the seam the platform
already declares: ``build_cropping_layer(crop_sink=…)``. No Vision OS module
changes, and nothing here is a second pipeline — the crops handed to this sink
are the same objects M8 sent to M9, not a re-extraction.

### The rule that governs whether a crop is written at all

``RetentionMode`` is M8's decision and this sink obeys it:

| Mode | Meaning | This sink |
|---|---|---|
| ``NEVER_PERSIST`` | *"must not touch disk"* (12_SECURITY §2.3) | refuses, always |
| ``EPHEMERAL`` | *"lives only long enough to be analysed"* | refuses |
| ``EVIDENCE`` | retained for a bounded TTL so a claim can be audited | writes |

A deployment that wants retrievable crops sets ``cropping.retention_mode`` to
``evidence`` in its configuration, which is a privacy decision recorded where
privacy decisions belong. Writing an ephemeral crop because a UI wanted to show
it would quietly convert a policy into a leak.

### Why the skips are kept too

``EvaluationResult`` arrives at this sink alongside the crops, and it carries the
other half of the accounting: every candidate M8 considered and did **not** crop,
with an attributed ``SkipReason``. This archive used to drop it on the floor.

Keeping it is what lets a viewer show a complete frame. A detection-only object
has no crop and never will — M8 crops what a demand asked for, and cropping
everything is the cost model the platform exists to avoid — so the only honest
thing to display next to it is the platform's own recorded reason. Without the
skip, "no image" and "no demand for an image" look identical on screen, which is
the exact confusion invariant V8 and §M8 responsibility 7 exist to prevent.

Nothing here re-derives that reason. It is copied verbatim from the record M8
already produced.
"""

from __future__ import annotations

import base64
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Serving stays bounded: a demo left running must not fill a disk.
MAX_RETAINED = 2_000

#: The same bound applied to skip records, which cost memory rather than disk.
#: A 20-object scene skipping most candidates on every frame reaches this in a
#: few hundred frames, and the oldest frames are the ones nobody is looking at.
MAX_SKIPS = 4_000


@dataclass(frozen=True, slots=True)
class RecordedSkip:
    """One candidate M8 considered and did not crop, and the reason it gave."""

    object_id: str
    frame_seq: int | None
    reason: str
    detail: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "frame_seq": self.frame_seq,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RetainedCrop:
    """One written crop and the references needed to find it again."""

    crop_id: str
    object_id: str | None
    class_hint: str
    camera_id: str
    frame_seq: int | None
    t_capture_ns: int
    width: int
    height: int
    path: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "crop_id": self.crop_id,
            "object_id": self.object_id,
            "camera_id": self.camera_id,
            "frame_seq": self.frame_seq,
            "t_capture_ns": self.t_capture_ns,
            "width": self.width,
            "height": self.height,
        }


class CropArchive:
    """Writes retained crops to a folder and indexes them for retrieval.

    One archive per session, so closing a session can drop its images without
    reasoning about which of them another session might still be showing.
    """

    def __init__(self, root: Path | str, *, session_id: str) -> None:
        self._root = Path(root) / session_id
        self._session_id = session_id
        self._lock = threading.Lock()
        self._by_crop: dict[str, RetainedCrop] = {}
        self._by_object: dict[str, list[str]] = {}
        #: Insertion-ordered, so eviction can drop the oldest without a clock.
        self._skips: dict[tuple[str, int | None, str], RecordedSkip] = {}
        self.refused_ephemeral = 0
        self.refused_never_persist = 0
        self.written = 0
        self.skipped = 0
        self.errors = 0

    @property
    def root(self) -> Path:
        return self._root

    # --- the Flow 5 sink ------------------------------------------------------ #

    def accept(self, evaluation: Any, crops: tuple[Any, ...]) -> None:
        """Called by the Crop Manager with the crops it just produced.

        **Never raises.** The runtime treats a failing sink as a sink problem
        and carries on — attention must not stop because a disk was full — so
        errors are counted and reported rather than propagated.
        """
        try:
            self._record_skips(evaluation)
        except Exception:  # noqa: BLE001 - accounting must not break attention either
            self.errors += 1

        for crop in crops:
            try:
                self._write(crop)
            except Exception:  # noqa: BLE001 - a bad archive must not break attention
                self.errors += 1

    def _record_skips(self, evaluation: Any) -> None:
        """Keep M8's own reason for every candidate it did not crop.

        Read by capability rather than by type: the sink is handed whatever the
        cropping runtime publishes, and an evaluation without a `skipped` tuple
        is a legitimate shape rather than a fault.
        """
        skipped = getattr(evaluation, "skipped", None)
        if not skipped:
            return

        frame_ref = getattr(evaluation, "frame_ref", None)
        raw_seq = getattr(frame_ref, "frame_seq", None)
        frame_seq = int(raw_seq) if raw_seq is not None else None

        with self._lock:
            for skip in skipped:
                object_id = str(getattr(skip, "object_id", "") or "")
                if not object_id:
                    continue
                reason = getattr(skip, "reason", None)
                record = RecordedSkip(
                    object_id=object_id,
                    frame_seq=frame_seq,
                    # `.value` is the enum's wire form; the fallback keeps a
                    # future reason readable rather than rendering as a repr.
                    reason=str(getattr(reason, "value", reason) or "unstated"),
                    detail=str(getattr(skip, "detail", "") or ""),
                )
                key = (record.object_id, record.frame_seq, record.reason)
                if key in self._skips:
                    continue
                self._skips[key] = record
                self.skipped += 1
            while len(self._skips) > MAX_SKIPS:
                self._skips.pop(next(iter(self._skips)))

    def _write(self, crop: Any) -> None:
        retention = getattr(getattr(crop, "retention", None), "value", "")
        if retention == "never_persist":
            self.refused_never_persist += 1
            return
        if retention != "evidence":
            # Ephemeral, or a mode this console does not recognise. Both refuse:
            # writing on an unrecognised policy is how a policy stops meaning
            # anything.
            self.refused_ephemeral += 1
            return

        pixels = getattr(crop, "pixels", None)
        if pixels is None:
            return

        width, height = crop.output_size
        png = _png_bytes(pixels, int(width), int(height))

        crop_id = str(crop.crop_id)
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{_safe(crop_id)}.png"
        path.write_bytes(png)

        frame_ref = getattr(crop, "source_frame", None)
        record = RetainedCrop(
            crop_id=crop_id,
            object_id=str(crop.object_id) if crop.object_id else None,
            class_hint="",
            camera_id=str(crop.camera_id),
            frame_seq=int(getattr(frame_ref, "frame_seq", -1)) if frame_ref else None,
            t_capture_ns=int(getattr(crop.t_capture, "ns", 0)),
            width=int(width),
            height=int(height),
            path=str(path),
        )

        with self._lock:
            self._by_crop[crop_id] = record
            if record.object_id:
                held = self._by_object.setdefault(record.object_id, [])
                if crop_id not in held:
                    held.append(crop_id)
            self.written += 1
            self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while len(self._by_crop) > MAX_RETAINED:
            oldest, record = next(iter(self._by_crop.items()))
            self._by_crop.pop(oldest, None)
            for held in self._by_object.values():
                if oldest in held:
                    held.remove(oldest)
            try:
                Path(record.path).unlink(missing_ok=True)
            except OSError:
                pass

    # --- retrieval -------------------------------------------------------------- #

    def get(self, crop_id: str) -> RetainedCrop | None:
        with self._lock:
            return self._by_crop.get(crop_id)

    def bytes_of(self, crop_id: str) -> bytes | None:
        record = self.get(crop_id)
        if record is None:
            return None
        try:
            return Path(record.path).read_bytes()
        except OSError:
            return None

    def for_object(self, object_id: str) -> tuple[RetainedCrop, ...]:
        with self._lock:
            ids = list(self._by_object.get(object_id, ()))
            return tuple(self._by_crop[i] for i in ids if i in self._by_crop)

    def skips_for(self, object_id: str) -> tuple[RecordedSkip, ...]:
        with self._lock:
            return tuple(
                record for record in self._skips.values() if record.object_id == object_id
            )

    def index(self) -> dict[str, Any]:
        """Everything retained, so a viewer can map an object to its picture.

        `skips_by_object` is the counterpart: the objects that produced no
        picture, and M8's reason for each. A viewer holding both can render every
        object a frame contains without inventing an image for any of them.
        """
        with self._lock:
            skips: dict[str, list[dict[str, Any]]] = {}
            for record in self._skips.values():
                skips.setdefault(record.object_id, []).append(record.to_wire())
            return {
                "session_id": self._session_id,
                "written": self.written,
                "retained": len(self._by_crop),
                "skipped": self.skipped,
                "refused_ephemeral": self.refused_ephemeral,
                "refused_never_persist": self.refused_never_persist,
                "errors": self.errors,
                "by_object": {
                    object_id: [
                        self._by_crop[i].to_wire() for i in ids if i in self._by_crop
                    ]
                    for object_id, ids in self._by_object.items()
                },
                "skips_by_object": skips,
            }

    def dispose(self) -> None:
        """Drop this session's images. Called when the session is closed."""
        with self._lock:
            self._by_crop.clear()
            self._by_object.clear()
            self._skips.clear()
        try:
            for file in self._root.glob("*.png"):
                file.unlink(missing_ok=True)
            self._root.rmdir()
        except OSError:
            pass


def _png_bytes(pixels: Any, width: int, height: int) -> bytes:
    """BGR24 crop bytes → PNG.

    Reuses the platform adapter's encoder rather than carrying a second one; it
    returns base64, so this decodes it back. A wasted round trip, and cheaper
    than two implementations of a PNG writer drifting apart.
    """
    from app.vision_os.adapters.understanding.payload import encode_png_base64

    return base64.b64decode(
        encode_png_base64(pixels, width, height, colour_space="bgr24", max_side=512)
    )


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)[:120]


__all__ = ["CropArchive", "RecordedSkip", "RetainedCrop"]
