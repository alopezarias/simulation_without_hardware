"""Helpers for consolidating runtime diagnostics and capability warnings."""

from __future__ import annotations

from dataclasses import dataclass, field

from device_runtime.domain.capabilities import CapabilityStatus, DeviceCapabilities
from device_runtime.domain.state import DeviceSnapshot


@dataclass(slots=True)
class DiagnosticsSnapshot:
    warnings: list[str] = field(default_factory=list)
    transport_status: str = "disconnected"
    last_error: str = ""
    active_turn_id: str | None = None
    connected: bool = False


class DiagnosticsService:
    """Produces capability-aware diagnostics without leaking vendor details."""

    def build_snapshot(self, snapshot: DeviceSnapshot) -> DiagnosticsSnapshot:
        warnings = list(snapshot.diagnostics.warnings)
        warnings.extend(self._warnings_from_capabilities(snapshot.capabilities))
        unique_warnings = list(dict.fromkeys(warnings))
        return DiagnosticsSnapshot(
            warnings=unique_warnings,
            transport_status=snapshot.diagnostics.transport_status,
            last_error=snapshot.diagnostics.last_error,
            active_turn_id=snapshot.turn_id,
            connected=snapshot.connected,
        )

    def refresh_snapshot(
        self,
        snapshot: DeviceSnapshot,
        *,
        capabilities: DeviceCapabilities | None = None,
        transport_status: str | None = None,
        last_error: str | None = None,
        note: str | None = None,
    ) -> DeviceSnapshot:
        if capabilities is not None:
            snapshot.capabilities = capabilities
        if transport_status is not None:
            snapshot.diagnostics.transport_status = transport_status
        if last_error is not None:
            snapshot.diagnostics.last_error = last_error
        if note is not None:
            snapshot.diagnostics.last_note = note
        snapshot.diagnostics.warnings = self._warnings_from_capabilities(snapshot.capabilities)
        return snapshot

    def _warnings_from_capabilities(self, capabilities: DeviceCapabilities) -> list[str]:
        warnings: list[str] = []
        for name, capability in capabilities.all().items():
            if capability.status == CapabilityStatus.ENABLED:
                continue
            detail = f": {capability.detail}" if capability.detail else ""
            warnings.append(f"{name} {capability.status.value}{detail}")
        return warnings
