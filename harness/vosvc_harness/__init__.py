"""Vision OS Validation Harness.

A P32 transport adapter (HTTP + WebSocket) over the public `ObservationApi`,
plus P1/P2 acquisition adapters for recorded video. Built entirely on Vision OS's
public composition roots; it modifies nothing.
"""

from .config import HarnessConfig, load_config
from .contract import WIRE_MAJOR, WIRE_VERSION, encode, encode_error

__version__ = WIRE_VERSION

__all__ = [
    "WIRE_MAJOR",
    "WIRE_VERSION",
    "HarnessConfig",
    "encode",
    "encode_error",
    "load_config",
]
