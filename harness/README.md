# Validation Harness

The bridge. An HTTP/WebSocket transport over Vision OS's public Observation API,
plus P1/P2 acquisition adapters that let a recorded video enter the pipeline as
if it were a camera.

**It modifies nothing.** `vosvc_harness/assembly.py` is the only module that
imports Vision OS, and it uses only public composition roots.

## Run

```bash
pip install -e ".[dev]"     # + [av] or [cv] for MP4/AVI/MKV
python -m vosvc_harness
```

## Why this exists

Vision OS binds two transports — `transport.in_process` and
`transport.recording` — and one frame source, `InMemoryRawSource`. The
architecture anticipates both extensions explicitly:

> *"An RTSP/WebRTC source and an NVDEC/QSV/VAAPI decoder are sibling adapters
> behind the same ports. **No platform module changes to add them** — that is the
> whole point of P1 and P2."*

> *"The day an HTTP adapter is written, it will implement the same three members
> and the API will not change."*

This package is those adapters.

## Modules

| Module | Responsibility |
|---|---|
| `assembly.py` | Boots L0–L7 through public composition roots. **The only importer of Vision OS.** |
| `contract.py` | Wire serialization. Reflective by design — it has no place to put an interpretation. |
| `session.py` | Session lifecycle, replay transport, the clock pump. |
| `taps.py` | Event Bus / Metrics / Health observability. Bounded rings. |
| `media.py` | Uploaded and discovered video, with explicit capability gaps. |
| `sources/decoding.py` | PyAV → OpenCV → none, degrading explicitly. |
| `sources/adapters.py` | `ReplayFileSource`, `RtspReplaySource`, `ValidationDecoder`. |
| `sources/faults.py` | The eleven scenarios, injected at the port boundary. |
| `routes/` | REST + WebSocket. |

## Three decisions worth knowing

**The pump owns time.** Vision OS runs on a `VirtualClock`, so nothing advances
unless `Session._pump` advances it. That is what makes replay deterministic — and
what makes the pump's `settle_cycles` necessary, so a stepped frame finishes
travelling the pipeline instead of stranding between layers.

**Buffer sizing is derived, not guessed.** `_buffer_sizing` computes slots from
the target frame rate and shortens the history window when the memory budget
cannot cover it. A fixed six-slot pool with a ten-second window stalled the
replay at frame 7; a buffer that promises more history than it can hold is a
stall waiting for a busy moment.

**A fault verdict needs an injection *and* a post-arming event.** Crediting a
scenario with an event that fired during startup produces a false pass, which is
the worst defect a validation tool can have. `FaultLedger` records when each
event type was last seen and compares against the arming sequence.

## Tests

```bash
pytest -q
```

34 unit tests run without Vision OS. 12 integration tests boot the real platform
and are **skipped with a stated reason** — never passed vacuously — when it is
not importable.
