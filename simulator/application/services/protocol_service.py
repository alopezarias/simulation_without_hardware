"""Inbound protocol mapping that preserves local device-state ownership."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from simulator.domain.events import DomainEffect, EffectPayload
from simulator.domain.state import DeviceSnapshot
from simulator.shared.protocol import UiState


@dataclass(slots=True)
class ProtocolUpdate:
    snapshot: DeviceSnapshot
    effects: list[EffectPayload] = field(default_factory=list)
    note: str = ""


class ProtocolService:
    """Applies backend messages onto the shared simulator snapshot."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl_seconds = ttl_seconds

    def apply_message(self, snapshot: DeviceSnapshot, message: dict[str, Any], *, now: float) -> ProtocolUpdate:
        current = deepcopy(snapshot)
        message_type = str(message.get("type", ""))

        if message_type == "session.ready":
            return self._apply_session_ready(current, message, now=now)
        if message_type == "ui.state":
            return self._apply_ui_state(current, message)
        if message_type == "agents.version.response":
            return self._apply_agents_version(current, message, now=now)
        if message_type == "agents.list.response":
            return self._apply_agents_list(current, message, now=now)
        if message_type == "agent.selected":
            return self._apply_agent_selected(current, message)
        if message_type == "transcript.partial":
            piece = str(message.get("text", "")).strip()
            if piece:
                current.transcript = (current.transcript + " " + piece).strip()
            return ProtocolUpdate(snapshot=current)
        if message_type == "transcript.final":
            current.transcript = str(message.get("text", current.transcript))
            return ProtocolUpdate(snapshot=current)
        if message_type == "assistant.text.partial":
            current.assistant_text += str(message.get("text", ""))
            return ProtocolUpdate(snapshot=current)
        if message_type == "assistant.text.final":
            current.assistant_text = str(message.get("text", current.assistant_text))
            if bool(message.get("interrupted")):
                current.assistant_text += " [interrupted]"
            latency = message.get("latency_ms")
            if isinstance(latency, int):
                current.last_latency_ms = latency
            return ProtocolUpdate(snapshot=current)
        if message_type == "error":
            current.remote_ui_state = UiState.ERROR
            if current.pending_agent_ack:
                current.pending_agent_ack = None
            detail = str(message.get("detail", "")).strip()
            return ProtocolUpdate(snapshot=current, note=detail or "backend error")
        return ProtocolUpdate(snapshot=current)

    def _apply_session_ready(
        self,
        snapshot: DeviceSnapshot,
        message: dict[str, Any],
        *,
        now: float,
    ) -> ProtocolUpdate:
        snapshot.connected = True
        snapshot.session_id = str(message.get("session_id", ""))
        version = str(message.get("agents_version", "")).strip()
        agents = self._normalize_agents(message.get("available_agents"))
        remote_agent = str(message.get("active_agent", "")).strip()

        if agents:
            self._replace_agent_cache(snapshot, agents, version=version, now=now)

        if remote_agent and not snapshot.pending_agent_ack:
            snapshot.set_agent(remote_agent)

        if version:
            snapshot.agents_version = version
            snapshot.agent_cache.version = version

        return ProtocolUpdate(snapshot=snapshot, note="session ready")

    def _apply_ui_state(self, snapshot: DeviceSnapshot, message: dict[str, Any]) -> ProtocolUpdate:
        state_value = str(message.get("state", UiState.IDLE.value))
        try:
            snapshot.remote_ui_state = UiState(state_value)
        except ValueError:
            snapshot.remote_ui_state = UiState.ERROR
        return ProtocolUpdate(snapshot=snapshot)

    def _apply_agents_version(
        self,
        snapshot: DeviceSnapshot,
        message: dict[str, Any],
        *,
        now: float,
    ) -> ProtocolUpdate:
        version = str(message.get("version", "")).strip()
        remote_agent = str(message.get("active_agent", "")).strip()
        if remote_agent and not snapshot.pending_agent_ack:
            snapshot.set_agent(remote_agent)

        if version and version == snapshot.agents_version and snapshot.agents:
            snapshot.agent_cache.loaded_at = now
            snapshot.agent_cache.expires_at = now + self._ttl_seconds
            snapshot.agent_cache.version = version
            return ProtocolUpdate(snapshot=snapshot, note="agent cache confirmed")

        effects = [EffectPayload(DomainEffect.REQUEST_AGENTS_LIST)]
        return ProtocolUpdate(snapshot=snapshot, effects=effects, note="agent cache stale")

    def _apply_agents_list(
        self,
        snapshot: DeviceSnapshot,
        message: dict[str, Any],
        *,
        now: float,
    ) -> ProtocolUpdate:
        version = str(message.get("version", "")).strip()
        agents = self._normalize_agents(message.get("agents"))
        remote_agent = str(message.get("active_agent", "")).strip()

        if agents:
            focus_agent = snapshot.pending_agent_ack or remote_agent or snapshot.active_agent
            self._replace_agent_cache(snapshot, agents, version=version, now=now)
            if focus_agent in snapshot.agents:
                snapshot.navigation.focused_agent_index = snapshot.agents.index(focus_agent)
                if not snapshot.pending_agent_ack:
                    snapshot.navigation.active_agent_id = focus_agent

        if remote_agent and not snapshot.pending_agent_ack:
            snapshot.set_agent(remote_agent)

        return ProtocolUpdate(snapshot=snapshot, note="agents catalog updated")

    def _apply_agent_selected(self, snapshot: DeviceSnapshot, message: dict[str, Any]) -> ProtocolUpdate:
        selected = str(message.get("agent_id", "")).strip()
        if not selected:
            return ProtocolUpdate(snapshot=snapshot)

        snapshot.pending_agent_ack = None
        snapshot.set_agent(selected)
        return ProtocolUpdate(snapshot=snapshot, note=f"agent ACK: {selected}")

    def _replace_agent_cache(
        self,
        snapshot: DeviceSnapshot,
        agents: list[str],
        *,
        version: str,
        now: float,
    ) -> None:
        snapshot.agent_cache.agents = agents
        snapshot.agent_cache.loaded_at = now
        snapshot.agent_cache.expires_at = now + self._ttl_seconds
        snapshot.agent_cache.version = version
        snapshot.agents_version = version

    def _normalize_agents(self, raw_agents: Any) -> list[str]:
        if not isinstance(raw_agents, list):
            return []
        return [str(agent).strip() for agent in raw_agents if str(agent).strip()]
