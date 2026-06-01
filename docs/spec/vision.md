# Vision: Intelligent Note-Taker Device

**Fecha:** 2026-05-30  
**Estado:** Borrador v1  
**Rama:** note_taker

---

## ¿Qué es esto?

Un dispositivo físico siempre activo que captura ideas habladas, las convierte en notas estructuradas y las pone disponibles en cualquiera de tus pantallas (web, móvil) sin que tengas que tocar un teclado.

La premisa central: **el pensamiento hablado es más rápido que el escrito, pero las notas escritas son más útiles que los audios**. Este dispositivo cierra ese gap.

---

## Casos de uso núcleo

### 1. Captura ambiental (modo principal)
El dispositivo está en la mesa. Tienes una idea. Dices la frase de activación, hablas, te callas. La nota aparece categorizada en el frontend segundos después. Cero fricción.

> *"hey jarvis, recordar llamar al proveedor el jueves"*  
> → nota tipo `tarea`, tag `pendiente`, entidad `jueves`

### 2. Dictado activo (modo Wispr Flow)
Tienes el frontend abierto en el PC. Activas el dispositivo, dictas texto, el texto transcrito aparece en el frontend listo para copiar. Sin teclado. Sin levantar las manos del pensamiento.

> *"hey jarvis, dictado: la arquitectura hexagonal separa dominio de infraestructura mediante puertos..."*  
> → nota tipo `dictado`, texto en portapapeles del frontend

### 3. Captura manual silenciosa
Estás en una reunión. No quieres hablar en voz alta. Mantienes el botón pulsado, susurras la nota, sueltas. El flujo es idéntico al modo 1 pero sin wake word.

---

## Lo que NO es (v1)

- No es un asistente conversacional (no hay turno de respuesta por defecto)
- No es multiusuario (un solo propietario, un solo dispositivo)
- No tiene app móvil nativa (el backend es API-first para soportarla en el futuro)
- No graba conversaciones completas; captura fragmentos intencionales

---

## Principios de diseño

| Principio | Aplicación concreta |
|---|---|
| **Zero-touch por defecto** | Wake word activa la captura sin botones |
| **API-first** | El backend expone REST + WebSocket; cualquier cliente puede beber de él |
| **Degradación elegante** | Si el backend no responde, el dispositivo guarda el audio localmente y reintenta |
| **Privacy by design** | El wake word se detecta on-device; solo el audio post-activación sale del dispositivo |
| **Arquitectura abierta al agente** | El pipeline de clasificación tiene un puerto para enchufar un agente LLM sin refactorizar |

---

## Visión de producto a 6 meses

```
v1 — MVP funcional
  ├── Wake word + captura + transcripción
  ├── Clasificación básica (tipo + tags)
  ├── Frontend web (lista de notas, copia)
  └── Dictado activo con copia al portapapeles

v2 — Agente y refinamiento
  ├── Agente LLM revisa la nota y puede preguntar por el altavoz
  ├── Tareas con fecha detectadas se añaden a calendario
  └── Búsqueda semántica sobre el histórico de notas

v3 — Multi-cliente
  ├── App móvil (iOS/Android) que bebe del mismo backend
  ├── Daemon de clipboard para PC (copia automática sin frontend abierto)
  └── Multi-dispositivo (varios micrófonos alimentan el mismo backend)
```

---

## Stack tecnológico (decisiones tomadas)

| Capa | Tecnología |
|---|---|
| Dispositivo | Raspberry Pi + Whisplay (pantalla + mic + altavoz) |
| Wake word | openWakeWord (on-device, sin cloud) |
| VAD | Silero VAD o WebRTC VAD |
| STT | Whisper (backend, fast/base model) |
| Backend | FastAPI + WebSocket + SQLite → PostgreSQL |
| Clasificación IA | Claude API (prompt estructurado, puerto abierto al agente) |
| Frontend | Web app (React o Svelte, SPA ligera) |
| Auth v1 | Token estático en header / variable de entorno |
