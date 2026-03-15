# OpenClawd WebSocket Setup

This guide shows how to point the backend at an OpenClawd-compatible WebSocket service. It is an optional integration path for this MVP, not the default workflow. For general local setup, start with `README.md` and `RUNBOOK.md`.

All commands assume you are running from the repository root.

## When to use this guide

Use this setup when you want the backend to forward assistant turns to a remote OpenClawd deployment over WebSocket instead of staying in local `mock` mode.

In practice, the flow is:

1. Expose the remote WebSocket endpoint locally, often through an SSH tunnel.
2. Switch the backend to `OPENCLAWD_MODE=ws`.
3. Align the request and response field mapping with the remote server schema.
4. Re-run the normal simulator checks against the local backend.

## 1. Expose the remote WebSocket endpoint

If the OpenClawd service is only reachable from a VPS or internal host, open a local SSH tunnel first:

```bash
ssh -i ~/.ssh/your_key -N -L 8765:127.0.0.1:8765 user@your-vps
```

That makes `127.0.0.1:8765` on your machine forward to the remote WebSocket port.

If your OpenClawd service is already directly reachable, use its actual `ws://` or `wss://` URL in the next step and skip the tunnel.

## 2. Configure `.env`

Start from `.env.example`, then set the WebSocket-specific variables:

```env
OPENCLAWD_MODE=ws
OPENCLAWD_WS_URL=ws://127.0.0.1:8765/ws
OPENCLAWD_WS_BEARER_TOKEN=
OPENCLAWD_WS_HEADERS={}
OPENCLAWD_WS_EXTRA_PAYLOAD={}
OPENCLAWD_WS_REQUEST_TYPE=
OPENCLAWD_WS_AGENT_FIELD=agent_id
OPENCLAWD_WS_INPUT_FIELD=input
OPENCLAWD_WS_SESSION_FIELD=session_id
OPENCLAWD_WS_PARTIAL_TYPES=assistant.text.partial,partial,delta,response.chunk
OPENCLAWD_WS_FINAL_TYPES=assistant.text.final,final,done,response.final
OPENCLAWD_WS_TIMEOUT_S=25
OPENCLAWD_WS_RECEIVE_TIMEOUT_S=4
OPENCLAWD_WS_MAX_MESSAGES=64
```

Notes:

- `OPENCLAWD_WS_URL` is required when `OPENCLAWD_MODE=ws`.
- `OPENCLAWD_WS_HEADERS` and `OPENCLAWD_WS_EXTRA_PAYLOAD` must be valid JSON objects.
- `OPENCLAWD_WS_BEARER_TOKEN` adds an `Authorization: Bearer ...` header automatically.
- `OPENCLAWD_WS_PARTIAL_TYPES` and `OPENCLAWD_WS_FINAL_TYPES` are comma-separated message-type lists used to decide when a stream is still in progress versus complete.

## 3. Align field mapping with your server schema

The adapter builds the outbound request from configurable fields:

- agent id: `OPENCLAWD_WS_AGENT_FIELD`
- user text: `OPENCLAWD_WS_INPUT_FIELD`
- session id: `OPENCLAWD_WS_SESSION_FIELD`
- optional request type: `OPENCLAWD_WS_REQUEST_TYPE`
- static extra payload: `OPENCLAWD_WS_EXTRA_PAYLOAD`

For example, if the remote server expects `{"type":"chat","agent":"x","prompt":"...","session":"..."}`, use:

```env
OPENCLAWD_WS_REQUEST_TYPE=chat
OPENCLAWD_WS_AGENT_FIELD=agent
OPENCLAWD_WS_INPUT_FIELD=prompt
OPENCLAWD_WS_SESSION_FIELD=session
```

The backend accepts a few response shapes:

- plain text WebSocket messages are treated as final text
- JSON messages with a recognized `type` in `OPENCLAWD_WS_PARTIAL_TYPES` are treated as partial chunks
- JSON messages with a recognized `type` in `OPENCLAWD_WS_FINAL_TYPES`, or with `done=true`, are treated as final

If your deployment uses different message names, update the type lists rather than changing the application code.

## 4. Start the backend and validate the path

Run the backend with your updated `.env`:

```bash
source .venv/bin/activate
python -m backend.run --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Then validate from another terminal against the local backend WebSocket:

```bash
source .venv/bin/activate
python -m simulator.qa.scenario_runner --ws-url ws://127.0.0.1:8000/ws --scenario listen-agents
```

You can also open the desktop simulator for a more visible check:

```bash
source .venv/bin/activate
python -m simulator.entrypoints.ui --ws-url ws://127.0.0.1:8000/ws
```

Recommended manual checks once connected:

- submit a short text turn and confirm streamed assistant text arrives
- switch agents and verify the selected agent is still forwarded correctly
- interrupt a response and confirm the backend returns to `idle`

## 5. Troubleshooting

- No text returns: verify `OPENCLAWD_WS_URL` and the `OPENCLAWD_WS_*_FIELD` mapping.
- Authentication fails: verify `OPENCLAWD_WS_BEARER_TOKEN` and `OPENCLAWD_WS_HEADERS`.
- Streaming never ends: update `OPENCLAWD_WS_FINAL_TYPES` or confirm the server sends `done=true`.
- Streaming ends too early: increase `OPENCLAWD_WS_RECEIVE_TIMEOUT_S` or review whether unrecognized messages are being treated as final.
- The backend errors immediately: confirm `OPENCLAWD_MODE=ws` and that the target service is actually reachable through the tunnel.

## Scope note

This repository already includes WebSocket and HTTP OpenClawd adapters, but the easiest and most stable path for first-time exploration is still local `mock` mode. Treat this guide as an integration aid for experimentation, not as proof of production-ready OpenClawd deployment support.
