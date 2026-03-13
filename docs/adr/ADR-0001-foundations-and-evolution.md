# ADR-0001: Fundaciones y Evolucion del Simulador sin Hardware

- Estado: `accepted`
- Fecha: `2026-03-13`
- Alcance: `/simulation_without_hardware`
- Audiencia: humana + agentes IA

## 1. Contexto y problema

Necesitabamos validar las bases del dispositivo conversacional antes de tener hardware real (Whisplay HAT y encapsulado final).
El objetivo era reducir riesgo tecnico en protocolo, estados, UX del boton, audio streaming y conexion a agentes remotos.

## 2. Decision principal

Construir un MVP "hardwareless" en Python, con backend WebSocket + simuladores (CLI/UI), manteniendo contratos de mensajes compatibles con el futuro dispositivo.

## 3. Decisiones arquitectonicas adoptadas

### D1. Backend asincrono con FastAPI/WebSocket
- Se adopta `FastAPI` + endpoint `/ws` para canal bidireccional estado-comandos-eventos.
- Razon: simplicidad, latencia baja, y buena trazabilidad de eventos en tiempo real.
- Implementacion: `backend/api.py`.

### D2. Protocolo explicito y compartido
- Tipos de mensaje y estados comunes centralizados en `backend/shared/protocol.py`.
- Estados canonicos: `idle`, `listening`, `processing`, `speaking`, `error`.
- Razon: evitar divergencia entre backend, simulador CLI y simulador UI.

### D3. Adaptador OpenClawd desacoplado por modo
- `backend/infrastructure/ai/openclawd_adapter.py` soporta `mock`, `http` y `ws`.
- Razon: poder iterar localmente en `mock`, y luego conectar VPS por HTTP/WS sin reescribir backend.

### D4. Simulacion UI orientada a hardware final
- `simulator/entrypoints/ui.py` replica interacciones del dispositivo:
  - `Tap`, `Double Tap`, `Long Press`
  - mini pantalla estilo HAT (estado, bateria, texto, LED)
  - terminal lateral de trafico WS (`TX/RX/SYS`)
  - selector de micro, indicador REC y contadores de audio
- Razon: validar UX y flujo de eventos antes de GPIO/pantalla real.

### D5. Audio en chunks end-to-end
- Entrada: micro local en chunks PCM16 (`audio.chunk`).
- Backend: recompone audio completo en archivo temporal PCM por turno.
- Salida: envio de audio de respuesta por `assistant.audio.start/chunk/end`.
- Razon: comportamiento realista de streaming y test de robustez.

### D6. Control de memoria y estabilidad
- En UI:
  - cola de micro acotada
  - limite de chunks enviados por ciclo
  - buffers/logs acotados
  - sanitizacion de payload base64 en terminal WS
- En backend:
  - escritura de audio en archivo temporal (no en RAM)
  - limpieza de archivos por turno/cancelacion/disconnect
- Razon: evitar picos de RAM y degradacion en sesiones largas.

### D7. Pipeline de voz local (STT + TTS)
- Nuevo modulo `backend/infrastructure/speech/speech_pipeline.py`:
  - STT: `faster-whisper` (configurable por env)
  - TTS: backend `auto|say|pyttsx3`
  - conversion segura a PCM16 para streaming
- Razon: probar ciclo de voz completo local sin dependencia cloud obligatoria.

### D8. Modo de respuesta configurable
- `AUDIO_REPLY_MODE=assistant|echo`
  - `assistant`: texto de agente (mock/http/ws) y TTS sobre esa respuesta
  - `echo`: devuelve como respuesta lo transcrito (ideal para prueba de ida/vuelta voz)
- Razon: separar prueba funcional de voz del comportamiento de agente.

### D9. Observabilidad operativa
- Logging estructurado en backend (`IN/OUT`, chunks, latencia, modo audio).
- `session.ready` y `/health` exponen capacidades de speech y modo de respuesta.
- Razon: diagnostico rapido en pruebas locales y en VPS/tunel.

### D10. Validacion automatizada por escenarios
- `simulator/qa/smoke_test.py` + `simulator/qa/scenario_runner.py`.
- Escenarios: `baseline`, `interrupt`, `cancel`, `audio-loopback`.
- Razon: detectar regresiones de protocolo/estado/audio en cada iteracion.

### D11. Refactor a arquitectura hexagonal (sin romper contrato)
- Se desacopla `backend/api.py` en capas `config`, `domain`, `application`, `infrastructure` dentro de `backend/`.
- `backend/api.py` queda como fachada de compatibilidad + composition root (`python -m backend.run` recomendado, manteniendo `backend.api:app`).
- Puertos explicitos para IA, speech, salida a dispositivo y storage de audio temporal.
- Razon: permitir enchufar/desenchufar motores de IA/adapters sin reescribir casos de uso.

### D12. Separacion fisica backend/simulator como proyectos independientes
- Todo el backend queda en `backend/` y todo el emulador en `simulator/`.
- Se elimina el arbol `app/` y scripts Python sueltos en raiz para evitar acoplamiento accidental.
- Dependencias separadas en `backend/requirements.txt` y `simulator/requirements.txt`.
- Razon: desplegar backend en servidor sin arrastrar dependencias/UI del simulador.

## 4. Cronologia resumida

### Hito 1 (inicio MVP)
- Se crea base de backend, protocolo y simulador CLI.
- Se define handshake (`device.hello` -> `session.ready`) y maquina de estados.

### Hito 2 (UI y simulacion hardware)
- Se implementa simulador UI Tkinter con vista tipo dispositivo.
- Se agregan controles de boton, bateria, texto y terminal WS.

### Hito 3 (conexion OpenClawd real)
- Se incorpora adaptador desacoplado con modos `mock/http/ws`.
- Se documenta setup WS por tunel SSH (`OPENCLAWD_WS_SETUP.md`).

### Hito 4 (audio chunking + robustez)
- Captura micro local por chunks, envio continuo, metricas TX/RX.
- Ajustes de memoria para evitar consumo excesivo.
- Backend recompone audio en archivo y soporta loopback en chunks.

### Hito 5 (STT/TTS local)
- Integracion Whisper (`faster-whisper`) para transcripcion.
- Integracion TTS local (`say`/`pyttsx3`) con streaming de audio al simulador.
- Se introduce modo `echo` para prueba definitiva audio->texto->audio.

### Hito 6 (hexagonal definitivo)
- Refactor de backend monolitico a modulos hexagonales en `backend/`.
- Se mantiene compatibilidad de contrato WS, estado y comandos.
- Se conserva `backend/api.py` como entrada estable para despliegue y tests.

## 5. Snapshot tecnico actual

### Componentes
- `backend/api.py`: fachada de compatibilidad y punto de entrada de despliegue.
- `backend/config/settings.py`: carga de configuracion runtime.
- `backend/domain/session.py`: entidad de sesion.
- `backend/application/ports.py`: puertos hexagonales.
- `backend/application/services/*`: casos de uso de sesion/recording/turno/ruteo.
- `backend/infrastructure/*`: adapters concretos (OpenClawd, Speech, WS, audio-store, logging).
- `backend/shared/protocol.py`: tipos de mensaje y helpers.
- `backend/infrastructure/ai/openclawd_adapter.py`: cliente de agente remoto (mock/http/ws).
- `backend/infrastructure/speech/speech_pipeline.py`: STT/TTS local y conversion a PCM16.
- `simulator/entrypoints/ui.py`: emulador visual y de interaccion.
- `simulator/entrypoints/cli.py`: emulador CLI.
- `simulator/qa/scenario_runner.py`: regresion por escenarios.
- `simulator/qa/smoke_test.py`: prueba E2E minima.

### Flujo de turno (audio)
1. `recording.start`
2. N x `audio.chunk` (PCM16 base64)
3. `recording.stop`
4. Backend recompone y procesa audio
5. `transcript.final`
6. `assistant.text.partial/final`
7. `assistant.audio.start/chunk/end`
8. `ui.state` vuelve a `idle`

## 6. Variables de entorno clave

### OpenClawd
- `OPENCLAWD_MODE=mock|http|ws`
- `OPENCLAWD_BASE_URL`, `OPENCLAWD_CHAT_ENDPOINT`, `OPENCLAWD_API_KEY`
- `OPENCLAWD_WS_URL`, `OPENCLAWD_WS_*`

### Speech
- `ENABLE_WHISPER_STT`
- `WHISPER_MODEL_SIZE` (`tiny`, `base`, ...)
- `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, `WHISPER_LANGUAGE`
- `ENABLE_LOCAL_TTS`
- `TTS_BACKEND=auto|say|pyttsx3`
- `TTS_RATE`, `TTS_VOLUME`, `TTS_VOICE`
- `AUDIO_REPLY_MODE=assistant|echo`

### Seguridad/operacion
- `SIM_DEVICE_AUTH_TOKEN`
- `SIM_ALLOWED_DEVICE_IDS`
- `SIM_AVAILABLE_AGENTS`

## 7. Tradeoffs y consecuencias

### Positivas
- Desarrollo rapido con alto feedback visual.
- Contrato de protocolo estable antes de integrar hardware.
- Pruebas repetibles y diagnostico claro por logs/escenarios.
- Camino de migracion limpio a agentes reales (HTTP/WS).

### Costes
- El TTS local puede variar por plataforma (especialmente macOS).
- Whisper local consume CPU/RAM segun modelo; requiere tuning.
- Sin hardware real aun: faltan validaciones GPIO/pantalla fisica/latencias reales del dispositivo.

## 8. Riesgos abiertos

- Ajuste fino de STT en audio real de usuario (ruido, acento, VAD).
- Latencia total del pipeline para experiencia conversacional final.
- Politicas de reconexion y resiliencia en despliegue embebido.
- Alineacion final con driver/pantalla real del HAT en Raspberry Pi.

## 9. Reglas para futuras modificaciones (importante para IA)

1. No romper el contrato de `backend/shared/protocol.py` sin versionarlo y actualizar simuladores/tests.
2. Mantener `audio.chunk` como unidad de streaming (evitar blobs monoliticos).
3. Cualquier cambio de estados debe mantener coherencia UI/backend (`ui.state`).
4. Si se toca audio, correr al menos `scenario_runner --scenario audio-loopback`.
5. Si se toca flujo principal, correr `scenario_runner --scenario all`.
6. Mantener logs sanitizados (no imprimir payload base64 completo).

## 10. Estado de validacion al cerrar este ADR

- Regresion de escenarios (`baseline`, `interrupt`, `cancel`, `audio-loopback`) pasando en modo base.
- Modo `echo` validado con STT+TTS activos y audio de respuesta en chunks.
- Suite unitaria actualizada y pasando (`76 passed`, backend + simulator).
- Documentacion operativa centralizada en `RUNBOOK.md`.

## 11. Contexto estructurado (para IA)

```json
{
  "adr_id": "ADR-0001",
  "status": "accepted",
  "scope": "simulation_without_hardware",
  "core_stack": {
    "backend": "FastAPI + WebSocket",
    "simulators": ["CLI", "Tkinter UI"],
    "protocol_file": "backend/shared/protocol.py + simulator/shared/protocol.py"
  },
  "message_flow": {
    "inbound_from_device": [
      "device.hello",
      "recording.start",
      "audio.chunk",
      "debug.user_text",
      "recording.stop",
      "recording.cancel",
      "assistant.interrupt"
    ],
    "outbound_to_device": [
      "session.ready",
      "ui.state",
      "transcript.partial",
      "transcript.final",
      "assistant.start",
      "assistant.text.partial",
      "assistant.text.final",
      "assistant.audio.start",
      "assistant.audio.chunk",
      "assistant.audio.end",
      "error"
    ]
  },
  "audio_pipeline": {
    "capture": "PCM16 chunks from simulator",
    "reassembly": "temp PCM file per turn in backend",
    "stt": "faster-whisper (optional)",
    "tts": "say or pyttsx3 (optional)",
    "reply_modes": ["assistant", "echo"]
  },
  "integration_modes": {
    "openclawd": ["mock", "http", "ws"]
  },
  "critical_env": [
    "OPENCLAWD_MODE",
    "ENABLE_WHISPER_STT",
    "WHISPER_MODEL_SIZE",
    "ENABLE_LOCAL_TTS",
    "TTS_BACKEND",
    "AUDIO_REPLY_MODE",
    "SIM_DEVICE_AUTH_TOKEN",
    "SIM_ALLOWED_DEVICE_IDS"
  ],
  "regression": {
    "runner": "simulator/qa/scenario_runner.py",
    "required_scenarios": ["baseline", "interrupt", "cancel", "audio-loopback"]
  }
}
```

## 12. Referencias

- `README.md`
- `RUNBOOK.md`
- `OPENCLAWD_WS_SETUP.md`
- `MVP_ALIGNMENT.md`
- `backend/api.py`
- `backend/infrastructure/speech/speech_pipeline.py`
- `simulator/entrypoints/ui.py`
- `backend/infrastructure/ai/openclawd_adapter.py`
- `backend/shared/protocol.py`
- `simulator/qa/scenario_runner.py`
