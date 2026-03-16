#!/usr/bin/env bash
set -eu

usage() {
  printf 'Usage: %s --pi user@host --device-id ID --device-ws-url ws://PC:8000/ws [--ssh-port PORT] [--remote-runtime-dir DIR]\n' "$0" >&2
  exit 1
}

PI_HOST=""
DEVICE_ID=""
DEVICE_WS_URL=""
SSH_PORT="22"
REMOTE_STAGING="/tmp/device-runtime-deploy"
REMOTE_RUNTIME_DIR=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pi)
      PI_HOST="${2:-}"
      shift 2
      ;;
    --device-id)
      DEVICE_ID="${2:-}"
      shift 2
      ;;
    --device-ws-url)
      DEVICE_WS_URL="${2:-}"
      shift 2
      ;;
    --ssh-port)
      SSH_PORT="${2:-}"
      shift 2
      ;;
    --remote-staging)
      REMOTE_STAGING="${2:-}"
      shift 2
      ;;
    --remote-runtime-dir)
      REMOTE_RUNTIME_DIR="${2:-}"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

if [ -z "$PI_HOST" ] || [ -z "$DEVICE_ID" ] || [ -z "$DEVICE_WS_URL" ]; then
  usage
fi

if [ -z "$REMOTE_RUNTIME_DIR" ]; then
  REMOTE_USER="${PI_HOST%@*}"
  if [ "$REMOTE_USER" = "$PI_HOST" ]; then
    REMOTE_RUNTIME_DIR="device_runtime"
  elif [ "$REMOTE_USER" = "root" ]; then
    REMOTE_RUNTIME_DIR="/root/device_runtime"
  else
    REMOTE_RUNTIME_DIR="/home/$REMOTE_USER/device_runtime"
  fi
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RUNTIME_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SSH_CMD="ssh -p $SSH_PORT"

rsync -az --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.DS_Store' \
  -e "$SSH_CMD" \
  "$RUNTIME_ROOT/" "$PI_HOST:$REMOTE_STAGING/"

$SSH_CMD "$PI_HOST" "mkdir -p '$REMOTE_RUNTIME_DIR'"

$SSH_CMD "$PI_HOST" "sudo DEVICE_RUNTIME_INSTALL_ROOT='$REMOTE_RUNTIME_DIR' DEVICE_RUNTIME_RESTART_SERVICE=0 DEVICE_RUNTIME_ENABLE_SERVICE=0 bash '$REMOTE_STAGING/scripts/install_raspberry.sh'"

$SSH_CMD "$PI_HOST" "python3 - '$DEVICE_ID' '$DEVICE_WS_URL' '$REMOTE_RUNTIME_DIR/.env' <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

device_id = sys.argv[1]
device_ws_url = sys.argv[2]
env_path = Path(sys.argv[3]).expanduser()
lines = []
if env_path.exists():
    lines = env_path.read_text(encoding='utf-8').splitlines()
mapping = {}
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        mapping[key] = value
mapping['DEVICE_ID'] = device_id
mapping['DEVICE_WS_URL'] = device_ws_url
ordered_keys = []
for line in lines:
    if '=' in line and not line.lstrip().startswith('#'):
        key = line.split('=', 1)[0]
        if key not in ordered_keys:
            ordered_keys.append(key)
for key in ('DEVICE_ID', 'DEVICE_WS_URL'):
    if key not in ordered_keys:
        ordered_keys.append(key)
output = [f'{key}={mapping[key]}' for key in ordered_keys if key in mapping]
env_path.write_text('\n'.join(output) + '\n', encoding='utf-8')
PY"

$SSH_CMD "$PI_HOST" "sudo systemctl enable device-runtime.service && sudo systemctl restart device-runtime.service && sudo systemctl --no-pager --full status device-runtime.service"
