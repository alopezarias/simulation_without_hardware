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

VENV_ARGS=""
if [ "$USE_SYSTEM_SITE_PACKAGES" = "1" ]; then
  VENV_ARGS="--system-site-packages"
fi

python3 -m venv $VENV_ARGS "$INSTALL_ROOT/.venv"
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_ROOT/.venv/bin/python" -m pip install -r "$INSTALL_ROOT/requirements-base.txt"
if [ "$INSTALL_RASPI_EXTRAS" = "1" ]; then
  "$INSTALL_ROOT/.venv/bin/python" -m pip install -r "$INSTALL_ROOT/requirements-raspi.txt"
  "$INSTALL_ROOT/.venv/bin/python" -m pip install ".[raspi]" --no-build-isolation
else
  "$INSTALL_ROOT/.venv/bin/python" -m pip install . --no-build-isolation
fi

if [ ! -f "$LOCAL_ENV_FILE" ]; then
  if [ -f "$LEGACY_ENV_FILE" ]; then
    cp "$LEGACY_ENV_FILE" "$LOCAL_ENV_FILE"
  else
    cp "$INSTALL_ROOT/.env.example" "$LOCAL_ENV_FILE"
  fi
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
printf 'Next: edit %s, then run %s/scripts/run_runtime.sh.\n' "$LOCAL_ENV_FILE" "$INSTALL_ROOT"
