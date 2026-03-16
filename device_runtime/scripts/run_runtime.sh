#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
RUNTIME_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
LOCAL_ENV_FILE="$RUNTIME_ROOT/.env"
LEGACY_ENV_FILE="/etc/device-runtime/device-runtime.env"
VENV_BIN="${DEVICE_RUNTIME_VENV_BIN:-$RUNTIME_ROOT/.venv/bin}"

if [ -n "${DEVICE_RUNTIME_ENV:-}" ]; then
  ENV_FILE="$DEVICE_RUNTIME_ENV"
elif [ -f "$LOCAL_ENV_FILE" ]; then
  ENV_FILE="$LOCAL_ENV_FILE"
else
  ENV_FILE="$LEGACY_ENV_FILE"
fi

if [ ! -f "$ENV_FILE" ]; then
  printf 'Missing runtime env file: %s\n' "$ENV_FILE" >&2
  printf 'Create %s from %s/.env.example and set DEVICE_WS_URL to your PC backend.\n' "$LOCAL_ENV_FILE" "$RUNTIME_ROOT" >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

if [ -z "${DEVICE_WS_URL:-}" ]; then
  printf 'DEVICE_WS_URL is required in %s\n' "$ENV_FILE" >&2
  exit 1
fi

if [ -n "${DEVICE_WHISPLAY_DRIVER_PATH:-}" ]; then
  PYTHONPATH="$DEVICE_WHISPLAY_DRIVER_PATH${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONPATH
fi

if [ ! -x "$VENV_BIN/device-runtime" ]; then
  printf 'Runtime executable not found: %s/device-runtime\n' "$VENV_BIN" >&2
  printf 'Run scripts/install_raspberry.sh first.\n' >&2
  exit 1
fi

exec "$VENV_BIN/device-runtime" "$@"
