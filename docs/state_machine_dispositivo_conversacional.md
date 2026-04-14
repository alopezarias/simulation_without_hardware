# Maquina de Estados del Dispositivo Conversacional

## 1. Proposito del documento

Este documento define la maquina de estados visible del dispositivo para el modelo simplificado aprobado en `docs/functional_spec_simplified_device_interaction.md` y `docs/adr/ADR-0003-simplified-device-interaction-model.md`.

Reemplaza la semantica anterior basada en `LOCKED`, `READY`, `LISTEN`, `MENU`, `MODE` y `AGENTS` como modelo canonico de interaccion.

## 2. Eventos de entrada

El dispositivo dispone de un unico boton fisico y se consideran tres gestos funcionales:

| Gesto | Descripcion |
| --- | --- |
| `single_click` | Pulsacion corta y liberacion rapida |
| `double_click` | Dos pulsaciones cortas consecutivas |
| `press_hold` / `release` | Presion sostenida que activa escucha mientras se mantiene, y liberacion que finaliza captura |

## 3. Estados visibles canonicos

| Estado | Descripcion |
| --- | --- |
| `standby` | Dispositivo encendido, ocioso y listo |
| `listening` | Captura de audio activa solo mientras el boton sigue presionado |
| `calling` | Inicio breve de llamada saliente; la pantalla muestra `llamando` |
| `incoming_call` | Llamada iniciada por backend; muestra llamada entrante y tono distintivo |
| `config` | Modo de configuracion local |

`standby` es el estado base y de retorno.

## 4. Transiciones principales

### 4.1 Desde `standby`

| Evento | Resultado |
| --- | --- |
| `press_hold` | Entrar inmediatamente en `listening` |
| `single_click` | Enviar senal de llamada al backend y entrar en `calling` |
| `double_click` | Entrar en `config` |
| `backend_incoming_call` | Entrar en `incoming_call` |

### 4.2 Desde `listening`

| Evento | Resultado |
| --- | --- |
| mantener presionado | Permanecer en `listening` |
| `release` | Detener captura, empaquetar/enviar audio y volver a `standby` |

Regla importante: al liberar el boton no suena beep local de fin.

### 4.3 Desde `calling`

| Evento | Resultado |
| --- | --- |
| saludo de backend en reproduccion | Mantener flujo de llamada saliente |
| fin de reproduccion del saludo | Volver automaticamente a `standby` |

`calling` es transitorio. No deja un modo persistente despues del saludo.

### 4.4 Desde `config`

| Evento | Resultado |
| --- | --- |
| `backend_incoming_call` | `incoming_call` preempta `config` automaticamente |

Los detalles internos de `config` no se modelan aun en este documento.

### 4.5 Desde `incoming_call`

| Evento | Resultado |
| --- | --- |
| llamada entrante activa | Mantener aviso visual/sonoro de llamada |
| `press_hold` | Pasar a `listening` para hablar normalmente |
| `release` tras hablar | Detener captura, enviar audio y volver a `standby` |

## 5. Flujos nominales

### Hold-to-talk

`standby` -> `listening` mientras el boton esta presionado -> `release` -> envio de audio -> `standby`

### Llamada saliente

`standby` -> `single_click` -> `calling` (`llamando`) -> saludo backend -> fin playback -> beep de fin opcional -> `standby`

### Configuracion

`standby` -> `double_click` -> `config`

### Llamada entrante

`standby|config` -> `backend_incoming_call` -> `incoming_call` -> `press_hold` -> `listening` -> `release` -> envio de audio -> `standby`

## 6. Reglas de audio y feedback

- El beep de fin solo puede sonar cuando termina el playback de audio recibido desde backend.
- Nunca debe sonar beep por soltar el boton.
- `incoming_call` debe tener un sonido distinguible del audio normal de respuesta.

## 7. Fuera de alcance actual

- modelado interno de `config`
- cancelacion de llamada entrante antes de respuesta por parte del backend
- flujos legacy de bloqueo, menu, seleccion de modo o seleccion de agente como modelo canonico visible

## 8. Canonicalidad

Si otra documentacion del repositorio contradice este documento en materia de interaccion del boton o estados visibles, este documento y el ADR-0003 prevalecen.
