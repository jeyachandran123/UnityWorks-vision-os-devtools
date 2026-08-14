"""Route groups. Each registers itself onto the FastAPI app."""

from . import (
    architecture,
    crops,
    findings,
    model,
    observation_api,
    reports,
    sessions,
    streams,
)

__all__ = [
    "architecture",
    "crops",
    "findings",
    "model",
    "observation_api",
    "reports",
    "sessions",
    "streams",
]
