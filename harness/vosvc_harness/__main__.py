"""Run the harness.

    python -m vosvc_harness

Environment (all optional, all defaulting to the safe choice):

    VOSVC_HOST              127.0.0.1
    VOSVC_PORT              8808
    VOSVC_VISION_OS_ROOT    ../backend
    VOSVC_MEDIA_ROOT        ./media
    VOSVC_SERVE_FRAMES      0   pixels stay local by default (V12)
    VOSVC_ALLOW_EVIDENCE    0   evidence is separately privileged (12_SECURITY §5.3)
"""

from __future__ import annotations

import sys

from .app import create_app
from .config import load_config


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required: pip install -e harness[dev]", file=sys.stderr)
        return 1

    config = load_config()
    app = create_app(config)

    banner = [
        "",
        "  Vision OS Validation Harness",
        f"  http://{config.host}:{config.port}/api/v1/health",
        f"  vision_os_root : {config.vision_os_root}",
        f"  media_root     : {config.media_root}",
        f"  serve_frames   : {config.serve_frames}  (V12 — pixels stay local unless enabled)",
        f"  allow_evidence : {config.allow_evidence}",
        "",
    ]
    print("\n".join(banner))

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
