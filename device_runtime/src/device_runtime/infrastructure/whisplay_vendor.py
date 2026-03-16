"""Helpers for loading optional Whisplay vendor bindings."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import sys
from types import ModuleType


DEVICE_WHISPLAY_DRIVER_PATH_ENV = "DEVICE_WHISPLAY_DRIVER_PATH"
MODULE_CANDIDATES = ("whisplay", "WhisPlay")


@dataclass(slots=True)
class WhisplayVendorModule:
    module: ModuleType | None
    import_error: Exception | None
    driver_path: str = ""

    @property
    def available(self) -> bool:
        return self.module is not None

    def create_board(self) -> object:
        if self.module is None:
            raise RuntimeError(_build_error_message(self.driver_path, self.import_error))
        for name in ("WhisPlayBoard", "WhisplayBoard"):
            factory = getattr(self.module, name, None)
            if callable(factory):
                return factory()
        display_factory = getattr(self.module, "Display", None)
        if callable(display_factory):
            return display_factory()
        raise RuntimeError(
            "Whisplay vendor module does not expose WhisPlayBoard/WhisplayBoard or Display"
        )


def load_whisplay_vendor(driver_path: str = "") -> WhisplayVendorModule:
    normalized_path = _normalize_driver_path(driver_path)
    path_error: Exception | None = None
    if normalized_path:
        resolved = Path(normalized_path).expanduser()
        if resolved.exists():
            resolved_str = str(resolved)
            if resolved_str not in sys.path:
                sys.path.insert(0, resolved_str)
        else:
            path_error = ModuleNotFoundError(
                f"{DEVICE_WHISPLAY_DRIVER_PATH_ENV} path does not exist: {resolved}"
            )

    errors: list[Exception] = []
    if path_error is not None:
        errors.append(path_error)
    for module_name in MODULE_CANDIDATES:
        try:
            return WhisplayVendorModule(
                module=importlib.import_module(module_name),
                import_error=None,
                driver_path=normalized_path,
            )
        except Exception as exc:  # pragma: no cover - host dependent imports
            errors.append(exc)

    import_error = errors[-1] if errors else ModuleNotFoundError("Whisplay vendor bindings not found")
    return WhisplayVendorModule(module=None, import_error=import_error, driver_path=normalized_path)


def _normalize_driver_path(driver_path: str) -> str:
    raw = driver_path.strip() or os.environ.get(DEVICE_WHISPLAY_DRIVER_PATH_ENV, "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser())


def _build_error_message(driver_path: str, import_error: Exception | None) -> str:
    detail = ""
    if import_error is not None:
        detail = f": {import_error}"
    if driver_path:
        return (
            "Whisplay display adapter requires vendor Python bindings "
            f"(tried {DEVICE_WHISPLAY_DRIVER_PATH_ENV}={driver_path}){detail}"
        )
    return "Whisplay display adapter requires vendor Python bindings" + detail
