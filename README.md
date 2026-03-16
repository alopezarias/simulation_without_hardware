# Simulation without Hardware

Python MVP for a conversational device before real hardware exists. The repository lets you exercise the device protocol, state machine, button UX, backend orchestration, streaming audio flow, and Raspberry Pi runtime packaging using a local simulator instead of a physical build.

## Why this project is interesting

- It validates a hardware-oriented product with software-only tooling.
- It keeps backend, simulator, and device runtime concerns separated while sharing the same protocol and state model.
- It supports both fast local iteration (`mock` agent mode) and more realistic integrations (`http` and `ws` OpenClawd adapters).
- It already includes CLI, desktop UI, smoke tests, scenario-based QA, and a Raspberry/device runtime entrypoint.

## What is inside

### Main pieces

| Area | Purpose |
| --- | --- |
| `backend/` | FastAPI + WebSocket backend that manages sessions, protocol events, speech pipeline, agent routing, and device-facing responses. |
| `simulator/` | CLI and Tkinter UI that emulate the device interactions, transport, and local audio behavior. |
| `device_runtime/` | Standalone Raspberry runtime package with its own protocol boundary, Whisplay/PiSugar/RGB adapters, deploy scripts, and `systemd` assets. |
| `docs/` | ADRs and supporting design notes. |

### Core capabilities today

- Session handshake: `device.hello` -> `session.ready`
- Device interaction semantics: `Tap`, `Double Tap`, `Long Press`, interrupt, and cancel
- Agent switching through `agent.select` / `agent.selected`
- Streaming responses with `assistant.text.partial` and `assistant.text.final`
- Audio input through `audio.chunk`
- Audio response streaming through `assistant.audio.start`, `assistant.audio.chunk`, and `assistant.audio.end`
- Local speech loop with Whisper STT and local TTS
- Optional basic device auth with token + allowlist
- Shared runtime bootstrapping for non-hardware and Raspberry-style setups

## Architecture at a glance

The repository follows a hexagonal direction.

- `backend/` is organized into `domain`, `application`, `infrastructure`, and `config`, with `backend/run.py` as the recommended launcher and `backend/api.py` as the compatibility entrypoint.
- `simulator/` contains domain/application/infrastructure layers plus executable entrypoints for the CLI and desktop UI.
- `device_runtime/` concentrates reusable runtime services and adapters for audio, input, and display, including development-friendly adapters and Raspberry Pi scaffolding that degrades safely when host libraries are missing.

In practice, the flow is:

1. A simulator or runtime connects to the backend over WebSocket.
2. The backend manages session state and routes device events.
3. User input can travel as debug text or audio chunks.
4. The backend produces streaming text and optional audio responses.
5. The simulator UI or runtime renders the result as if it were the device.

## Quick start

### 1. Create an environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

If you want microphone support on macOS, install PortAudio first:

```bash
brew install portaudio
```

### 2. Start the backend

```bash
python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

### 3. Run one of the clients

CLI simulator:

```bash
python -m simulator.entrypoints.cli --ws-url ws://127.0.0.1:8000/ws
```

Desktop simulator UI:

```bash
python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws
```

Shared runtime bootstrap from the monorepo:

```bash
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws python -m device_runtime.entrypoints.raspi_main
```

Standalone Raspberry package flow:

```bash
cd device_runtime
cp .env.example .env
sudo bash scripts/install_raspberry.sh
nano .env
./scripts/run_runtime.sh
sudo systemctl enable device-runtime.service
sudo systemctl restart device-runtime.service
```

## Common workflows

### Run the full test suite

```bash
pytest
```

`pytest.ini` is configured so bare `pytest` covers `backend/tests`, `simulator/tests`, and `device_runtime/tests`.

### Run automated simulator QA

Smoke test with a backend already running:

```bash
python -m simulator.qa.smoke_test --ws-url ws://127.0.0.1:8000/ws
```

Scenario runner:

```bash
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario all
```

### Use the local speech loop

The default `.env.example` enables local Whisper STT and local TTS. For a simple audio-to-text-to-audio loopback, run the backend in `echo` mode:

```bash
ENABLE_WHISPER_STT=true ENABLE_LOCAL_TTS=true AUDIO_REPLY_MODE=echo python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

### Switch installation scope when needed

Backend only:

```bash
pip install -r backend/requirements.txt
```

Simulator only:

```bash
pip install -r simulator/requirements.txt
```

## Repository layout

```text
.
|- backend/          FastAPI backend, protocol services, speech pipeline, tests
|- simulator/        CLI/UI simulator, QA runners, tests
|- device_runtime/   Shared device runtime and Raspberry-oriented adapters
|- docs/             ADRs and design notes
|- RUNBOOK.md        Detailed operational walkthroughs
|- OPENCLAWD_WS_SETUP.md
|- MVP_ALIGNMENT.md
|- pytest.ini
|- requirements*.txt
```

## Raspberry Pi deployment notes

- On the Pi, only `device_runtime/` is deployed; the backend stays on the user's PC.
- The only required network binding is `DEVICE_WS_URL=ws://<pc-ip>:8000/ws` or `wss://...`.
- The happy path is manual and direct: copy `device_runtime/` to a clear final folder on the Pi such as `~/device_runtime`, keep `~/device_runtime/.env`, and run from there.
- `device_runtime/scripts/deploy_raspberry.sh` can still use `/tmp` as staging for a repeatable remote copy/install/configure/restart flow, but it leaves the final runtime in a persistent folder with local config.
- `device_runtime/scripts/smoke_check.sh` verifies installed-package bootstrap and optional TCP reachability to the configured backend.
- `/etc/device-runtime/device-runtime.env` is now optional compatibility fallback, not the primary documented path.
- `wakeword`, `camera`, and `whisplay-im` remain intentionally excluded from this runtime package.

Useful environment variables:

```env
DEVICE_HARDWARE_PROFILE=auto|generic|whisplay
DEVICE_DISPLAY_ADAPTER=null|whisplay
DEVICE_BUTTON_ADAPTER=null|keyboard|gpio|whisplay
DEVICE_AUDIO_IN_ADAPTER=null|sounddevice|alsa
DEVICE_AUDIO_OUT_ADAPTER=null|sounddevice|alsa
DEVICE_FAIL_FAST_ON_MISSING_BUTTON=false
DEVICE_POWER_ADAPTER=pisugar
DEVICE_RGB_ADAPTER=hardware
DEVICE_WHISPLAY_DRIVER_PATH=~/Whisplay/Driver
```

On the real Whisplay Raspberry target, the recommended baseline is `DEVICE_HARDWARE_PROFILE=whisplay`, `DEVICE_BUTTON_ADAPTER=whisplay`, and audio adapters left at `null` unless you have explicitly validated an external non-conflicting path. With that profile active, `DEVICE_DISPLAY_ADAPTER=whisplay` is enforced, the button comes from the vendor board instead of GPIO17, and `DEVICE_RGB_ADAPTER=hardware` becomes the safe integrated default.

## Current status

This is an MVP focused on reducing risk before hardware integration.

- Strong coverage: protocol contract, device states, button semantics, simulator UX, backend orchestration, local speech pipeline, and automated scenario testing.
- Partial/iterative areas: final real-device validation on Pi hardware, exact PiSugar/RGB vendor nuances, and broader production hardening.

`MVP_ALIGNMENT.md` documents the current scope against the broader conversational-device vision.

## Documentation map

- `RUNBOOK.md` - step-by-step setup, manual checks, and test flows
- `OPENCLAWD_WS_SETUP.md` - WebSocket/OpenClawd integration details
- `MVP_ALIGNMENT.md` - what this MVP covers and what remains outside scope
- `docs/adr/ADR-0001-foundations-and-evolution.md` - architectural foundations and evolution
- `docs/adr/ADR-0002-simulator-testing-and-hexagonal-plan.md` - simulator testing strategy and refactor direction

## Notes for first-time visitors

- Start with `mock` mode if you just want to understand the protocol and UI behavior quickly.
- Use the desktop UI when you want the closest preview of the intended device experience.
- Use the shared runtime entrypoint when validating packaging and adapter behavior closer to Raspberry deployment.
