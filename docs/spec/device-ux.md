# Device UX Specification

**Fecha:** 2026-05-30  
**Estado:** Borrador v1

---

## Estados del dispositivo

El dispositivo tiene tres modos de operación. El estado activo siempre se muestra en pantalla.

```
┌─────────────────────────────────────┐
│           MODOS OPERATIVOS          │
│                                     │
│  AMBIENT ──tap──► SILENT            │
│    ▲                  │             │
│    └────────tap───────┘             │
│                                     │
│  Cualquier estado + hold (1s+)      │
│    ──────────────────► MANUAL       │
│    (suelta el botón → vuelve al     │
│     estado anterior)                │
└─────────────────────────────────────┘
```

### AMBIENT (por defecto al arrancar)

- El detector de wake word está activo permanentemente
- Consumo de CPU bajo gracias a openWakeWord (modelo ligero)
- Display: icono de micrófono animado sutilmente (ondas)

### SILENT

- El detector de wake word está pausado
- El dispositivo no captura nada
- Display: icono de micrófono con línea/barra encima
- Útil en reuniones, llamadas, o cuando se quiere privacidad

### MANUAL RECORD

- Se activa manteniendo el botón pulsado ≥ 1 segundo
- Graba mientras se mantiene el botón
- Al soltar → envía el audio al backend
- Display: círculo rojo de grabación + tiempo transcurrido
- Al soltar → vuelve al estado anterior (AMBIENT o SILENT)

---

## Botón físico — tabla de comportamientos

| Acción | Duración | Desde cualquier estado | Resultado |
|---|---|---|---|
| Tap | < 300ms | AMBIENT | → SILENT |
| Tap | < 300ms | SILENT | → AMBIENT |
| Hold | ≥ 1s | AMBIENT o SILENT | → MANUAL RECORD (mientras se mantiene) |
| Release | — | MANUAL RECORD | envía audio, vuelve al estado anterior |

---

## Flujo wake word (modo AMBIENT)

```
[siempre escuchando]
       │
       ▼ wake word detectada
[beep corto de confirmación 🔔]
[display: "escuchando..."]
       │
       ▼ VAD detecta voz
[grabando...]
       │
       ▼ VAD detecta silencio (≥ 1.5s)
[beep doble de fin 🔔🔔]
[display: "procesando..."]
       │
       ▼ backend confirma recepción
[display: tipo de nota + preview 2s]
[vuelve a estado AMBIENT]
```

Timeout de seguridad: si no se detecta voz en 4s después del wake word, cancela y vuelve a escuchar.

---

## Flujo manual record (botón hold)

```
[hold ≥ 1s]
[beep largo de inicio 🎵]
[display: ● REC 0:00]
       │
       ▼ [grabando mientras se mantiene]
[release]
[beep doble 🔔🔔]
[display: "enviando..."]
       │
       ▼ backend confirma
[display: tipo de nota + preview 2s]
[vuelve a estado anterior]
```

---

## Display — estados visuales

El display es pequeño (e-ink o TFT según el módulo Whisplay). Prioridad: legibilidad sobre decoración.

| Estado dispositivo | Pantalla |
|---|---|
| AMBIENT (idle) | Logo + icono mic + hora |
| Wake word detectada | "Escuchando..." + onda animada |
| Grabando | "Grabando..." + cronómetro |
| Procesando | "Procesando..." + spinner |
| Nota recibida | Tipo + primeras palabras (truncadas) |
| SILENT | Logo + mic tachado + hora |
| MANUAL REC | "● REC" + cronómetro |
| Sin conexión | "Sin conexión" + tiempo offline |
| Error | Mensaje corto de error |

---

## Feedback de audio

Tonos simples (sine wave cortos generados en el dispositivo, no samples):

| Evento | Tono |
|---|---|
| Wake word detectado | 880 Hz, 100ms |
| Fin de grabación / envío | 880 Hz + 1320 Hz, 80ms c/u |
| Nota procesada OK | 1320 Hz, 150ms |
| Error | 440 Hz, 300ms |
| Modo SILENT activado | silencio (sin tono, solo display) |

---

## Wake word

**MVP:** wake word genérica preentrenada del repositorio openWakeWord.  
Candidatas disponibles: `hey_jarvis`, `alexa` (evitar por conflicto), `hey_mycroft`.  
**Recomendación MVP:** `hey_jarvis` — modelo preentrenado disponible, latencia < 200ms en Pi 4.

**Futuro:** wake word personalizada entrenada con Porcupine Console (gratuita para uso personal).

---

## Parámetros de VAD (Voice Activity Detection)

| Parámetro | Valor por defecto | Configurable via env |
|---|---|---|
| Silencio para cerrar grabación | 1.5 s | `DEVICE_VAD_SILENCE_MS` |
| Timeout sin voz tras wake word | 4.0 s | `DEVICE_VAD_TIMEOUT_S` |
| Duración mínima de grabación | 0.5 s | `DEVICE_VAD_MIN_DURATION_S` |
| Duración máxima de grabación | 60 s | `DEVICE_VAD_MAX_DURATION_S` |
