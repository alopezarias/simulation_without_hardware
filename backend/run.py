"""Backend launcher bound to the current Python interpreter.

Running this module avoids mismatches where `uvicorn` resolves to a different
environment that does not have STT/TTS dependencies installed.
"""

from __future__ import annotations

import argparse
import os

import uvicorn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run simulation backend")
    parser.add_argument("--host", default=os.getenv("BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BACKEND_PORT", "8000")))
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("BACKEND_RELOAD", "true").strip().lower() in {"1", "true", "yes", "on"},
        help="Enable uvicorn auto-reload",
    )
    parser.add_argument(
        "--env-file",
        default=os.getenv("BACKEND_ENV_FILE", ".env"),
        help="Environment file passed to uvicorn",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uvicorn.run(
        "backend.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        env_file=args.env_file,
    )


if __name__ == "__main__":
    main()
