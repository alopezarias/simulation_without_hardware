"""Monorepo shim that resolves runtime imports from `device_runtime/src`."""

from __future__ import annotations

from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_SRC_ROOT = _PKG_ROOT / "src" / "device_runtime"

if _SRC_ROOT.is_dir():
    __path__ = [str(_SRC_ROOT), str(_PKG_ROOT)]
else:
    __path__ = [str(_PKG_ROOT)]
