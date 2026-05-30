# Frontend Specification

**Fecha:** 2026-05-30  
**Estado:** Borrador v1

---

## Concepto general

SPA ligera (React o Svelte) que muestra las notas del backend en tiempo real. Diseño minimalista orientado a captura rápida y consulta rápida. No es un gestor de proyectos: es un buzón inteligente de ideas habladas.

---

## Vistas principales

### 1. Feed de notas (vista por defecto)

```
┌────────────────────────────────────────────┐
│  🎙 note-taker          [● LIVE] [⚙ Config] │
├────────────────────────────────────────────┤
│  [Todas] [Tareas] [Ideas] [Recordatorios]  │
├────────────────────────────────────────────┤
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ NUEVA ↑                              │  │
│  │ ✅ TAREA · hace 2 min               │  │
│  │ Llamar al proveedor el jueves        │  │
│  │ #proveedor #llamada                  │  │
│  │                          [🔊] [🗑]   │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ 💡 IDEA · hace 1h                   │  │
│  │ La arquitectura hexagonal separa...  │  │
│  │ #arquitectura #dev                   │  │
│  │                          [🔊] [🗑]   │  │
│  └──────────────────────────────────────┘  │
│                                            │
└────────────────────────────────────────────┘
```

- Las notas nuevas aparecen en la parte superior con animación de entrada suave
- El indicador `● LIVE` muestra que el WebSocket está conectado
- Filtros por tipo (tab pills arriba)
- Cada tarjeta muestra: tipo, tiempo relativo, resumen, tags
- Botón de audio (🔊) reproduce el audio original
- Botón de borrar (🗑) con confirmación

---

### 2. Nota en modo DICTADO

Cuando llega una nota de tipo `dictation`, aparece en posición destacada:

```
┌────────────────────────────────────────────┐
│  📋 DICTADO · ahora                        │
│                                            │
│  La arquitectura hexagonal separa dominio  │
│  de infraestructura mediante puertos y     │
│  adaptadores, permitiendo que el dominio   │
│  no conozca los detalles de persistencia.  │
│                                            │
│              [ Copiar al portapapeles ]    │
└────────────────────────────────────────────┘
```

- El botón "Copiar" usa `navigator.clipboard.writeText()`
- Feedback visual inmediato al copiar ("¡Copiado!")
- La nota de dictado no desaparece hasta que el usuario la cierra o borra
- En el futuro, un daemon en el PC puede copiar automáticamente sin que el usuario toque el botón

---

### 3. Detalle de nota (clic en tarjeta)

```
┌────────────────────────────────────────────┐
│  ← Volver                                  │
├────────────────────────────────────────────┤
│  ✅ Tarea                                  │
│  30 mayo 2026, 10:34                       │
│                                            │
│  Transcripción completa:                   │
│  "recordar llamar al proveedor el jueves"  │
│                                            │
│  Resumen: Llamar al proveedor el jueves    │
│                                            │
│  Tags: #proveedor #llamada #pendiente      │
│                                            │
│  Entidades detectadas:                     │
│  · Fecha: jueves                           │
│  · Persona: proveedor                      │
│                                            │
│  Audio: ▶ [──────────●────] 0:04          │
│                                            │
│                          [Editar] [Borrar] │
└────────────────────────────────────────────┘
```

---

### 4. Estado del dispositivo (barra o widget)

Indicador en el header que muestra el estado actual del dispositivo en tiempo real:

| Estado dispositivo | Indicador en frontend |
|---|---|
| AMBIENT (conectado) | `● AMBIENT` en verde |
| SILENT | `⬜ SILENT` en gris |
| MANUAL RECORD | `● GRABANDO` en rojo parpadeante |
| Desconectado | `○ OFFLINE` en rojo |

---

## Comportamiento en tiempo real

- El frontend se conecta a `ws://{backend}/ws/client` al arrancar
- Cada evento `note.created` inserta la nota al principio del feed sin recargar
- Cada evento `device.status` actualiza el indicador del header
- Reconexión automática con backoff si se pierde el WebSocket

---

## Autenticación

- Token estático almacenado en `localStorage` bajo la clave `notes_token`
- En la primera visita (o si el token es inválido), se muestra un campo de texto para introducir el token
- Todas las peticiones REST llevan el header `Authorization: Bearer <token>`
- El WebSocket envía el token en el primer mensaje tras conectar: `{ "type": "auth", "token": "..." }`

---

## Consideraciones técnicas

| Decisión | Elección v1 | Razón |
|---|---|---|
| Framework | React 18 + Vite | Ecosistema maduro, build rápido |
| Alternativa más ligera | Svelte + Vite | Menos boilerplate, bundle más pequeño |
| Estado global | Zustand o Context API | No necesitamos Redux para esta escala |
| Estilos | Tailwind CSS | Prototipado rápido, sin custom CSS |
| WebSocket | `useWebSocket` hook custom | Control total sobre reconexión |
| Audio player | HTML5 `<audio>` nativo | Sin dependencias extra |

---

## Roadmap de funcionalidades del frontend

| Versión | Feature |
|---|---|
| v1 | Feed en tiempo real, filtros por tipo, detalle, borrado, dictado+copia |
| v1.5 | Búsqueda por texto, rango de fechas |
| v2 | Búsqueda semántica, edición de nota, cambio manual de tipo/tags |
| v3 | Modo vista kanban por tipo, exportar a Markdown/CSV |
