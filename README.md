# Vision OS Validation Console

### The permanent engineering tool for validating UnityWorks Vision OS

| | |
|---|---|
| **Purpose** | Engineering Validation System. Not a production feature. |
| **Validates** | UnityWorks Vision OS v1 — Flows 1–8, layers L0–L7 |
| **Couples to Vision OS via** | REST + WebSocket only. The console imports no backend code. |
| **Writes to Vision State** | Nothing. There is no write path to disable. |

---

## What this is

Engineers point this at a prerecorded CCTV video and watch it travel every layer
of Vision OS — acquisition, detection, tracking, registry, cropping,
understanding, synthesis, state, API — one frame at a time, with every
intermediate output inspectable and every observation traceable to its evidence.

It exists to answer one question before Vision OS is integrated into the
Cognitive Operating System: **does the platform do what its architecture says it
does?**

```
 uploaded video ──► Validation Harness ──► Vision OS (unmodified)
                    (P1/P2 adapters)         │
                                             ▼
                    Harness taps ◄──── Event Bus · Metrics · Health · M14 API
                          │
                    REST + WebSocket
                          │
                          ▼
                  Validation Console (React)
```

---

## Why there are two pieces

Vision OS's Observation API is an **in-process Python object**. Its transport
catalogue binds `transport.in_process` and `transport.recording` — there is no
HTTP surface, and P1 binds only an in-memory frame source, so there is no path
for an MP4 to enter the pipeline.

Both absences are anticipated by the architecture, which says of P1/P2 that a
file source and a hardware decoder are *"sibling adapters behind the same ports.
**No platform module changes to add them**"*, and of P32 that *"the day an HTTP
adapter is written, it will implement the same three members and the API will not
change."*

The **Validation Harness** is those adapters. It lives in this repository, is
built entirely on Vision OS's public composition roots, and modifies nothing.
`harness/vosvc_harness/assembly.py` is the only file in the repo that imports
Vision OS at all.

---

## Quick start

```bash
# 1. Harness — needs the backend's Python environment
cd harness
pip install -e ".[dev,av]"        # [av] is what lets it open MP4/AVI/MKV
python -m vosvc_harness           # http://127.0.0.1:8808

# 2. Console
npm install
npm run dev                       # http://localhost:5273
```

> **Install the `av` extra unless you have a reason not to.** Without a codec the
> harness runs fine but every `.mp4` you upload is listed as *unusable* — which
> is correct behaviour (it refuses to pretend it opened a file it cannot read),
> but it is not what you want on day one. `GET /api/v1/health` reports
> `media.backends`; anything other than `raw` alone means containers will open.

Open the console, pick **synthetic-moving-target** (always present, needs no
codec), press **Open session**, then **play**. Every panel fills.

To replay real footage, install a decode backend and upload a file:

```bash
pip install -e "harness[av]"     # PyAV — preferred
pip install -e "harness[cv]"     # or OpenCV
```

Without one, the harness reports `mp4: unavailable` in `GET /api/v1/health`
rather than silently returning zero frames. **A tool that mis-reports its own
blindness cannot be trusted to report the platform's.**

---

## Configuration

Every setting defaults to the safe choice.

| Variable | Default | Meaning |
|---|---|---|
| `VOSVC_PORT` | `8808` | Harness port |
| `VOSVC_VISION_OS_ROOT` | `../backend` | Where `app.vision_os` is imported from |
| `VOSVC_MEDIA_ROOT` | `./media` | Uploaded video |
| `VOSVC_SERVE_FRAMES` | `0` | **Off.** Pixels stay local (V12). When on, every frame fetch needs a declared purpose and is audited. |
| `VOSVC_ALLOW_EVIDENCE` | `0` | **Off.** Evidence is a separately-privileged act (12_SECURITY §5.3). |

Neither of the last two is toggleable from the console. They are deployment
decisions, which is the only place they can honestly live.

---

## What you get

**Fourteen inspection panels** — Live Video · Frame Information · Detections ·
Tracks · Visual Objects · Canonical Crops · Understanding Results · Observations ·
Vision State · Observation Log · State Events · Demand Registry · Camera ·
Health · Architecture Events.

**Pipeline view** — all ten layers with live traffic counts, and a live ordering
check. A layer producing output before the layer that feeds it would mean a
bypass, and the console says so.

**Timeline** — pause, resume, step, jump, scrub, replay, compare. A *step* runs
the frame through the **whole** pipeline; it does not move a playhead over
pre-computed results.

**State inspector** — object lifecycle, attributes with evidence refs,
provenance, confidence, coverage. No hidden state.

**Performance dashboard** — every metric from the platform's closed vocabulary,
grouped per module, with the real sweep cadence shown so a stall is visible
rather than assumed.

**Failure injection** — eleven scenarios, each injected at the P1 source or P2
decoder, never inside a Vision OS module. Each declares the architectural
response it expects and is reported `unvalidated` when that response does not
arrive.

**Architecture validation** — module health, pipeline ordering, ownership
transfer, port bindings, Semantic Ceiling suspects, evidence traceability, V13
determinism.

**Eight reports** — replay, performance, observation, architecture, failure,
latency, regression, validation summary. All exportable as JSON.

---

## The rules this console holds itself to

These are enforced by `tests/architecture/constraints.test.ts`, not by convention.

1. **No backend imports.** Every module in `src/` is walked; any specifier
   resolving outside the repo fails the build.
2. **No business logic.** No threshold comparison against a domain attribute, no
   derived semantic verdict. The console may compare a value to a value it was
   *given*; it may not decide what a value means.
3. **No fabricated facts.** `src/simulator/` is test-only, is not imported by any
   application module, and is asserted absent from `dist/`.
4. **Nothing inferred that arrives as a field.** Retryability, staleness and
   coverage are all fields. Deriving any of them locally is a test failure.
5. **No write path.** `PUT` and `PATCH` appear nowhere; no mutating method exists
   on the client to call.

---

## Absence is never ambiguous

The single design rule that shaped this console:

> **V8 — Blindness is explicit.** *Absence of observation ≠ observation of
> absence.*

So "nothing was there" and "we could not look" use **different components**,
different colours, and different words, everywhere:

- `<EmptyResult>` — the platform looked and found nothing. A complete answer.
- `<Unavailable>` — the platform could not be asked, or could not answer. Nothing
  may be concluded from it.

A state result arriving without its coverage report renders `COVERAGE MISSING` in
red, because the platform returns coverage unconditionally and its absence is a
contract violation rather than a default.

---

## Tests

```bash
npm test                    # 69 tests, 8 console suites
npm run test:architecture   # the constraint suite above
cd harness && pytest        # 76 harness tests, 42 against real Vision OS
python scripts/verify_untouched.py   # prove nothing upstream changed
```

| Suite | Covers |
|---|---|
| `component` | Display primitives; the absence vocabulary |
| `integration` | The wire contract end to end through the real client |
| `replay` | Determinism reporting; refusal to compare incomparable runs |
| `ui` | Panels rendered against contract shapes |
| `websocket` | Sequencing and gap detection |
| `performance` | Buffer and stream scaling under 100k messages |
| `regression` | Contract shape pinned against silent field drift |
| `architecture` | The five rules above, read from source |

---

## Repository layout

```
vision_os_validation_console/
├── docs/
│   ├── CONTRACT.md              the frozen wire contract — read this first
│   ├── VERIFICATION.md          the final verification record
│   └── manifest-*.json          hash manifests proving upstream is untouched
├── harness/                     Python — the HTTP/WS + P1/P2 adapters
│   └── vosvc_harness/
│       ├── assembly.py          THE ONLY FILE THAT IMPORTS VISION OS
│       ├── contract.py          shape translation, zero interpretation
│       ├── session.py           replay lifecycle and the pump
│       ├── taps.py              per-layer observability
│       ├── routes/              REST + WebSocket
│       └── sources/             P1 sources, P2 decoder, fault injection
├── src/                         React + TypeScript console
├── tests/                       eight suites
└── scripts/verify_untouched.py
```

---

## Relationship to the rest of Atlas

Nothing depends on this repository, and it depends on the backend only as a
**read-only import path** at harness startup. Deleting it changes nothing about
Vision OS or the production frontend.

No production UI component is reused. The console's theme, primitives and state
layer are its own, and the two are allowed to diverge forever.

**The Vision OS Constitution always wins.** Where this tool and the architecture
disagree, the architecture is right and this tool has a bug.
