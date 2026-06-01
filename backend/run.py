"""Backend launcher — binds uvicorn to the current interpreter."""

from __future__ import annotations

import argparse
import os

import uvicorn


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run note-taker backend")
    p.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    p.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("BACKEND_RELOAD", "false").strip().lower() in {"1", "true", "yes"},
    )
    p.add_argument("--env-file", default=os.getenv("BACKEND_ENV_FILE", ".env"))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    uvicorn.run(
        "backend.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        env_file=args.env_file,
    )


if __name__ == "__main__":
    main()
