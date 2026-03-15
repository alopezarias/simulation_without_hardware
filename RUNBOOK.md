# Runbook

This runbook is the practical companion to `README.md`. It is meant for someone inspecting the repository on GitHub and wanting to reproduce the MVP locally: start the backend, run the simulator, exercise the shared device runtime, and validate the current test coverage.

All commands assume you are running from the repository root.

## What this runbook covers

- local environment setup
- backend startup and health checks
- simulator CLI and desktop UI flows
- shared `device_runtime/` bootstrap without physical hardware
- manual validation scenarios for the conversational-device MVP
- automated checks and optional integration modes

## 1. Prepare the environment

Create a virtual environment and install the development dependencies used across the repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

If you only need part of the repository, these narrower installs are also available:

```bash
pip install -r backend/requirements.txt
pip install -r simulator/requirements.txt
```

If you plan to use microphone input on macOS, install PortAudio first:

```bash
brew install portaudio
```

Notes:

- The default `.env.example` enables local Whisper STT and local TTS.
- The first Whisper transcription downloads the configured model from `WHISPER_MODEL_SIZE` (default: `base`).

## 2. Start the backend

Standard local run:

```bash
source .venv/bin/activate
python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

Simple audio-to-text-to-audio loopback using the local speech pipeline:

```bash
source .venv/bin/activate
ENABLE_WHISPER_STT=true ENABLE_LOCAL_TTS=true AUDIO_REPLY_MODE=echo python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

If local TTS on macOS has trouble with `pyttsx3`, force the native `say` backend:

```bash
source .venv/bin/activate
ENABLE_LOCAL_TTS=true TTS_BACKEND=say AUDIO_REPLY_MODE=echo python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

## 3. Run a client

CLI simulator:

```bash
source .venv/bin/activate
python -m simulator.entrypoints.cli --ws-url ws://127.0.0.1:8000/ws
```

Useful CLI commands once connected:

```text
help
state
tap
double
text hello this is a test
send
long
quit
```

Desktop simulator UI:

```bash
source .venv/bin/activate
python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws
```

The UI is the closest preview of the intended device experience in this repository. It exposes button interactions, text submission, microphone controls, transport tracing, and streaming response state in one window.

## 4. Bootstrap the shared runtime without hardware

The repository also includes a reusable runtime intended for Raspberry Pi-oriented deployment. You can bootstrap it locally without real device peripherals:

```bash
source .venv/bin/activate
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws python -m device_runtime.entrypoints.raspi_main
```

Useful variants:

```bash
# development-friendly shared adapters
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws DEVICE_BUTTON_ADAPTER=keyboard DEVICE_AUDIO_IN_ADAPTER=sounddevice DEVICE_AUDIO_OUT_ADAPTER=sounddevice python -m device_runtime.entrypoints.raspi_main

# Raspberry-oriented adapter selection with safe degradation when host libraries are missing
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws DEVICE_DISPLAY_ADAPTER=whisplay DEVICE_BUTTON_ADAPTER=gpio DEVICE_AUDIO_IN_ADAPTER=alsa DEVICE_AUDIO_OUT_ADAPTER=alsa python -m device_runtime.entrypoints.raspi_main
```

Expected behavior:

- the entrypoint starts as long as `DEVICE_ID` and `DEVICE_WS_URL` are provided
- missing Raspberry-specific libraries degrade to safe `null_*` behavior with warnings
- simulator entrypoints continue to work independently of the shared runtime

## 5. Manual validation scenarios

These checks are useful when confirming that the hardwareless MVP still behaves like the intended conversational device.

### Session handshake

1. Start the backend.
2. Launch the UI with `python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws`.
3. Confirm the UI reaches `connected`, shows a `session_id`, and settles in `idle`.

### Agent switching

1. From `idle`, trigger `Double Tap`.
2. Confirm the active agent changes and an `agent.selected` event arrives.

### Text turn

1. Trigger `Tap` to enter `listening`.
2. Enter debug text and submit it.
3. Trigger `Tap` again to close the turn.
4. Confirm a full cycle: transcript, streaming assistant text, final response, and return to `idle`.

### Microphone turn

1. Refresh the microphone list and select a real input device.
2. Trigger `Tap` to enter `listening` and begin capture.
3. Speak for a few seconds.
4. Trigger `Tap` again to close the turn and send audio.
5. Confirm:

```text
- multiple audio.chunk messages are transmitted
- backend logs report received audio chunks
- a transcript final event is produced by Whisper
- assistant.audio.start/chunk/end events are returned when audio output is enabled
- the UI counters and audio indicators move as expected
```

### Echo-mode speech loop

1. Start the backend in `AUDIO_REPLY_MODE=echo`.
2. Open the UI and complete a microphone turn.
3. Confirm:

```text
- transcript.final contains the recognized speech
- assistant.text.final mirrors that text in echo mode
- assistant.audio.* events are emitted for local playback
```

### Interrupt and cancel behavior

- While the assistant is `speaking`, trigger `Tap` or `Long Press` to verify interruption back to `idle`.
- While `listening`, trigger `Long Press` to verify cancellation without an assistant reply.

### If the microphone is not detected

1. On macOS, grant microphone permission to the terminal or IDE in System Settings.
2. Restart the terminal session.
3. Refresh the microphone list in the UI.
4. If detection still fails, inspect the UI's microphone error field.

## 6. Automated validation

Run the full Python test suite:

```bash
source .venv/bin/activate
pytest
```

`pytest.ini` is configured so bare `pytest` runs `backend/tests`, `simulator/tests`, and `device_runtime/tests`.

Run the simulator smoke test against an already running backend:

```bash
source .venv/bin/activate
python -m simulator.qa.smoke_test --ws-url ws://127.0.0.1:8000/ws
```

Expected result:

```text
SMOKE TEST PASSED
```

Run the scenario runner against an already running backend:

```bash
source .venv/bin/activate
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario all
```

Available scenario names at the time of writing:

```text
locked-ready
listen-agents
cache-refresh
agent-ack
raspi-bootstrap
raspi-no-mic
raspi-no-display
raspi-reconnect
all
```

Example single-scenario runs:

```bash
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario raspi-bootstrap
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario listen-agents
```

Save a JSON report:

```bash
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario all --report /tmp/sim_report.json
```

## 7. Optional device auth

Set the backend-side auth values in `.env`:

```env
SIM_DEVICE_AUTH_TOKEN=my-token
SIM_ALLOWED_DEVICE_IDS=sim-device-001,sim-device-ui-001,sim-smoke-001
```

Then pass the same token from the simulator or smoke test:

```bash
python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws --auth-token my-token
python -m simulator.entrypoints.cli --ws-url ws://127.0.0.1:8000/ws --auth-token my-token
python -m simulator.qa.smoke_test --ws-url ws://127.0.0.1:8000/ws --auth-token my-token
```

## 8. Optional OpenClawd integration modes

### WebSocket mode over an SSH tunnel

Open an SSH tunnel, for example:

```bash
ssh -i ~/.ssh/your_key -N -L 8765:127.0.0.1:8765 user@your-vps
```

Then configure `.env`:

```env
OPENCLAWD_MODE=ws
OPENCLAWD_WS_URL=ws://127.0.0.1:8765/ws
OPENCLAWD_WS_BEARER_TOKEN=
OPENCLAWD_WS_HEADERS={}
OPENCLAWD_WS_EXTRA_PAYLOAD={}
OPENCLAWD_WS_REQUEST_TYPE=
OPENCLAWD_WS_AGENT_FIELD=agent_id
OPENCLAWD_WS_INPUT_FIELD=input
OPENCLAWD_WS_SESSION_FIELD=session_id
OPENCLAWD_WS_PARTIAL_TYPES=assistant.text.partial,partial,delta,response.chunk
OPENCLAWD_WS_FINAL_TYPES=assistant.text.final,final,done,response.final
```

Restart the backend and repeat the text-turn and interruption scenarios.

### HTTP mode

If your OpenClawd deployment exposes HTTP:

```env
OPENCLAWD_MODE=http
OPENCLAWD_BASE_URL=https://your-endpoint
OPENCLAWD_CHAT_ENDPOINT=/api/chat
OPENCLAWD_API_KEY=
```

## 9. Scope note

This runbook documents what is already practical in the repository today. It does not assume production-ready hardware adapters or final embedded deployment hardening.
