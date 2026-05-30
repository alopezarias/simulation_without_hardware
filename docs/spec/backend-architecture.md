# Backend Architecture Specification

**Fecha:** 2026-05-30  
**Estado:** Borrador v1

---

## Diagrama de componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                         BACKEND (FastAPI)                       │
│                                                                 │
│  ┌────────────┐   ┌─────────────┐   ┌──────────────────────┐   │
│  │ /audio     │   │ /ws/device  │   │ /ws/client           │   │
│  │ (POST)     │   │ (WebSocket) │   │ (WebSocket)          │   │
│  │ multipart  │   │ dispositivo │   │ frontend / móvil     │   │
│  └─────┬──────┘   └──────┬──────┘   └──────────────────────┘   │
│        │                 │                     ▲                │
│        └────────┬─────────┘                    │                │
│                 ▼                              │                │
│        ┌─────────────────┐                    │                │
│        │  AudioIngestion │                    │                │
│        │  Service        │                    │                │
│        └────────┬────────┘                    │                │
│                 │                             │                │
│        ┌────────▼────────┐         ┌──────────┴───────────┐    │
│        │  Whisper STT    │         │  NotificationService │    │
│        │  (port)         │         │  (WebSocket fan-out) │    │
│        └────────┬────────┘         └──────────────────────┘    │
│                 │                             ▲                │
│        ┌────────▼────────┐                   │                │
│        │  NoteClassifier │                   │                │
│        │  (port → Claude)│                   │                │
│        └────────┬────────┘                   │                │
│                 │                            │                │
│        ┌────────▼────────┐                   │                │
│        │  NoteRepository │───────────────────┘                │
│        │  (SQLite/PG)    │                                     │
│        └─────────────────┘                                     │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ AgentPort (futuro v2) — enchufe para agente LLM          │   │
│  │ recibe nota, puede generar respuesta → TTS → dispositivo │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
          ▲                                      ▲
          │                                      │
   [Dispositivo Pi]                    [Frontend web / app móvil]
```

---

## Endpoints REST

### POST `/audio/capture`
Recibe audio del dispositivo y dispara el pipeline completo.

**Request:** `multipart/form-data`
- `audio`: fichero WAV (16kHz, 16-bit, mono)
- `metadata`: JSON string con `device_id`, `capture_mode`, `timestamp`

**Response 202 Accepted:**
```json
{
  "note_id": "abc-123",
  "status": "processing"
}
```

El procesamiento es asíncrono. El resultado llega por WebSocket cuando está listo.

---

### GET `/notes`
Lista de notas con filtrado y paginación.

**Query params:** `type`, `tags`, `from`, `to`, `page`, `limit` (default 50)

**Response 200:**
```json
{
  "items": [...],
  "total": 142,
  "page": 1,
  "pages": 3
}
```

---

### GET `/notes/{note_id}`
Detalle de una nota.

---

### DELETE `/notes/{note_id}`
Borra nota y su audio asociado.

---

### GET `/notes/{note_id}/audio`
Devuelve el audio original en streaming.

---

### GET `/health`
Estado del servicio + disponibilidad de Whisper.

```json
{
  "status": "ok",
  "whisper_loaded": true,
  "db": "ok",
  "version": "0.1.0"
}
```

---

## WebSocket `/ws/device`

Canal de comunicación bidireccional con el dispositivo.

**Dispositivo → Backend:**
```json
{ "type": "audio_chunk", "data": "<base64>" }
{ "type": "audio_end", "metadata": {...} }
{ "type": "ping" }
{ "type": "status_update", "mode": "ambient" | "silent" | "manual" }
```

**Backend → Dispositivo:**
```json
{ "type": "pong" }
{ "type": "note_ack", "note_id": "abc", "summary": "Llamar al proveedor" }
{ "type": "error", "code": "stt_failed", "message": "..." }
{ "type": "agent_response", "text": "...", "audio_url": "..." }  // v2
```

---

## WebSocket `/ws/client`

Canal de notificaciones en tiempo real para el frontend y la app móvil.

**Backend → Cliente:**
```json
{ "event": "note.created", "data": { <nota completa> } }
{ "event": "note.deleted", "data": { "id": "abc" } }
{ "event": "device.status", "data": { "mode": "ambient", "connected": true } }
```

**Cliente → Backend:**
```json
{ "type": "subscribe", "filters": { "types": ["task", "idea"] } }
{ "type": "ping" }
```

---

## Capa de dominio — puertos (hexagonal)

```
application/ports/
├── stt_port.py          # SttService.transcribe(audio_bytes) → TranscriptionResult
├── classifier_port.py   # ClassifierService.classify(text) → NoteMetadata
├── note_repository.py   # NoteRepository.save/get/list/delete
├── audio_store.py       # AudioStore.save(note_id, bytes) → path
└── notifier.py          # Notifier.broadcast(event, data)

infrastructure/adapters/
├── whisper_stt.py       # impl de SttPort con Whisper
├── null_stt.py          # impl mock para tests
├── claude_classifier.py # impl de ClassifierPort con Claude API
├── null_classifier.py   # impl mock (devuelve tipo "note" sin llamar a IA)
├── sqlite_notes.py      # impl de NoteRepository
└── ws_notifier.py       # impl de Notifier via WebSocket fan-out
```

---

## AgentPort (v2 — diseñado, no implementado en v1)

El puerto del agente se define en v1 como interfaz pero su implementación queda como `null_agent.py` (no hace nada).

```python
class AgentPort(ABC):
    async def process(self, note: Note) -> AgentResponse | None:
        """Analiza la nota. Devuelve respuesta si el agente quiere actuar, None si no."""
        ...
```

El pipeline lo invoca después de clasificar:
```
transcripción → clasificación → [agente] → guardar → notificar
```

En v1 el agente siempre devuelve `None`. En v2 puede responder por audio al dispositivo, crear tareas en un gestor externo, o pedir clarificación.

---

## Autenticación (v1)

Token estático en header `Authorization: Bearer <token>`. El token se define en `NOTES_API_TOKEN` en `.env`. Sin rotación automática en v1.

El frontend lo guarda en `localStorage`. La app móvil lo guarda en el keychain del sistema.

---

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `NOTES_API_TOKEN` | — | Token de acceso (obligatorio) |
| `NOTES_DB_URL` | `sqlite:///data/notes.db` | URL de la DB |
| `NOTES_AUDIO_DIR` | `data/audio` | Directorio de ficheros de audio |
| `WHISPER_MODEL` | `base` | Modelo de Whisper |
| `WHISPER_LANGUAGE` | `auto` | Idioma forzado o auto |
| `AI_CLASSIFIER_ENABLED` | `true` | Deshabilitar para MVP sin API key |
| `AI_CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Modelo Claude para clasificación |
| `ANTHROPIC_API_KEY` | — | API key de Anthropic |
| `HOST` | `0.0.0.0` | Host de escucha |
| `PORT` | `8000` | Puerto de escucha |
