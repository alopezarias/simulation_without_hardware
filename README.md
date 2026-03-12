# Simulation without Hardware

Primera base funcional del proyecto de dispositivo conversacional, centrada en validar protocolo, estados, UX del boton e integracion backend sin hardware real.

## Componentes

- `backend.py`: servidor WebSocket con FastAPI.
- `openclawd_adapter.py`: adaptador encapsulado (modo `mock`, `http`/`real` o `ws`).
- `protocol.py`: utilidades de protocolo y estados compartidos.
- `simulator.py`: simulador CLI.
- `simulator_ui.py`: simulador con UI grafica (Tkinter) y mini pantalla estilo HAT (LED, red, bateria, texto enviado/recibido).
- `smoke_test.py`: prueba end-to-end automatizada.
- `scenario_runner.py`: ejecutor de escenarios de simulacion repetibles (baseline/interrupcion/cancelacion).

## Flujo MVP cubierto

- `device.hello` -> `session.ready`.
- `agent.select` y confirmacion `agent.selected`.
- `recording.start` / `recording.stop` / `recording.cancel`.
- `debug.user_text` para fase de simulacion logica.
- `audio.chunk` en streaming desde micro local (modo `mic` en UI).
- Respuesta en streaming con `assistant.text.partial` y `assistant.text.final`.
- Interrupcion con `assistant.interrupt`.
- Estados `idle`, `listening`, `processing`, `speaking`, `error`.
- Auth basica opcional por token de dispositivo.

## Arranque rapido

Consulta [RUNBOOK.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/RUNBOOK.md) para comandos completos por escenario.

## OpenClawd WebSocket

Configuracion detallada en [OPENCLAWD_WS_SETUP.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/OPENCLAWD_WS_SETUP.md).

## Encaje con especificacion final

Consulta [MVP_ALIGNMENT.md](/Users/user/Documents/projects/ai/ia_device/simulation_without_hardware/MVP_ALIGNMENT.md) para el contraste detallado entre este MVP y el documento final del proyecto.
