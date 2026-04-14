# ADR-0003: Modelo Simplificado de Interaccion Dispositivo/Backend

- Estado: `accepted`
- Fecha: `2026-04-11`
- Alcance: `/simulation_without_hardware`
- Audiencia: humana + agentes IA
- Supersede: logica previa de interaccion/estados del dispositivo, incluyendo la maquina documentada anteriormente en `docs/state_machine_dispositivo_conversacional.md`

## 1. Contexto

El proyecto arrastraba una semantica de interaccion demasiado amplia para el objetivo inmediato del dispositivo: `LOCKED`, `READY`, `LISTEN`, `MENU`, `MODE`, `AGENTS`, mas estados remotos como `idle`, `processing` y `speaking`.

Ese modelo introducia complejidad en:

- maquina local del runtime Raspberry
- UX del boton unico
- simulador UI/CLI
- protocolo entre dispositivo y backend
- pruebas de regresion

La nueva necesidad aprobada prioriza un flujo directo: hablar manteniendo pulsado, iniciar una llamada saliente con click simple, abrir configuracion con doble click y aceptar llamadas entrantes del backend con minima logica adicional.

## 2. Decision

Se adopta un modelo simplificado con cinco estados visibles canonicos:

- `standby`
- `listening`
- `calling`
- `incoming_call`
- `config`

Y con estas reglas estructurales:

1. `hold-to-talk` pasa a ser la interaccion principal.
2. `listening` solo existe mientras el boton permanezca fisicamente presionado.
3. un click simple inicia una llamada saliente breve y transitoria (`calling`), sin modo persistente posterior.
4. un doble click entra en `config`.
5. una llamada iniciada por backend entra en `incoming_call` y puede interrumpir `config`.
6. durante `incoming_call`, el usuario puede hablar con la misma mecanica de `press+hold`.
7. el beep de fin aplica solo al fin de reproduccion de audio recibido, nunca al release del boton.
8. se permite romper logica/protocolo legacy si eso simplifica el sistema.
9. no se modela por ahora el caso de cancelacion de llamada antes de respuesta por parte del backend.

## 3. Alternativas consideradas

### A. Mantener la maquina anterior y mapear nuevos gestos

- Ventaja: menos cambios inmediatos en codigo.
- Desventaja: preserva estados que ya no representan la UX aprobada y mantiene deuda conceptual.

### B. Mantener estados internos legacy pero ocultarlos en la UI

- Ventaja: transicion mas incremental.
- Desventaja: la documentacion y las pruebas seguirian ancladas a una semantica que ya no es la verdad funcional.

### C. Simplificar explicitamente y aceptar ruptura dirigida

- Ventaja: una sola fuente de verdad, menos ramificaciones, mejores tests y protocolo mas claro.
- Desventaja: requiere actualizar runtime, simulador, backend y contratos compartidos.

Decision: **C**.

## 4. Consecuencias

### Positivas

- Menor complejidad en la maquina local del dispositivo.
- Semantica del boton mas facil de aprender y testear.
- Menos estados ambiguos entre backend y frontend/runtime.
- Mejor alineacion con un dispositivo fisico tipo walkie/intercom.

### Costes

- El contrato actual de `UiState` y/o mensajes de protocolo puede requerir ruptura o reemplazo.
- Parte de la documentacion, tests y nombres de enums quedaran obsoletos hasta migrar.
- El modo `config` queda definido solo como entrada/salida, no en detalle.

## 5. Impacto esperado en el repositorio

- `device_runtime/src/device_runtime/domain/events.py`: reemplazar estados/eventos locales heredados por el nuevo modelo.
- `device_runtime/src/device_runtime/domain/state.py`: simplificar snapshot y campos asociados a menu/modo/agentes si dejan de ser visibles.
- `device_runtime/src/device_runtime/application/services/device_state_machine.py`: reescribir transiciones.
- `device_runtime/src/device_runtime/application/services/protocol_service.py`: mapear llamadas entrantes, saludo y fin de playback al nuevo modelo.
- `device_runtime/src/device_runtime/protocol/*` y `backend/shared/protocol.py`: ajustar enums/mensajes si el contrato simplificado rompe el anterior.
- `simulator/` y `device_runtime/tests/`: actualizar tests al nuevo flujo.

## 6. Regla de canonicalidad

Desde este ADR, la semantica canonica de interaccion del dispositivo es la del modelo simplificado.
Si algun documento, test o contrato previo entra en conflicto, debe considerarse desactualizado y debe migrarse hacia este ADR y la especificacion funcional asociada.

## 7. Notas de migracion

- No asumir compatibilidad hacia atras de nombres como `READY`, `MENU`, `MODE`, `AGENTS`, `processing` o `speaking` como modelo visible final.
- El backend puede seguir emitiendo eventos legacy temporalmente solo durante la migracion, pero la documentacion nueva no debe tratarlos como verdad canonica.
- La ausencia de "call cancel before answer" es deliberada, no un olvido.
