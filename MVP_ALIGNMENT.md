# MVP Alignment

This repository is a hardwareless MVP for a conversational device. It is designed to validate the protocol, interaction model, backend orchestration, simulator UX, and early Raspberry Pi runtime packaging before committing to a final hardware build.

The comparison below reflects the broader conversational-device target the project is moving toward, while staying grounded in what is implemented in this repository today.

## What the MVP already demonstrates well

| Area | Status | Notes |
| --- | --- | --- |
| Thin device + remote backend architecture | Yes | The repository cleanly separates device-facing clients from the backend services that manage sessions, orchestration, and responses. |
| Bidirectional WebSocket transport | Yes | Simulator and shared runtime connect over WebSocket and exercise the device protocol end to end. |
| Core device state model | Yes | The MVP covers `idle`, `listening`, `processing`, `speaking`, and `error` state transitions. |
| Button interaction semantics | Yes | `Tap`, `Double Tap`, `Long Press`, interruption, and cancellation behavior are implemented and testable. |
| Agent selection flow | Yes | The backend and simulator support agent switching through `agent.select` and `agent.selected`. |
| Streaming assistant text | Yes | The protocol supports partial and final assistant responses through `assistant.text.partial` and `assistant.text.final`. |
| Device UX preview | Yes | The desktop simulator exposes a useful stand-in for display, LED, connection state, and traffic inspection. |
| Basic observability | Yes | The project includes protocol logging, scenario-based QA, smoke checks, and turn-level validation flows. |
| Basic device auth | Yes | Optional token and allowlist controls exist for device connection gating. |

## What is present, but still MVP-level

| Area | Status | Notes |
| --- | --- | --- |
| Audio input over the protocol | Partial | `audio.chunk` is implemented, but text-driven flows remain the quickest and most stable path for general local testing. |
| Audio output streaming | Partial | `assistant.audio.start`, `assistant.audio.chunk`, and `assistant.audio.end` exist, with local playback paths intended for MVP validation rather than production audio tuning. |
| Local STT and TTS loop | Partial | Whisper STT and local TTS are available for hardwareless validation, but they should be viewed as development scaffolding rather than a final production speech stack. |
| Raspberry-oriented runtime packaging | Partial | `device_runtime/` proves that the reusable runtime can boot and degrade safely when hardware-specific libraries are unavailable. |
| OpenClawd integration | Partial | HTTP and WebSocket adapters exist, but the default and easiest path remains local `mock` mode. |

## What remains outside current scope

| Area | Status | Notes |
| --- | --- | --- |
| Final physical device integration | Not yet | Real GPIO, display, button, LED, and enclosure integration are not the focus of this repository stage. |
| Production-grade embedded hardening | Not yet | Service supervision, watchdog behavior, long-running recovery, and deployment hardening remain future work. |
| Final hardware audio path | Not yet | Real microphone and speaker behavior on the target hardware still needs dedicated device-side validation. |
| Production speech stack decisions | Not yet | The repository validates the conversation loop, but does not lock in the final STT/TTS vendors, latency profile, or deployment architecture. |

## Why this MVP is still useful

Even without final hardware, the project already reduces risk in the parts that are easiest to get wrong early:

- the message contract between device and backend
- the turn-taking state machine
- button-driven interaction semantics
- agent routing and streaming response behavior
- the handoff between simulator logic and a reusable Raspberry-style runtime

In other words, this MVP is not a substitute for hardware integration. It is a way to enter hardware integration with the protocol, interaction model, and software boundaries already exercised.
