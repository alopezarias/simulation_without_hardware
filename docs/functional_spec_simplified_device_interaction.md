# Especificacion Funcional: Modelo Simplificado de Interaccion Dispositivo/Backend

## 1. Objetivo

Definir el nuevo comportamiento funcional canonico para la interaccion entre el dispositivo tipo Raspberry y el backend, priorizando una UX mas simple basada en un unico boton fisico.

## 2. Alcance

Este documento define:

- estados visibles del dispositivo
- semantica del boton (click simple, doble click, press+hold)
- comportamiento de audio y pantalla
- reglas de preemption entre estados
- limites deliberados del modelo

No define aun los detalles internos del modo `config`.

## 3. Estados visibles canonicos

| Estado | Significado | Pantalla esperada | Audio esperado |
| --- | --- | --- | --- |
| `standby` | Dispositivo encendido y en espera | Estado reposo | Sin audio activo |
| `listening` | Captura activa solo mientras el boton sigue presionado | Indicacion de escucha | Captura micro activa |
| `calling` | Inicio breve de llamada saliente tras click simple | `llamando` | Puede haber tono breve opcional; no conversacion aun |
| `incoming_call` | Llamada iniciada por backend | Aviso de llamada entrante | Sonido distintivo de llamada |
| `config` | Modo de configuracion local | Vista/config copy a definir | Sin comportamiento interno definido aun |

## 4. Semantica del boton

| Gesto | Resultado |
| --- | --- |
| Press+hold | Entra inmediatamente en `listening` |
| Mantener presionado | Permanece en `listening` |
| Soltar tras hold | Detiene captura, empaqueta/envia audio y vuelve a `standby` |
| Click simple | Inicia llamada saliente; entra brevemente en `calling` |
| Doble click | Entra en `config` |

## 5. Requerimientos funcionales

### RF-1. Standby como estado base

El dispositivo DEBE usar `standby` como estado estable por defecto cuando no este escuchando, llamando, atendiendo una llamada entrante ni en configuracion.

### RF-2. Hold-to-talk estricto

El dispositivo DEBE entrar en `listening` en cuanto detecta `press+hold`.
El dispositivo DEBE permanecer en `listening` solo mientras el boton siga fisicamente presionado.
Al liberar el boton, el dispositivo DEBE:

1. detener la captura
2. empaquetar y enviar el audio acumulado al backend
3. volver a `standby`

La liberacion del boton NO DEBE disparar ningun beep local.

### RF-3. Click simple inicia llamada saliente breve

Un click simple DEBE enviar una senal de llamada al backend.
Tras esa accion, el dispositivo DEBE entrar en `calling` durante una ventana breve de inicio y mostrar `llamando`.
Cuando el backend responda con el saludo y finalice su reproduccion, el dispositivo DEBE volver a `standby`.
No DEBE quedar un modo especial persistente despues del saludo.

### RF-4. Doble click entra en configuracion

Un doble click DEBE mover el dispositivo a `config`.
Los subestados, menus o acciones internas de `config` quedan fuera de esta version.

### RF-5. Llamada iniciada por backend

Cuando el backend inicie una llamada, el dispositivo DEBE entrar en `incoming_call`.
La pantalla DEBE mostrar una llamada entrante y el dispositivo DEBE reproducir un sonido distintivo de llamada.

### RF-6. Preemption de `incoming_call`

Si el dispositivo esta en `config` y llega una llamada del backend, `incoming_call` DEBE interrumpir `config` automaticamente.

### RF-7. Hablar normalmente durante `incoming_call`

Mientras el dispositivo este en `incoming_call`, el usuario DEBE poder presionar y mantener para hablar usando la misma semantica de `hold-to-talk`.
Esa accion DEBE transicionar a `listening` sin requerir pasos intermedios adicionales.

### RF-8. Beep solo al terminar playback recibido

Si existe beep de fin de reproduccion, este DEBE sonar solo cuando termina la reproduccion de audio recibido desde backend.
El beep NO DEBE sonar cuando el usuario suelta el boton.

### RF-9. Compatibilidad subordinada a simplicidad

La implementacion PUEDE romper la logica o el protocolo anterior si eso simplifica el modelo y reduce estados/interacciones innecesarias.

### RF-10. No modelar cancelacion temprana de llamada

El sistema NO DEBE modelar el caso de "backend cancela la llamada antes de que el usuario responda" salvo que aparezca una necesidad concreta posterior.

## 6. Flujos nominales

### 6.1 Hold-to-talk

`standby` -> usuario mantiene boton -> `listening` -> usuario libera -> envio de audio -> `standby`

### 6.2 Llamada saliente por click simple

`standby` -> click simple -> `calling` (`llamando`) -> saludo del backend -> fin playback -> beep opcional de fin -> `standby`

### 6.3 Entrada a configuracion

`standby` -> doble click -> `config`

### 6.4 Llamada entrante desde backend

`standby|config` -> evento backend -> `incoming_call` -> press+hold del usuario -> `listening` -> release -> envio de audio -> `standby`

## 7. Reglas de UX y presentacion

- `listening` solo es visible mientras el boton esta presionado.
- `calling` es transitorio y no representa una sesion persistente.
- `incoming_call` debe diferenciarse visual y sonoramente de la respuesta normal del asistente.
- El retorno a `standby` tras saludo o tras envio de audio debe ser automatico.

## 8. Fuera de alcance en esta iteracion

- detalle interno de `config`
- flujos de bloqueo/desbloqueo
- seleccion de agente por boton
- menus multinivel
- cancelacion de llamada entrante antes de respuesta

## 9. Canonicalidad

Este documento es la referencia funcional para futuras decisiones de protocolo, runtime Raspberry, simulador y tests del modelo simplificado.
