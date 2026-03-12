# Runbook de pruebas

## 0. Preparacion (una sola vez)

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Si vas a usar micro en macOS, instala PortAudio:

```bash
brew install portaudio
```

## 1. Arrancar backend

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
uvicorn backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

## 2. Verificar backend vivo

```bash
curl -s http://127.0.0.1:8000/health
```

## 3. Probar simulador CLI

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
python simulator.py --ws-url ws://127.0.0.1:8000/ws
```

Comandos dentro del CLI:

```text
help
state
double
tap
text hola esta es una prueba
send
tap
long
quit
```

## 4. Probar simulador UI (ventana)

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
python simulator_ui.py --ws-url ws://127.0.0.1:8000/ws
```

Controles UI:

- Botones: `Tap`, `Double Tap`, `Long Press`, `Enviar Texto`.
- Teclado: `Space` (tap), doble `Space` rapido (double tap), `Esc` (long press).
- Selector `Entrada`: `text` o `mic` (audio por micro local en chunks).
- La mini pantalla muestra barra superior de estado (LED, red, bateria) y bloques de texto de envio/respuesta.
- A la derecha tienes una terminal de trafico WS con JSON `TX` y `RX` en tiempo real.
- Puedes ajustar la bateria manualmente con el slider `Bateria`.
- Selector `Vista`: `cased` (carcasa) o `bare` (sin carcasa).
- Botones de simulacion: `Turno`, `Interrupcion`, `Cancelacion`.

## 5. Escenarios funcionales para probar

### Escenario A: handshake de sesion

1. Arranca backend.
2. Arranca `simulator_ui.py`.
3. Comprueba en la UI: conexion `connected`, `session_id` visible y estado `idle`.

### Escenario B: cambio de agente

1. Estando en `idle`, pulsa `Double Tap`.
2. Verifica que cambia el agente activo y llega `agent.selected`.

### Escenario C: turno completo con texto debug

1. Pulsa `Tap` (pasa a `listening`).
2. Escribe texto y pulsa `Enviar Texto`.
3. Pulsa `Tap` para cerrar turno.
4. Verifica transcripcion final, respuesta parcial/final y vuelta a `idle`.

### Escenario C2: turno con audio por micro

1. Cambia `Entrada` a `mic`.
2. Pulsa `Tap` y habla unos segundos.
3. Pulsa `Tap` para cerrar turno.
4. Revisa:
   - terminal WS lateral con varios `audio.chunk` enviados,
   - logs del backend con `audio.chunk received ... size_kb=...`.

### Escenario D: interrupcion de respuesta

1. Lanza un turno como en C.
2. Mientras esta en `speaking`, pulsa `Tap` o `Long Press`.
3. Verifica que la respuesta se interrumpe y vuelve a `idle`.

### Escenario E: cancelacion de turno

1. Pulsa `Tap` para entrar en `listening`.
2. Pulsa `Long Press`.
3. Verifica cancelacion y vuelta a `idle` sin respuesta del asistente.

## 6. Smoke test automatizado

Con backend activo:

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
python smoke_test.py --ws-url ws://127.0.0.1:8000/ws
```

Resultado esperado:

```text
SMOKE TEST PASSED
```

## 7. Ejecutar simulaciones automatizadas (escenarios)

Con backend activo:

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
python scenario_runner.py --ws-url ws://127.0.0.1:8000/ws --scenario all
```

Escenario individual:

```bash
python scenario_runner.py --ws-url ws://127.0.0.1:8000/ws --scenario interrupt
```

Guardar reporte JSON:

```bash
python scenario_runner.py --ws-url ws://127.0.0.1:8000/ws --scenario all --report /tmp/sim_report.json
```

## 8. Activar auth basica de dispositivo (opcional)

Editar `.env`:

```env
SIM_DEVICE_AUTH_TOKEN=mi-token
SIM_ALLOWED_DEVICE_IDS=sim-device-001,sim-device-ui-001,sim-smoke-001
```

Arrancar backend y cliente con token:

```bash
python simulator_ui.py --ws-url ws://127.0.0.1:8000/ws --auth-token mi-token
python simulator.py --ws-url ws://127.0.0.1:8000/ws --auth-token mi-token
python smoke_test.py --ws-url ws://127.0.0.1:8000/ws --auth-token mi-token
```

## 9. OpenClawd por WebSocket con tunel SSH

Abrir tunel SSH (ejemplo):

```bash
ssh -i ~/.ssh/tu_clave -N -L 8765:127.0.0.1:8765 usuario@tu-vps
```

Configurar `.env` para modo WebSocket:

```env
OPENCLAWD_MODE=ws
OPENCLAWD_WS_URL=ws://127.0.0.1:8765/ws
OPENCLAWD_WS_BEARER_TOKEN=tu_token_si_aplica
OPENCLAWD_WS_HEADERS={}
OPENCLAWD_WS_EXTRA_PAYLOAD={}
OPENCLAWD_WS_REQUEST_TYPE=
OPENCLAWD_WS_AGENT_FIELD=agent_id
OPENCLAWD_WS_INPUT_FIELD=input
OPENCLAWD_WS_SESSION_FIELD=session_id
OPENCLAWD_WS_PARTIAL_TYPES=assistant.text.partial,partial,delta,response.chunk
OPENCLAWD_WS_FINAL_TYPES=assistant.text.final,final,done,response.final
```

Reiniciar backend y repetir escenarios C y D.

## 10. OpenClawd por HTTP (alternativa)

Si tu instancia expone HTTP/REST:

```env
OPENCLAWD_MODE=http
OPENCLAWD_BASE_URL=https://tu-endpoint
OPENCLAWD_CHAT_ENDPOINT=/api/chat
OPENCLAWD_API_KEY=tu_api_key
```
