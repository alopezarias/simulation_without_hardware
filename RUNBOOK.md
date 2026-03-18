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

# Raspberry-oriented adapter selection with Whisplay/WM8960 audio defaults
DEVICE_ID=raspi-dev DEVICE_WS_URL=ws://127.0.0.1:8000/ws DEVICE_HARDWARE_PROFILE=whisplay DEVICE_DISPLAY_ADAPTER=whisplay DEVICE_BUTTON_ADAPTER=whisplay DEVICE_AUDIO_IN_ADAPTER=alsa DEVICE_AUDIO_OUT_ADAPTER=alsa DEVICE_AUDIO_IN_ALSA_DEVICE=plughw:wm8960soundcard,0 DEVICE_AUDIO_OUT_ALSA_DEVICE=plughw:wm8960soundcard,0 DEVICE_POWER_ADAPTER=pisugar DEVICE_RGB_ADAPTER=hardware python -m device_runtime.entrypoints.raspi_main
```

Expected behavior:

- the entrypoint starts as long as `DEVICE_ID` and `DEVICE_WS_URL` are provided
- missing Raspberry-specific libraries degrade to safe `null_*` behavior with warnings
- simulator entrypoints continue to work independently of the shared runtime

## 5. Deploy only `device_runtime/` to a Raspberry Pi

The backend stays on your PC. The Pi only needs the standalone `device_runtime/` package and a reachable `DEVICE_WS_URL`.

The preferred flow is direct and manual: copy `device_runtime/` to its final folder on the Pi, keep a local `.env` inside that folder, and work from there. `/tmp` can still be used as transient staging, but it is no longer the main documented runtime location.

### Copy the package to the Pi

Manual copy:

```bash
rsync -az device_runtime/ pi@raspberrypi.local:~/device_runtime/
```

Repeatable helper from the development machine:

```bash
cd device_runtime
bash scripts/deploy_raspberry.sh \
  --pi pi@raspberrypi.local \
  --device-id raspi-01 \
  --device-ws-url ws://192.168.1.10:8000/ws
```

### Prepare and install on the Pi

If you copied manually, log into the Pi and run:

```bash
cd ~/device_runtime
cp -n .env.example .env
sudo bash scripts/install_raspberry.sh
```

This keeps the runtime in `~/device_runtime`, creates `~/device_runtime/.venv`, preserves `~/device_runtime/.env` as the primary config, and writes `/etc/systemd/system/device-runtime.service` if you run it with `sudo` on a systemd-based Pi.

### Configure `DEVICE_WS_URL`

Edit the local runtime env file:

```bash
nano ~/device_runtime/.env
```

Required fields:

```env
DEVICE_ID=raspi-01
DEVICE_WS_URL=ws://192.168.1.10:8000/ws
```

Optional but typical Raspberry values:

```env
DEVICE_HARDWARE_PROFILE=whisplay
DEVICE_DISPLAY_ADAPTER=whisplay
DEVICE_BUTTON_ADAPTER=whisplay
DEVICE_AUDIO_IN_ADAPTER=alsa
DEVICE_AUDIO_OUT_ADAPTER=alsa
DEVICE_AUDIO_IN_ALSA_DEVICE=plughw:wm8960soundcard,0
DEVICE_AUDIO_OUT_ALSA_DEVICE=plughw:wm8960soundcard,0
DEVICE_POWER_ADAPTER=pisugar
DEVICE_RGB_ADAPTER=hardware
DEVICE_WHISPLAY_DRIVER_PATH=~/Whisplay/Driver
DEVICE_WHISPLAY_BACKLIGHT=50
DEVICE_AUDIO_OUT_CHUNK_MS=200
DEVICE_AUDIO_OUT_START_BUFFER_MS=1000
```

When `DEVICE_HARDWARE_PROFILE=whisplay` is active, the runtime treats Whisplay as one integrated hardware bundle. It forces the Whisplay display path, routes button clicks through the vendor board with `DEVICE_BUTTON_ADAPTER=whisplay`, keeps RGB on the vendor controller by default, and rejects `DEVICE_BUTTON_ADAPTER=gpio` automatically because separate GPIO17 access conflicts with the vendor-owned button on real Raspberry hardware. For audio, the runtime now defaults Whisplay ALSA devices to `plughw:wm8960soundcard,0`, which is more tolerant than raw `hw:` for the integrated WM8960 codec.

Audio tuning knobs for the Pi:

- `DEVICE_AUDIO_SAMPLE_RATE`, `DEVICE_AUDIO_CHANNELS`, `DEVICE_AUDIO_CHUNK_MS` tune the runtime-side capture/upload contract.
- `DEVICE_AUDIO_IN_ALSA_DEVICE` and `DEVICE_AUDIO_OUT_ALSA_DEVICE` pick the exact ALSA endpoints.
- `DEVICE_AUDIO_IN_ALSA_PERIOD_SIZE` and `DEVICE_AUDIO_IN_ALSA_NONBLOCK` tune ALSA capture open/read behavior.
- `DEVICE_AUDIO_OUT_CHUNK_MS` controls how much assistant PCM the runtime aggregates per playback write; start with `200` on the Pi.
- `DEVICE_AUDIO_OUT_START_BUFFER_MS` controls how much assistant audio must be buffered before playback starts; start with `1000` on the Pi. The older `DEVICE_AUDIO_OUT_BUFFER_MS` name still works as an alias.
- `DEVICE_AUDIO_OUT_ALSA_PERIOD_SIZE` remains the lower-level ALSA playback knob if you need to experiment beyond the runtime defaults.

The current Raspberry UX tuning favors hardware-visible colors and a quieter screen layout: `ready` / `charging` = vivid green, `listening` = vivid yellow, `speaking` / `disconnected` = vivid blue, `error` = vivid red. On screen, the state stays top-left, battery top-right, and the center block carries the only main interaction text so the small panel does not feel crowded.

When your PC IP changes, update only `DEVICE_WS_URL` in `~/device_runtime/.env` and restart the runtime.

For the real Whisplay vendor stack validated on Raspberry Pi, keep the vendor repo on the Pi and point `DEVICE_WHISPLAY_DRIVER_PATH` at its `Driver/` folder. The launcher exports that path into `PYTHONPATH`, and the runtime also falls back to the real vendor module name `WhisPlay` / `WhisPlayBoard` if the legacy `whisplay` import is absent.

`scripts/install_raspberry.sh` now builds `~/device_runtime/.venv` with `--system-site-packages` by default so apt-installed packages used by the vendor stack (`spidev`, `RPi.GPIO`, `Pillow`) stay available inside the runtime venv.

Optional compatibility note: if you already have `/etc/device-runtime/device-runtime.env`, the launcher still accepts it as a fallback, but the primary path is now the local `.env` next to the runtime.

### Start manually

```bash
~/device_runtime/scripts/run_runtime.sh
```

### Enable and manage the `systemd` service

```bash
sudo systemctl enable device-runtime.service
sudo systemctl restart device-runtime.service
sudo systemctl status device-runtime.service
sudo journalctl -u device-runtime.service -f
```

### Quick real-Pi UX check

1. Confirm the top row shows state at left and battery at right with no extra chrome.
2. Press once from `Ready` and verify the LED flips to vivid yellow while the centered copy switches to listening guidance.
3. Trigger a backend reply and verify the LED turns vivid blue while the centered area prioritizes the assistant response.
4. Disconnect the backend or change `DEVICE_WS_URL` temporarily and verify the LED switches to vivid blue pulse plus an obvious offline message.
5. Force an error path if available and confirm the LED turns vivid red with the diagnostic centered clearly enough to read at arm's length.
6. Run `arecord -D plughw:wm8960soundcard,0 -f S16_LE -r 16000 -c 1 /tmp/test.wav` and `aplay -D plughw:wm8960soundcard,0 /tmp/test.wav` outside the runtime if transcription or playback still sounds wrong; if that path fails, fix ALSA/device-level issues before blaming the runtime.

### Run smoke checks on the Pi

Fast packaging/config check without network:

```bash
~/device_runtime/scripts/smoke_check.sh --skip-network --json
```

Packaging plus backend reachability check:

```bash
~/device_runtime/scripts/smoke_check.sh
```

Expected result:

```text
DEVICE RUNTIME SMOKE
- hello_type: device.hello
- network: tcp ok <pc-ip>:8000
```

If the TCP check fails, verify:

- the PC backend is running
- the Pi can reach the PC IP on the local network
- the backend is listening on the same host/port used in `DEVICE_WS_URL`
- firewall rules are not blocking the connection

## 6. Manual validation scenarios

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

## 7. Automated validation

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

Package-local Raspberry smoke helpers:

```bash
cd device_runtime
pytest tests/test_runtime_packaging.py tests/test_runtime_foundation.py
bash scripts/install_raspberry.sh
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

## 8. Optional device auth

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

## 9. Optional OpenClawd integration modes

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

## 10. Scope note

This runbook documents what is practical today: standalone Raspberry deploy/install/run/service workflows plus simulated validation. Real Pi verification is still required for exact display timings, PiSugar source availability, and RGB hardware characteristics.
