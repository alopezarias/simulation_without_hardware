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

Dependencias de voz local (Whisper + TTS):

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
pip install -r requirements.txt
```

Nota: la primera transcripcion con Whisper descarga el modelo configurado (`WHISPER_MODEL_SIZE`), por defecto `base`.

## 1. Arrancar backend

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
uvicorn backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Modo `echo` (audio->texto->audio sin pasar por agente):

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
ENABLE_WHISPER_STT=true ENABLE_LOCAL_TTS=true AUDIO_REPLY_MODE=echo uvicorn backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

Si en macOS falla `pyttsx3`, fuerza el backend de voz nativo:

```bash
ENABLE_LOCAL_TTS=true TTS_BACKEND=say AUDIO_REPLY_MODE=echo uvicorn backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
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
- Flujo rapido de audio: `Tap` abre escucha + micro automaticamente; siguiente `Tap` cierra y envia turno.
- Botones de audio: `Abrir Mic` y `Cerrar Mic` para control manual sin cambiar estado del turno.
- Boton `Refrescar Mic` + selector `Dispositivo Mic` para elegir el microfono real.
- La mini pantalla muestra barra superior de estado (LED, red, bateria) y bloques de texto de envio/respuesta.
- A la derecha tienes una terminal de trafico WS con JSON `TX` y `RX` en tiempo real.
- Indicador `Mic` con punto rojo parpadeante cuando esta grabando.
- Contadores en tiempo real: `Chunks TX`/`Audio TX` y `Chunks RX`/`Audio RX`.
- Indicador `Audio OUT` para ver si la reproduccion de audio de backend esta activa.
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

1. Pulsa `Refrescar Mic` y elige `Dispositivo Mic`.
2. Pulsa `Tap` para entrar en `LISTENING` (el micro se abre automaticamente).
3. Habla unos segundos.
4. Pulsa `Tap` otra vez para cerrar turno y enviar audio.
5. Revisa:
   - terminal WS lateral con varios `audio.chunk` enviados,
   - logs del backend con `audio.chunk received ... size_kb=...`,
   - `transcript.final` con texto reconocido por Whisper,
   - en la respuesta del backend, eventos `assistant.audio.start/chunk/end`,
   - contador `Audio RX` subiendo y `Audio OUT` en `ON` durante reproduccion.

### Escenario C2b: prueba definitiva (audio->texto->audio en echo)

1. Arranca backend en modo echo:

```bash
cd /Users/user/Documents/projects/ai/ia_device/simulation_without_hardware
source .venv/bin/activate
ENABLE_WHISPER_STT=true WHISPER_MODEL_SIZE=tiny ENABLE_LOCAL_TTS=true TTS_BACKEND=auto AUDIO_REPLY_MODE=echo python -m uvicorn backend:app --host 127.0.0.1 --port 8000 --reload --env-file .env
```

2. Abre `simulator_ui.py`, habla y cierra con `Tap`.
3. Comprueba:
   - `transcript.final` con el texto reconocido,
   - `assistant.text.final` con ese mismo texto (modo `echo`),
   - reproducción en la UI por `assistant.audio.*` (`source=tts` en logs).

### Escenario C3: loopback audio manual (botones)

1. Pulsa `Tap` para entrar en `LISTENING`.
2. Pulsa `Refrescar Mic` y elige `Dispositivo Mic`.
3. Pulsa `Abrir Mic` y habla unos segundos.
4. Pulsa `Cerrar Mic`.
5. Pulsa `Tap` para cerrar turno.
6. Verifica los mismos puntos que en C2.

### Si no detecta microfono

1. En macOS, habilita permisos de microfono para la app terminal/IDE (System Settings -> Privacy & Security -> Microphone).
2. Cierra y vuelve a abrir la terminal.
3. Ejecuta `Refrescar Mic` en la UI.
4. Si sigue sin detectar, revisa el campo `Mic Error` en la esquina superior izquierda.

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

Escenario de audio loopback:

```bash
python scenario_runner.py --ws-url ws://127.0.0.1:8000/ws --scenario audio-loopback
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
