"""Serialization tests.

The encoder must translate shapes without interpreting them. The assertion that
matters most is the `Instant`/`Duration` split: both wrap an integer `ns` and
both expose `millis`, so a shape-based discriminator silently mislabels every
timestamp in the system — which is exactly the bug this suite was written after.
"""

from __future__ import annotations

import base64
import enum
from dataclasses import dataclass, field

import pytest

from vosvc_harness.contract import HTTP_STATUS, encode, encode_error, status_for


@dataclass(frozen=True)
class Instant:
    """Mirrors the platform's Instant: integer ns, int millis."""

    ns: int

    @property
    def millis(self) -> int:
        return self.ns // 1_000_000


@dataclass(frozen=True)
class Duration:
    """Mirrors the platform's Duration: integer ns, float millis."""

    ns: int

    @property
    def millis(self) -> float:
        return self.ns / 1_000_000


class Colour(enum.Enum):
    RED = "red"


@dataclass(frozen=True)
class Sample:
    name: str
    observed_at: Instant
    window: Duration
    colour: Colour
    payload: bytes
    tags: tuple[str, ...] = ()
    nested: dict = field(default_factory=dict)


class TestInstantAndDuration:
    def test_an_instant_becomes_integer_nanoseconds_with_an_ns_suffix(self) -> None:
        encoded = encode(Sample("s", Instant(1_500_000_000), Duration(0), Colour.RED, b""))
        assert encoded["observed_at_ns"] == 1_500_000_000
        assert "observed_at" not in encoded

    def test_a_duration_becomes_float_milliseconds_with_an_ms_suffix(self) -> None:
        encoded = encode(Sample("s", Instant(0), Duration(2_500_000), Colour.RED, b""))
        assert encoded["window_ms"] == pytest.approx(2.5)
        assert "window" not in encoded

    def test_the_two_are_never_confused(self) -> None:
        """The regression this suite exists for.

        Both types expose `ns` and `millis`. A discriminator based on which
        attributes are present classifies every Instant as a Duration, and the
        console then renders capture times as durations — a bug that survives
        every test that does not check the field NAME.
        """
        encoded = encode(Sample("s", Instant(83_000_000), Duration(83_000_000), Colour.RED, b""))
        assert "observed_at_ns" in encoded
        assert "window_ms" in encoded
        assert encoded["observed_at_ns"] == 83_000_000
        assert encoded["window_ms"] == pytest.approx(83.0)

    def test_a_bare_integer_is_untouched(self) -> None:
        assert encode(42) == 42
        assert encode({"count": 7}) == {"count": 7}


class TestStructures:
    def test_enums_become_their_value(self) -> None:
        assert encode(Colour.RED) == "red"

    def test_bytes_become_base64_not_text(self) -> None:
        payload = bytes([0xFF, 0xFE, 0x00, 0x41])
        encoded = encode(payload)
        assert base64.b64decode(encoded) == payload

    def test_nested_structures_recurse(self) -> None:
        encoded = encode(
            Sample("s", Instant(1), Duration(1), Colour.RED, b"", ("a", "b"), {"k": Colour.RED})
        )
        assert encoded["tags"] == ["a", "b"]
        assert encoded["nested"] == {"k": "red"}

    def test_an_unknown_object_is_reported_rather_than_dropped(self) -> None:
        class Opaque:
            __slots__ = ()

        encoded = encode(Opaque())
        # Silently omitting a field the transport does not understand is the
        # hardest kind of observability hole to notice.
        assert "__repr__" in encoded

    def test_derived_properties_are_serialized(self) -> None:
        @dataclass(frozen=True)
        class WithDerived:
            raw: int

            @property
            def is_stale(self) -> bool:
                return self.raw > 5

        assert encode(WithDerived(9))["is_stale"] is True
        assert encode(WithDerived(1))["is_stale"] is False

    def test_a_raising_property_does_not_break_the_wire(self) -> None:
        @dataclass(frozen=True)
        class Broken:
            raw: int

            @property
            def is_stale(self) -> bool:
                raise RuntimeError("boom")

        encoded = encode(Broken(1))
        assert encoded["raw"] == 1
        assert "is_stale" not in encoded


class TestErrors:
    def test_an_unknown_exception_renders_the_envelope(self) -> None:
        view = encode_error(ValueError("bad"), request_id="req-1")
        assert view["code"] == "INTERNAL"
        assert view["retryable"] is False
        assert view["request_id"] == "req-1"

    def test_retryability_is_a_field(self) -> None:
        view = encode_error(RuntimeError("x"))
        assert "retryable" in view
        assert isinstance(view["retryable"], bool)

    def test_overloaded_is_the_only_retryable_status(self) -> None:
        retryable = {code for code, http in HTTP_STATUS.items() if http == 429}
        assert retryable == {"OVERLOADED"}

    def test_unknown_codes_do_not_become_500(self) -> None:
        # A code this harness has not seen is a client-visible contract problem,
        # not an internal error — reporting it as 500 would send integrators
        # hunting through platform logs for their own typo.
        assert status_for("SOMETHING_NEW") == 400
