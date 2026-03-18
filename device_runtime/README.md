# Device Runtime

Standalone Raspberry runtime package for `simulation_without_hardware`.

This package owns the Raspberry-side boundary. On the Pi it runs only `device_runtime`, connects to the PC backend through `DEVICE_WS_URL`, and keeps Whisplay-like UI, PiSugar, and RGB inside the runtime package. `wakeword`, `camera`, and `whisplay-im` stay out of scope.

The preferred operator flow is intentionally simple: copy this folder to a clear final location on the Pi, keep a local `.env` inside that folder, and run it directly from there. `/etc/device-runtime/device-runtime.env` remains only as an optional compatibility fallback.

## Included deploy assets

- `scripts/install_raspberry.sh` prepares the copied runtime folder itself (or another target folder if `DEVICE_RUNTIME_INSTALL_ROOT` is set), creates the virtualenv, seeds the local `.env`, and optionally installs the `systemd` unit.
- `scripts/run_runtime.sh` is the manual launch wrapper used both by operators and by `systemd`.
- `scripts/deploy_raspberry.sh` provides a repeatable copy/install/configure/restart flow from the development machine.
- `scripts/smoke_check.sh` runs `device-runtime-smoke` on the Pi to verify packaging, config loading, and optional network reachability.
- `deploy/device-runtime.service` starts the runtime automatically at boot.

## Raspberry operator flow

1. Copy this `device_runtime/` directory to the Raspberry Pi, ideally as `~/device_runtime`.
2. Enter the folder and run the installer on the Pi:

   ```bash
   cd ~/device_runtime
   sudo bash scripts/install_raspberry.sh
   ```

3. Edit `~/device_runtime/.env` and set at least:
    - `DEVICE_ID=raspi-01`
    - `DEVICE_WS_URL=ws://<IP-DE-TU-PC>:8000/ws`
    - `DEVICE_HARDWARE_PROFILE=whisplay`
    - `DEVICE_AUDIO_IN_ADAPTER=alsa`
    - `DEVICE_AUDIO_OUT_ADAPTER=alsa`
    - `DEVICE_AUDIO_IN_ALSA_DEVICE=plughw:wm8960soundcard,0`
    - `DEVICE_AUDIO_OUT_ALSA_DEVICE=plughw:wm8960soundcard,0`
    - `DEVICE_AUDIO_OUT_CHUNK_MS=200`
    - `DEVICE_AUDIO_OUT_START_BUFFER_MS=1000`
    - `DEVICE_WHISPLAY_DRIVER_PATH=~/Whisplay/Driver` when using the real vendor screen/RGB stack
4. Start manually:

   ```bash
   ~/device_runtime/scripts/run_runtime.sh
   ```

5. Enable auto-start:

   ```bash
   sudo systemctl enable device-runtime.service
   sudo systemctl restart device-runtime.service
   sudo journalctl -u device-runtime.service -f
   ```

6. Run smoke verification on the Pi:

   ```bash
   ~/device_runtime/scripts/smoke_check.sh
   ~/device_runtime/scripts/smoke_check.sh --skip-network --json
   ```

## One-command deploy from the development machine

From inside `device_runtime/` on your Mac/Linux PC:

```bash
bash scripts/deploy_raspberry.sh \
  --pi pi@raspberrypi.local \
  --device-id raspi-01 \
  --device-ws-url ws://192.168.1.10:8000/ws
```

That flow may still use `/tmp` as staging, but it leaves the final runtime in a clear folder such as `/home/pi/device_runtime`, writes the local `.env` there, enables the `systemd` service, and restarts it.

## Expected startup contract

- `DEVICE_WS_URL` must be an explicit `ws://` or `wss://` URL pointing to the PC backend.
- `DEVICE_HARDWARE_PROFILE=whisplay` treats Whisplay as an integrated bundle. In that mode the runtime forces `DEVICE_DISPLAY_ADAPTER=whisplay`, `DEVICE_BUTTON_ADAPTER=whisplay`, auto-defaults `DEVICE_RGB_ADAPTER=hardware`, and rejects a separate `DEVICE_BUTTON_ADAPTER=gpio` because GPIO17 conflicts with the vendor-owned button on real Raspberry hardware.
- The Whisplay hardware palette is intentionally brighter on real Pi hardware: `ready`/`charging` use vivid green, `listening` uses vivid yellow, `speaking`/`disconnected` use vivid blue, and `error` uses vivid red.
- The physical screen layout is now reduced to the essentials for the small panel: runtime state top-left, battery top-right, and the main interaction copy centered with minimal footer noise.
- For the validated Raspberry setup, keep `DEVICE_BUTTON_ADAPTER=whisplay`, use `DEVICE_AUDIO_IN_ADAPTER=alsa` and `DEVICE_AUDIO_OUT_ADAPTER=alsa`, and point both ALSA device vars to `plughw:wm8960soundcard,0` so the runtime opens the WM8960 codec through ALSA's conversion layer instead of the stricter raw `hw:` path.
- `DEVICE_AUDIO_SAMPLE_RATE`, `DEVICE_AUDIO_CHANNELS`, and `DEVICE_AUDIO_CHUNK_MS` control capture/upload pacing. Playback is tuned separately with `DEVICE_AUDIO_OUT_CHUNK_MS` (recommended `200`) and `DEVICE_AUDIO_OUT_START_BUFFER_MS` (recommended `1000`) so the Pi waits for about 1 second of assistant audio before starting to play larger chunks.
- `DEVICE_AUDIO_IN_ALSA_PERIOD_SIZE`, `DEVICE_AUDIO_IN_ALSA_NONBLOCK`, and `DEVICE_AUDIO_OUT_ALSA_PERIOD_SIZE` remain lower-level ALSA tuning knobs. The legacy `DEVICE_AUDIO_OUT_BUFFER_MS` name is still accepted as a fallback alias for `DEVICE_AUDIO_OUT_START_BUFFER_MS`.
- `./.env` inside the runtime folder is the default config source for both manual launch and `systemd`.
- `DEVICE_WHISPLAY_DRIVER_PATH` can point at the vendor repo's `Driver/` folder; the launcher prepends it to `PYTHONPATH` and the runtime tries both `import whisplay` and `from WhisPlay import WhisPlayBoard` compatibility paths.
- `scripts/install_raspberry.sh` now creates the venv with `--system-site-packages` by default so apt-installed vendor dependencies such as `spidev`, `RPi.GPIO`, and `Pillow` remain visible on Raspberry Pi. Set `DEVICE_RUNTIME_VENV_SYSTEM_SITE_PACKAGES=0` only if you want an isolated venv.
- The runtime fails fast when `DEVICE_ID` or `DEVICE_WS_URL` is missing.
- Hardware adapters degrade independently when optional native dependencies are absent.
- The runtime remains a thin WebSocket client; no backend orchestration is moved onto the Pi.
