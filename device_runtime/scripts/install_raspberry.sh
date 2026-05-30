#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SOURCE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
INSTALL_ROOT="${DEVICE_RUNTIME_INSTALL_ROOT:-$SOURCE_ROOT}"
LOCAL_ENV_FILE="${DEVICE_RUNTIME_ENV_FILE:-$INSTALL_ROOT/.env}"
LEGACY_ENV_FILE="${DEVICE_RUNTIME_LEGACY_ENV_FILE:-/etc/device-runtime/device-runtime.env}"
SERVICE_PATH="${DEVICE_RUNTIME_SERVICE_PATH:-/etc/systemd/system/device-runtime.service}"
RUN_USER="${DEVICE_RUNTIME_RUN_USER:-${SUDO_USER:-$(id -un)}}"
RUN_GROUP="${DEVICE_RUNTIME_RUN_GROUP:-$(id -gn "$RUN_USER")}"
INSTALL_RASPI_EXTRAS="${DEVICE_RUNTIME_INSTALL_RASPI_EXTRAS:-1}"
INSTALL_SERVICE="${DEVICE_RUNTIME_INSTALL_SERVICE:-1}"
ENABLE_SERVICE="${DEVICE_RUNTIME_ENABLE_SERVICE:-0}"
RESTART_SERVICE="${DEVICE_RUNTIME_RESTART_SERVICE:-0}"
USE_SYSTEM_SITE_PACKAGES="${DEVICE_RUNTIME_VENV_SYSTEM_SITE_PACKAGES:-1}"
INSTALL_PISUGAR="${DEVICE_RUNTIME_INSTALL_PISUGAR:-auto}"
ENABLE_I2C="${DEVICE_RUNTIME_ENABLE_I2C:-1}"
PISUGAR_CHANNEL="${DEVICE_RUNTIME_PISUGAR_CHANNEL:-release}"
PISUGAR_INSTALLER_URL="${DEVICE_RUNTIME_PISUGAR_INSTALLER_URL:-https://cdn.pisugar.com/release/pisugar-power-manager.sh}"
PISUGAR_SERVER_SERVICE="${DEVICE_RUNTIME_PISUGAR_SERVER_SERVICE:-pisugar-server.service}"
PISUGAR_DEFAULTS_PATH="${DEVICE_RUNTIME_PISUGAR_DEFAULTS_PATH:-/etc/default/pisugar-server}"
PISUGAR_MODEL="${DEVICE_RUNTIME_PISUGAR_MODEL:-}"

log_note() {
  printf '[install_raspberry] %s\n' "$1"
}

read_env_value() {
  python3 - "$1" "$2" "$3" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

env_path = Path(sys.argv[1])
key = sys.argv[2]
default = sys.argv[3]
if not env_path.exists():
    print(default)
    raise SystemExit(0)

for line in env_path.read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    current_key, value = stripped.split("=", 1)
    if current_key.strip() == key:
        print(value.strip())
        raise SystemExit(0)

print(default)
PY
}

ensure_local_env() {
  if [ -f "$LOCAL_ENV_FILE" ]; then
    return
  fi
  if [ -f "$LEGACY_ENV_FILE" ]; then
    cp "$LEGACY_ENV_FILE" "$LOCAL_ENV_FILE"
    return
  fi
  cp "$INSTALL_ROOT/.env.example" "$LOCAL_ENV_FILE"
}

should_install_pisugar() {
  case "$INSTALL_PISUGAR" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    0|false|FALSE|no|NO|off|OFF)
      return 1
      ;;
    auto|AUTO|"")
      power_adapter="$(read_env_value "$LOCAL_ENV_FILE" "DEVICE_POWER_ADAPTER" "none")"
      case "$power_adapter" in
        pisugar|PiSugar|PISUGAR)
          return 0
          ;;
      esac
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

configure_pisugar_defaults() {
  defaults_path="$1"
  model="$2"
  if [ ! -f "$defaults_path" ]; then
    log_note "PiSugar defaults file not found at $defaults_path; keeping package defaults"
    return
  fi
  python3 - "$defaults_path" "$model" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

defaults_path = Path(sys.argv[1])
model = sys.argv[2]
content = defaults_path.read_text(encoding="utf-8")
updated = re.sub(r"--model\s+(['\"]).*?\1", f"--model '{model}'", content)
if updated == content:
    updated = re.sub(r"(DAEMON_ARGS|DAEMON_OPTS|OPTIONS)=([\"'])(.*)\2", lambda m: f"{m.group(1)}={m.group(2)}{m.group(3)} --model '{model}'{m.group(2)}", content, count=1)
if updated == content:
    updated = content.rstrip() + f"\nDAEMON_ARGS=\"--model '{model}'\"\n"
defaults_path.write_text(updated, encoding="utf-8")
PY
}

install_pisugar_support() {
  if [ "$(id -u)" -ne 0 ]; then
    log_note "PiSugar install skipped: root privileges required"
    return
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    log_note "PiSugar install skipped: systemctl not available"
    return
  fi
  if [ "$ENABLE_I2C" = "1" ] && command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_i2c 0 || log_note "Unable to enable I2C via raspi-config"
  fi
  if [ "$ENABLE_I2C" = "1" ] && command -v modprobe >/dev/null 2>&1; then
    modprobe i2c-dev >/dev/null 2>&1 || true
  fi

  if ! systemctl list-unit-files "$PISUGAR_SERVER_SERVICE" >/dev/null 2>&1; then
    if ! command -v apt-get >/dev/null 2>&1; then
      log_note "PiSugar install skipped: apt-get not available"
      return
    fi
    apt-get update >/dev/null 2>&1
    apt-get install -y debconf-utils ca-certificates wget >/dev/null 2>&1
    printf 'pisugar-server pisugar-server/model select %s\n' "$PISUGAR_MODEL" | debconf-set-selections
    printf 'pisugar-poweroff pisugar-poweroff/model select %s\n' "$PISUGAR_MODEL" | debconf-set-selections
    installer_path="$(mktemp /tmp/pisugar-power-manager.XXXXXX.sh)"
    trap 'rm -f "$installer_path"' EXIT INT TERM
    wget -q -O "$installer_path" "$PISUGAR_INSTALLER_URL"
    chmod +x "$installer_path"
    DEBIAN_FRONTEND=noninteractive bash "$installer_path" -c "$PISUGAR_CHANNEL"
    rm -f "$installer_path"
    trap - EXIT INT TERM
  fi

  configure_pisugar_defaults "$PISUGAR_DEFAULTS_PATH" "$PISUGAR_MODEL"
  systemctl enable "$PISUGAR_SERVER_SERVICE" >/dev/null 2>&1 || true
  systemctl restart "$PISUGAR_SERVER_SERVICE" >/dev/null 2>&1 || systemctl start "$PISUGAR_SERVER_SERVICE" >/dev/null 2>&1 || true
}

mkdir -p "$INSTALL_ROOT"

python3 - "$SOURCE_ROOT" "$INSTALL_ROOT" <<'PY'
from __future__ import annotations

import shutil
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
install_root = Path(sys.argv[2]).resolve()
exclude_dirs = {".git", ".venv", "__pycache__", ".pytest_cache"}
exclude_files = {".DS_Store"}

if source_root == install_root:
    raise SystemExit(0)

for path in source_root.rglob("*"):
    relative = path.relative_to(source_root)
    if any(part in exclude_dirs for part in relative.parts):
        continue
    if path.name in exclude_files:
        continue
    target = install_root / relative
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
PY

ensure_local_env

if [ -z "$PISUGAR_MODEL" ]; then
  PISUGAR_MODEL="$(read_env_value "$LOCAL_ENV_FILE" "DEVICE_RUNTIME_PISUGAR_MODEL" "PiSugar 3")"
fi

VENV_ARGS=""
if [ "$USE_SYSTEM_SITE_PACKAGES" = "1" ]; then
  VENV_ARGS="--system-site-packages"
fi

python3 -m venv $VENV_ARGS "$INSTALL_ROOT/.venv"
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$INSTALL_ROOT/.venv/bin/python" -m pip install -r "$INSTALL_ROOT/requirements-base.txt"
if [ "$INSTALL_RASPI_EXTRAS" = "1" ]; then
  # numpy via apt first: pip-compiling numpy from source on a Pi takes ~30 min,
  # apt has a precompiled wheel in seconds. The venv is --system-site-packages
  # so the apt install becomes visible inside the venv automatically.
  if [ "$(id -u)" -eq 0 ] && command -v apt-get >/dev/null 2>&1; then
    apt-get install -y python3-numpy >/dev/null 2>&1 || log_note "apt python3-numpy install skipped"
  fi
  "$INSTALL_ROOT/.venv/bin/python" -m pip install -r "$INSTALL_ROOT/requirements-raspi.txt"
  "$INSTALL_ROOT/.venv/bin/python" -m pip install ".[raspi]" --no-build-isolation
else
  "$INSTALL_ROOT/.venv/bin/python" -m pip install . --no-build-isolation
fi

if should_install_pisugar; then
  install_pisugar_support
fi

if [ "$(id -u)" -eq 0 ] && [ "$INSTALL_SERVICE" = "1" ] && command -v systemctl >/dev/null 2>&1; then
python3 - "$INSTALL_ROOT/deploy/device-runtime.service" "$SERVICE_PATH" "$RUN_USER" "$RUN_GROUP" "$INSTALL_ROOT" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

template_path = Path(sys.argv[1])
service_path = Path(sys.argv[2])
run_user = sys.argv[3]
run_group = sys.argv[4]
install_root = sys.argv[5]
content = template_path.read_text(encoding="utf-8")
content = content.replace("__DEVICE_RUNTIME_USER__", run_user)
content = content.replace("__DEVICE_RUNTIME_GROUP__", run_group)
content = content.replace("__DEVICE_RUNTIME_ROOT__", install_root)
service_path.write_text(content, encoding="utf-8")
PY

  systemctl daemon-reload
  if [ "$ENABLE_SERVICE" = "1" ]; then
    systemctl enable device-runtime.service
  fi
  if [ "$RESTART_SERVICE" = "1" ]; then
    systemctl restart device-runtime.service
  fi
fi

if [ "$(id -u)" -eq 0 ] && [ -d "$INSTALL_ROOT" ]; then
  chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_ROOT"
  chown "$RUN_USER:$RUN_GROUP" "$LOCAL_ENV_FILE"
fi

printf 'Installed runtime into %s\n' "$INSTALL_ROOT"
printf 'Primary env file: %s\n' "$LOCAL_ENV_FILE"
if [ -f "$LEGACY_ENV_FILE" ]; then
  printf 'Legacy env file detected (optional fallback): %s\n' "$LEGACY_ENV_FILE"
fi
if [ "$(id -u)" -eq 0 ] && [ "$INSTALL_SERVICE" = "1" ] && command -v systemctl >/dev/null 2>&1; then
  printf 'Service file: %s\n' "$SERVICE_PATH"
else
  printf 'Service install: skipped\n'
fi
printf 'Run user: %s\n' "$RUN_USER"
printf 'Run group: %s\n' "$RUN_GROUP"
if should_install_pisugar; then
  printf 'PiSugar support: requested (model=%s, service=%s)\n' "$PISUGAR_MODEL" "$PISUGAR_SERVER_SERVICE"
else
  printf 'PiSugar support: skipped\n'
fi
printf 'Next: edit %s, then run %s/scripts/run_runtime.sh.\n' "$LOCAL_ENV_FILE" "$INSTALL_ROOT"
