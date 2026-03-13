# ADR-0002: Estrategia de Testing del Simulador y Plan de Refactor Hexagonal

- Estado: `accepted`
- Fecha: `2026-03-13`
- Alcance: `/simulation_without_hardware` (simulador CLI/UI)
- Audiencia: humana + agentes IA

## 1. Contexto

Tras estabilizar el backend en arquitectura hexagonal, el siguiente riesgo principal del proyecto esta en el simulador, porque concentra logica de dominio de interaccion, transporte WS, audio y representacion UI en dos modulos grandes.

Objetivo de este ADR:
- Definir inventario completo de ficheros del simulador.
- Establecer una red de seguridad con tests unitarios antes del refactor.
- Definir plan de refactor a arquitectura hexagonal para el simulador.

## 2. Inventario de ficheros relacionados

### Nucleo del simulador
- `simulator/entrypoints/cli.py` (simulador CLI y control de eventos por comandos)
- `simulator/entrypoints/ui.py` (simulador Tkinter con WS, audio in/out y preview del dispositivo)
- `simulator/shared/protocol.py` (tipos de mensaje y utilidades compartidas del lado simulador)
- `backend/shared/protocol.py` (contrato equivalente del backend)

### Verificacion automatizada
- `simulator/qa/scenario_runner.py` (escenarios E2E de protocolo)
- `simulator/qa/smoke_test.py` (smoke E2E minimo)

### Documentacion operativa
- `RUNBOOK.md`
- `README.md`

## 3. Riesgos detectados en la implementacion actual

1. `simulator/entrypoints/ui.py` mezcla estado, transporte WS, captura microfono, reproduccion audio y render de interfaz en una sola clase grande.
2. Existe acoplamiento fuerte entre handlers de protocolo y widgets Tk.
3. El comportamiento de audio tiene rutas delicadas (colas, truncado, loop de flush y reproduccion) con alto riesgo de regresion.
4. En CLI y UI hay logica duplicada de manejo de mensajes de protocolo.

## 4. Decision: red de seguridad de tests unitarios previa al refactor

Se introduce una suite unitaria especifica para simulador, desacoplada de hardware real y de display, usando dobles de prueba para:
- WebSocket
- entradas/salidas de audio
- variables/estado UI

### Tests introducidos
- `simulator/tests/test_simulator_cli_unit.py`
- `simulator/tests/test_simulator_ui_unit.py`

### Cobertura funcional alcanzada

#### CLI (`simulator/entrypoints/cli.py`)
- Estado/agente: `SimulatorState.active_agent`, `set_agent`.
- Interacciones: `tap`, `double_tap`, `long_press`, `send_debug_text`.
- Recepcion de mensajes: `receiver_loop` (session, ui.state, transcript, assistant, error).
- Infraestructura de bucles: `ping_loop`, `command_loop`.
- Parsing de argumentos: `parse_args`.

#### UI (`simulator/entrypoints/ui.py`)
- Modelo: `UiStateModel.set_agent`.
- Worker WS: `WsWorker.send`, `stop`, `_send_loop`, `_recv_loop`.
- Audio entrada: `MicAudioStreamer.pop_chunks`.
- Audio salida: `AudioOutputPlayer.push` (control de overflow).
- Helpers UI/logica: `_wire_safe_payload`, `_battery_color`, `_battery_dot_color`, `_wrap_for_display`, `_scrolling_message_lines`.
- Handlers de conexion/backend: `_handle_connection_event`, `_handle_backend_message`.
- Streaming de micro: `_flush_mic_chunks`.
- Cierre de audio de salida: `_maybe_finish_audio_playback`.

## 5. Estado de validacion

- Suite total del repositorio: `76 passed`.
- Suite de simulador (nueva): `29 passed`.
- Sin dependencia de microfono fisico ni display para tests unitarios.

## 6. Estrategia de refactor hexagonal del simulador

Estado actual de ejecucion:
- S1 iniciado y aplicado parcialmente: `SimulatorState` y `UiStateModel` ya viven en `simulator/domain/state.py`.
- CLI y UI mantienen compatibilidad importando esos modelos desde la capa de dominio.

## 6.1 Objetivo arquitectonico

Separar el simulador en capas para poder cambiar transporte WS, backend remoto, preview UI o motor de audio sin tocar casos de uso.

## 6.2 Estructura objetivo propuesta

```text
simulator/
  config/
    settings.py
  domain/
    state.py
    events.py
  application/
    ports.py
    services/
      interaction_service.py      # tap/double/long/send_text
      protocol_service.py         # aplicacion de mensajes RX al estado
      audio_tx_service.py         # chunking y flush de micro
      audio_rx_service.py         # reproduccion de audio RX
  infrastructure/
    ws/
      ws_client.py                # worker websocket
    audio/
      microphone_adapter.py
      speaker_adapter.py
    ui/
      tk_view_adapter.py          # render estado en widgets
      hat_preview_renderer.py
  entrypoints/
    cli_main.py
    ui_main.py
```

## 6.3 Plan de migracion por fases

### Fase S1: Extraer dominio puro
- Mover `SimulatorState` y `UiStateModel` a `simulator/domain/state.py`.
- Consolidar reglas de agente y transiciones basicas.
- Criterio: tests de estado en verde sin Tk ni WS.

### Fase S2: Extraer servicios de interaccion
- Mover `tap`, `double_tap`, `long_press`, envio de texto a `interaction_service.py`.
- Eliminar logica duplicada CLI/UI compartiendo casos de uso.
- Criterio: tests de comandos CLI y handlers UI siguen pasando.

### Fase S3: Extraer servicio de protocolo RX
- Mover parse/aplicacion de `session.ready`, `ui.state`, `transcript.*`, `assistant.*`, `error` a `protocol_service.py`.
- Criterio: misma semantica en CLI y UI con un solo punto de verdad.

### Fase S4: Extraer audio TX/RX
- Encapsular `MicAudioStreamer` y `AudioOutputPlayer` en adapters de infraestructura.
- Crear servicios de aplicacion para flush/playback, evitando dependencia directa de Tk.
- Criterio: tests de audio unitarios + escenario `audio-loopback` sin regresion.

### Fase S5: Adaptadores de entrada
- `entrypoints/cli_main.py` y `entrypoints/ui_main.py` como composition roots.
- Mantener wrappers legacy (`simulator/entrypoints/cli.py`, `simulator/entrypoints/ui.py`) temporalmente para compatibilidad.
- Criterio: comandos de ejecucion actuales siguen funcionando.

### Fase S6: Limpieza final
- Reducir `simulator/entrypoints/cli.py` y `simulator/entrypoints/ui.py` a fachadas minimas o redireccionar a entrypoints nuevos.
- Actualizar docs y tests por capa.

## 7. Reglas de seguridad durante el refactor

1. No romper el contrato de `backend/shared/protocol.py`.
2. Cualquier cambio en audio debe validar unit tests + `python -m simulator.qa.scenario_runner --scenario audio-loopback`.
3. Cualquier cambio de handlers debe validar unit tests + `python -m simulator.qa.scenario_runner --scenario all`.
4. Mantener sanitizacion de payload base64 en logs/wire terminal.

## 8. Comandos de verificacion

```bash
source .venv/bin/activate
pytest -q simulator/tests/test_simulator_cli_unit.py simulator/tests/test_simulator_ui_unit.py
pytest -q
```

## 9. Resultado esperado tras aplicar este ADR

- Simulador con capas legibles y mantenibles.
- Reutilizacion de casos de uso entre CLI y UI.
- Menor riesgo de regresiones gracias a cobertura unitaria previa y posterior al refactor.
