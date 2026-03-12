# OpenClawd WebSocket Setup

Guia para conectar el backend de simulacion con agentes remotos de OpenClawd via WebSocket, incluyendo tunel SSH.

## 1. Abrir tunel SSH

Ejemplo (tu maquina -> VPS):

```bash
ssh -i ~/.ssh/tu_clave -N -L 8765:127.0.0.1:8765 usuario@tu-vps
```

Esto expone localmente `127.0.0.1:8765` hacia el puerto remoto donde escucha OpenClawd WS.

## 2. Configurar `.env`

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

## 3. Ajustar mapeo de campos segun tu servidor

El adaptador construye el payload con estos campos configurables:

- agente: `OPENCLAWD_WS_AGENT_FIELD`
- entrada de usuario: `OPENCLAWD_WS_INPUT_FIELD`
- sesion: `OPENCLAWD_WS_SESSION_FIELD`
- tipo de mensaje opcional: `OPENCLAWD_WS_REQUEST_TYPE`
- payload adicional: `OPENCLAWD_WS_EXTRA_PAYLOAD` (JSON object)

Ejemplo si tu servidor espera `{"type":"chat","agent":"x","prompt":"..."}`:

```env
OPENCLAWD_WS_REQUEST_TYPE=chat
OPENCLAWD_WS_AGENT_FIELD=agent
OPENCLAWD_WS_INPUT_FIELD=prompt
OPENCLAWD_WS_SESSION_FIELD=session
```

## 4. Arrancar backend y probar

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
uvicorn app.backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

En otra terminal:

```bash
python scenario_runner.py --ws-url ws://127.0.0.1:8000/ws --scenario baseline
```

Si pasa, ya puedes usar `simulator_ui.py` y ejecutar escenarios de interfaz.

## 5. Diagnostico rapido

- Si no llega texto: revisa `OPENCLAWD_WS_*_FIELD`.
- Si responde pero no corta stream: revisa `OPENCLAWD_WS_FINAL_TYPES`.
- Si corta demasiado pronto: amplía `OPENCLAWD_WS_RECEIVE_TIMEOUT_S`.
- Si hay auth error: revisa `OPENCLAWD_WS_BEARER_TOKEN` o `OPENCLAWD_WS_HEADERS`.
