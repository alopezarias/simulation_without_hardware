"""Adapter to call OpenClawd via mock, HTTP, or WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

import httpx
import websockets


class OpenClawdAdapter:
    def __init__(self) -> None:
        # Core mode selection: mock | http | ws
        mode_value = os.getenv("OPENCLAWD_MODE", "mock").strip().lower()
        self.mode = mode_value

        # Shared streaming controls (backend -> device)
        self.chunk_size = int(os.getenv("ASSISTANT_STREAM_CHUNK_SIZE", "24"))
        self.chunk_delay_s = float(os.getenv("ASSISTANT_STREAM_CHUNK_DELAY", "0.12"))

        # HTTP mode config
        self.base_url = os.getenv("OPENCLAWD_BASE_URL", "").strip()
        self.endpoint = os.getenv("OPENCLAWD_CHAT_ENDPOINT", "/api/chat").strip()
        self.api_key = os.getenv("OPENCLAWD_API_KEY", "").strip()
        self.timeout_s = float(os.getenv("OPENCLAWD_TIMEOUT_S", "25"))

        # WebSocket mode config
        self.ws_url = os.getenv("OPENCLAWD_WS_URL", "").strip()
        self.ws_timeout_s = float(os.getenv("OPENCLAWD_WS_TIMEOUT_S", "25"))
        self.ws_receive_timeout_s = float(os.getenv("OPENCLAWD_WS_RECEIVE_TIMEOUT_S", "4"))
        self.ws_max_messages = int(os.getenv("OPENCLAWD_WS_MAX_MESSAGES", "64"))

        self.ws_request_type = os.getenv("OPENCLAWD_WS_REQUEST_TYPE", "").strip()
        self.ws_agent_field = os.getenv("OPENCLAWD_WS_AGENT_FIELD", "agent_id").strip()
        self.ws_input_field = os.getenv("OPENCLAWD_WS_INPUT_FIELD", "input").strip()
        self.ws_session_field = os.getenv("OPENCLAWD_WS_SESSION_FIELD", "session_id").strip()

        self.ws_headers = self._load_json_dict_env("OPENCLAWD_WS_HEADERS", default={})
        self.ws_extra_payload = self._load_json_dict_env("OPENCLAWD_WS_EXTRA_PAYLOAD", default={})

        self.ws_bearer_token = os.getenv("OPENCLAWD_WS_BEARER_TOKEN", "").strip()

        self.ws_partial_types = self._load_type_set_env(
            "OPENCLAWD_WS_PARTIAL_TYPES",
            default={"assistant.text.partial", "partial", "delta", "response.chunk"},
        )
        self.ws_final_types = self._load_type_set_env(
            "OPENCLAWD_WS_FINAL_TYPES",
            default={"assistant.text.final", "final", "done", "response.final"},
        )

    async def stream_response(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        response_text = await self._get_response_text(
            agent_id=agent_id,
            user_text=user_text,
            session_id=session_id,
        )

        for chunk in self._chunk_text(response_text):
            yield chunk
            await asyncio.sleep(self.chunk_delay_s)

    async def _get_response_text(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> str:
        if self.mode == "mock":
            return self._mock_response(agent_id=agent_id, user_text=user_text)

        if self.mode in {"http", "https", "rest", "real"}:
            return await self._get_response_text_http(
                agent_id=agent_id,
                user_text=user_text,
                session_id=session_id,
            )

        if self.mode in {"ws", "websocket"}:
            return await self._get_response_text_ws(
                agent_id=agent_id,
                user_text=user_text,
                session_id=session_id,
            )

        raise RuntimeError(
            "Unsupported OPENCLAWD_MODE. Use one of: mock, http (or real), ws."
        )

    async def _get_response_text_http(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> str:
        if not self.base_url:
            raise RuntimeError(
                "OPENCLAWD_BASE_URL is required when OPENCLAWD_MODE is 'http' or 'real'."
            )

        payload = {
            "agent_id": agent_id,
            "input": user_text,
            "session_id": session_id,
        }

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url.rstrip('/')}/{self.endpoint.lstrip('/')}"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(url, json=payload, headers=headers)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:300]
            raise RuntimeError(f"OpenClawd HTTP error: {exc}. Body: {detail}") from exc

        text = self._extract_text(response.json())
        if not text:
            raise RuntimeError("OpenClawd HTTP response did not include any text output.")

        return text

    async def _get_response_text_ws(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> str:
        if not self.ws_url:
            raise RuntimeError("OPENCLAWD_WS_URL is required when OPENCLAWD_MODE is 'ws'.")

        request_payload = self._build_ws_request_payload(
            agent_id=agent_id,
            user_text=user_text,
            session_id=session_id,
        )

        headers = dict(self.ws_headers)
        if self.ws_bearer_token:
            headers["Authorization"] = f"Bearer {self.ws_bearer_token}"

        collected: list[str] = []

        async with asyncio.timeout(self.ws_timeout_s):
            async with websockets.connect(
                self.ws_url,
                additional_headers=headers or None,
            ) as ws:
                await ws.send(json.dumps(request_payload))

                for _ in range(self.ws_max_messages):
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(),
                            timeout=self.ws_receive_timeout_s,
                        )
                    except asyncio.TimeoutError:
                        if collected:
                            break
                        raise RuntimeError(
                            "Timed out waiting for OpenClawd websocket response."
                        ) from None

                    piece, is_final = self._parse_ws_message(raw)
                    if piece:
                        collected.append(piece)
                    if is_final:
                        break

        text = "".join(collected).strip()
        if not text:
            raise RuntimeError(
                "OpenClawd websocket did not return text. "
                "Check OPENCLAWD_WS_* field mapping and server message schema."
            )

        return text

    def _build_ws_request_payload(
        self,
        agent_id: str,
        user_text: str,
        session_id: str,
    ) -> dict[str, Any]:
        payload = dict(self.ws_extra_payload)

        if self.ws_request_type:
            payload["type"] = self.ws_request_type

        if self.ws_agent_field:
            payload[self.ws_agent_field] = agent_id
        if self.ws_input_field:
            payload[self.ws_input_field] = user_text
        if self.ws_session_field:
            payload[self.ws_session_field] = session_id

        return payload

    def _parse_ws_message(self, raw: Any) -> tuple[str, bool]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")

        if not isinstance(raw, str):
            return "", False

        stripped = raw.strip()
        if not stripped:
            return "", False

        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            # Non-JSON websocket payload, treat as final plain text.
            return stripped, True

        if isinstance(message, str):
            return message.strip(), True

        if not isinstance(message, dict):
            return "", False

        if "error" in message:
            raise RuntimeError(f"OpenClawd websocket error: {message.get('error')}")

        msg_type = str(message.get("type", "")).strip().lower()
        done = bool(message.get("done"))
        piece = self._extract_text(message)

        if msg_type and msg_type in self.ws_final_types:
            return piece, True

        if done:
            return piece, True

        if msg_type and msg_type in self.ws_partial_types:
            return piece, False

        # Inference: if payload has text but no known type, treat as final.
        if piece:
            return piece, True

        return "", False

    def _extract_text(self, data: Any) -> str:
        if isinstance(data, str):
            return data.strip()

        if isinstance(data, dict):
            for key in ("output_text", "text", "response", "message", "content"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            # Common nested wrappers from websocket payloads.
            for nested_key in ("data", "payload", "result"):
                nested_value = data.get(nested_key)
                nested_text = self._extract_text(nested_value)
                if nested_text:
                    return nested_text

            choices = data.get("choices")
            if isinstance(choices, list):
                for item in choices:
                    nested_text = self._extract_text(item)
                    if nested_text:
                        return nested_text

        if isinstance(data, list):
            for item in data:
                nested_text = self._extract_text(item)
                if nested_text:
                    return nested_text

        return ""

    def _chunk_text(self, text: str) -> list[str]:
        text = text.strip()
        if not text:
            return ["No response generated."]

        size = max(self.chunk_size, 1)
        return [text[index : index + size] for index in range(0, len(text), size)]

    def _load_json_dict_env(self, env_key: str, default: dict[str, Any]) -> dict[str, Any]:
        value = os.getenv(env_key, "").strip()
        if not value:
            return dict(default)

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{env_key} must be valid JSON object.") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"{env_key} must be a JSON object.")

        return parsed

    def _load_type_set_env(self, env_key: str, default: set[str]) -> set[str]:
        value = os.getenv(env_key, "").strip()
        if not value:
            return {item.lower() for item in default}

        items = [piece.strip().lower() for piece in value.split(",") if piece.strip()]
        if not items:
            return {item.lower() for item in default}

        return set(items)

    def _mock_response(self, agent_id: str, user_text: str) -> str:
        cleaned = " ".join(user_text.split())
        prefix = {
            "assistant-general": "Respuesta general",
            "assistant-tech": "Respuesta tecnica",
            "assistant-ops": "Respuesta operativa",
        }.get(agent_id, f"Respuesta de {agent_id}")

        return (
            f"{prefix}: he recibido tu mensaje '{cleaned}'. "
            "Este backend esta en modo mock, pero ya mantiene el flujo completo "
            "de streaming, estados e interrupcion."
        )
