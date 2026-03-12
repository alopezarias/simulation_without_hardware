# Simulation without Hardware

Primera base funcional del proyecto de dispositivo conversacional, centrada en validar protocolo, estados, UX del boton e integracion backend sin hardware real.

## Componentes

- `app/backend.py`: fachada de compatibilidad del backend y entrypoint para `uvicorn app.backend:app`.
- `app/`: arquitectura hexagonal (config, dominio, aplicacion, infraestructura).
- `openclawd_adapter.py`: cliente OpenClawd usado por el adapter de infraestructura.
- `speech_pipeline.py`: pipeline local de voz usado por el adapter de infraestructura.
- `protocol.py`: utilidades de protocolo y estados compartidos.
- `simulator.py`: simulador CLI.
- `simulator_ui.py`: simulador con UI grafica (Tkinter) y mini pantalla estilo HAT (LED, red, bateria, texto enviado/recibido).
- `smoke_test.py`: prueba end-to-end automatizada.
- `scenario_runner.py`: ejecutor de escenarios de simulacion repetibles (baseline/interrupcion/cancelacion).

## Estructura hexagonal

- `app/config/settings.py`: configuracion runtime desde entorno.
- `app/domain/session.py`: estado de sesion del dispositivo.
- `app/application/ports.py`: puertos de IA, voz, salida y audio-store.
- `app/application/services/*`: casos de uso (`message_bus`, `recording`, `turn_processing`, `message_router`, `session_init`).
- `app/infrastructure/*`: adapters concretos (WebSocket, OpenClawd, Speech, audio temporal, logging).

## Flujo MVP cubierto

- `device.hello` -> `session.ready`.
- `agent.select` y confirmacion `agent.selected`.
- `recording.start` / `recording.stop` / `recording.cancel`.
- `debug.user_text` para fase de simulacion logica.
- `audio.chunk` en streaming desde micro local (modo `mic` en UI).
- `audio.chunk` en streaming desde micro local (auto al hacer `Tap`, y tambien con botones `Abrir Mic` / `Cerrar Mic`).
- Reensamblado de audio en backend (archivo temporal PCM).
- Transcripcion local con Whisper (`faster-whisper`) y sintesis local TTS (`pyttsx3`).
- Streaming de audio de respuesta `assistant.audio.*` por chunks hacia el simulador.
- Modo de respuesta configurable: `AUDIO_REPLY_MODE=assistant` (respuesta del agente) o `AUDIO_REPLY_MODE=echo` (repite lo transcrito en audio).
- Respuesta en streaming con `assistant.text.partial` y `assistant.text.final`.
- Interrupcion con `assistant.interrupt`.
- Estados `idle`, `listening`, `processing`, `speaking`, `error`.
- Auth basica opcional por token de dispositivo.

## Arranque rapido

Consulta [RUNBOOK.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/RUNBOOK.md) para comandos completos por escenario.

## Tests unitarios (backend)

Instalacion:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Ejecucion completa:

```bash
pytest
```

Nota: `pytest.ini` ya fuerza `-p no:capture` para evitar un `segfault` del plugin de captura en este entorno.

## OpenClawd WebSocket

Configuracion detallada en [OPENCLAWD_WS_SETUP.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/OPENCLAWD_WS_SETUP.md).

## Encaje con especificacion final

Consulta [MVP_ALIGNMENT.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/MVP_ALIGNMENT.md) para el contraste detallado entre este MVP y el documento final del proyecto.

## ADR

Documento de arquitectura y evolucion: [ADR-0001](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/docs/adr/ADR-0001-foundations-and-evolution.md).
