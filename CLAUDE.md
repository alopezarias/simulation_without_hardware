# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository purpose

Hardwareless Python MVP for a conversational device. Three independent packages share one WebSocket protocol so the device contract, state machine, button UX, backend orchestration, streaming audio flow, and Raspberry Pi runtime packaging can all be exercised without physical hardware.

## Top-level layout

- `backend/` — FastAPI + WebSocket backend (sessions, protocol routing, speech pipeline, agent gateway).
- `simulator/` — CLI and Tkinter UI that emulate the device. Has its own `requirements.txt`.
- `device_runtime/` — Standalone Raspberry-oriented runtime, packaged independently (`pyproject.toml`, own `requirements-*.txt`, `scripts/`, `deploy/`). Only this folder is shipped to a Pi; backend stays on the PC.
- `docs/adr/` — Architecture decisions. ADRs are authoritative when in doubt; ADR-0003 is the current canonical interaction model.
- `RUNBOOK.md` — Detailed manual + automated validation flows. Long-form companion to `README.md`.

## Commands

All commands assume repo root and an active venv:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # installs backend + simulator + dev deps
cp .env.example .env
```

Backend (recommended launcher — binds to the current interpreter so STT/TTS deps resolve correctly):

```bash
python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Simulator clients (need backend running):

```bash
python -m simulator.entrypoints.cli --ws-url ws://127.0.0.1:8000/ws
python -m simulator.entrypoints.ui  --ws-url ws://127.0.0.1:8000/ws
```

Shared device runtime against the local backend (no hardware required):

```bash
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws \
  python -m device_runtime.entrypoints.raspi_main
```

### Tests

`pytest.ini` intentionally restricts the default suite to `backend/tests` and `device_runtime/tests`. The `simulator/tests` tree is excluded because it still references legacy interaction-model symbols removed from the canonical `device_runtime` state model — do not re-enable it without migrating those tests.

```bash
pytest                                     # default suite (backend + device_runtime)
pytest backend/tests/test_foo.py::test_bar # single test
pytest device_runtime/tests -k whisplay    # filter by keyword
```

Simulator QA (run against an already-running backend, not via `pytest`):

```bash
python -m simulator.qa.smoke_test       --ws-url ws://127.0.0.1:8000/ws
python -m simulator.qa.scenario_runner  --ws-url ws://127.0.0.1:8000/ws --scenario all
```

Scenario names: `locked-ready`, `listen-agents`, `cache-refresh`, `agent-ack`, `raspi-bootstrap`, `raspi-no-mic`, `raspi-no-display`, `raspi-reconnect`, `all`.

### Narrower installs (when you only touch one package)

```bash
pip install -r backend/requirements.txt
pip install -r simulator/requirements.txt
# device_runtime is a real package:
pip install -e device_runtime            # base
pip install -e 'device_runtime[dev]'     # with pytest
pip install -e 'device_runtime[raspi]'   # GPIO/ALSA/sounddevice extras
```

## Architecture

The repo follows a hexagonal split inside each of the three packages: `domain/` (state, events), `application/` (services, ports), `infrastructure/` (adapters: transport, audio, speech, display, input, power, rgb), and `entrypoints/`. The same shape repeats in `backend/`, `simulator/`, and `device_runtime/src/device_runtime/`.

**One protocol, three mirrors.** The WebSocket message contract is duplicated across packages so each can be deployed independently:

- `backend/shared/protocol.py` — backend-side enums + `validate_device_message`.
- `simulator/shared/protocol.py` — simulator-side mirror.
- `device_runtime/src/device_runtime/protocol/` — runtime-side mirror with codec/validation.

When changing message types or `UiState`, update all three plus tests; divergence here is the most common cross-package break.

**Canonical interaction model (ADR-0003).** Visible states are exactly: `standby`, `listening`, `calling`, `incoming_call`, `config`. Hold-to-talk is primary (`listening` only while the button is physically held); single click → outbound `calling`; double click → `config`; backend-initiated → `incoming_call`. Legacy names (`READY`, `MENU`, `MODE`, `AGENTS`, `idle`/`processing`/`speaking` as visible states) are obsolete — treat any code or doc referencing them as stale and migrate toward ADR-0003.

**Backend entrypoints.** `backend/run.py` is the recommended launcher (binds uvicorn to the current Python so speech deps resolve). `backend/api.py` is a compatibility facade kept for tests/scripts that monkeypatch module-level globals (`AUDIO_REPLY_MODE`, `ENABLE_FAKE_AUDIO`, etc.); the real wiring lives in `backend/bootstrap.py::create_app`, which composes `AppContext` (settings, `OpenClawdGateway`, `SpeechGateway`, audio stores) and registers `/ws`, `/health`, `/audio/{audio_id}`.

**Assistant gateway has three modes** controlled by `OPENCLAWD_MODE`: `mock` (default, fastest local loop), `http`, `ws`. See `OPENCLAWD_WS_SETUP.md` for the WS-mode field mapping.

**Speech pipeline is optional.** Whisper STT + local TTS load lazily; the backend logs a warning and continues if the optional deps aren't installed in the active interpreter. `AUDIO_REPLY_MODE=echo` short-circuits the assistant and returns the recognized transcript — useful for end-to-end speech-loop validation.

**`device_runtime` adapter selection** is driven by `DEVICE_*` env vars and degrades safely:

- Missing native libs → adapter falls back to `null_*` with a warning (runtime keeps booting).
- `DEVICE_HARDWARE_PROFILE=whisplay` is treated as one integrated bundle: it forces `DEVICE_DISPLAY_ADAPTER=whisplay`, `DEVICE_BUTTON_ADAPTER=whisplay`, auto-defaults `DEVICE_RGB_ADAPTER=hardware`, and **rejects** `DEVICE_BUTTON_ADAPTER=gpio` (GPIO17 conflicts with the vendor button on real hardware).
- Audio on the Whisplay/WM8960 path: use `DEVICE_AUDIO_IN_ADAPTER=alsa` + `DEVICE_AUDIO_OUT_ADAPTER=alsa` with both ALSA device vars set to `plughw:wm8960soundcard,0` (`plughw:` is more tolerant than raw `hw:`).
- Playback tuning knobs that matter on the Pi: `DEVICE_AUDIO_OUT_CHUNK_MS=200`, `DEVICE_AUDIO_OUT_START_BUFFER_MS=1000` (legacy alias `DEVICE_AUDIO_OUT_BUFFER_MS`).
- The runtime fails fast if `DEVICE_ID` or `DEVICE_WS_URL` is missing; it never runs backend orchestration locally.

**Raspberry deploy** is intentionally simple: copy `device_runtime/` to a final folder on the Pi (typically `~/device_runtime`), keep `.env` next to it, run `scripts/install_raspberry.sh` (sets up venv with `--system-site-packages` so apt-provided `spidev`/`RPi.GPIO`/`Pillow` stay visible, optionally installs PiSugar + systemd unit). `/etc/device-runtime/device-runtime.env` is a fallback only.

## Conventions worth knowing

- Default suite excludes simulator tests on purpose — do not "fix" `pytest.ini` to include them without first migrating those tests to the ADR-0003 state model.
- The three protocol mirrors (`backend/shared/`, `simulator/shared/`, `device_runtime/.../protocol/`) must stay in sync; changing one without the others will silently break a client.
- `backend/api.py` keeps module-level globals (e.g. `AUDIO_REPLY_MODE`) deliberately mutable for legacy tests; the canonical source of truth is `BackendSettings` in `backend/config/settings.py`.
- Spanish is used throughout ADRs and many docstrings; new ADRs should follow the same format (estado/fecha/alcance/audiencia header).
