# Note Pipeline Specification

**Fecha:** 2026-05-30  
**Estado:** Borrador v1

---

## Visión general del flujo

```
DISPOSITIVO                    BACKEND                         CLIENTE
    │                             │                               │
    │── audio (WebSocket/HTTP) ──►│                               │
    │                             │── Whisper STT ──►             │
    │                             │── Clasificador ──►            │
    │                             │── DB (guardar nota) ──►       │
    │◄── ACK + preview ───────────│                               │
    │                             │── WebSocket push ────────────►│
    │                             │                               │ (nota nueva)
```

---

## Paso 1: Captura de audio en el dispositivo

- **Formato de captura:** WAV, 16kHz, 16-bit, mono (óptimo para Whisper)
- **Transmisión:** HTTP POST multipart (simple y robusto) o WebSocket binario
- **Metadatos enviados junto al audio:**

```json
{
  "device_id": "raspi-home",
  "capture_mode": "wake_word" | "manual",
  "duration_s": 4.2,
  "timestamp": "2026-05-30T10:34:00Z"
}
```

- **Fallback offline:** si no hay conexión, el dispositivo guarda el audio localmente en `/tmp/notes_queue/` y reintenta en cuanto se restaura la conexión (cola simple por orden de fichero).

---

## Paso 2: Transcripción con Whisper

- **Modelo por defecto:** `whisper-base` (equilibrio velocidad/precisión, ~70MB)
- **Modelo recomendado en producción:** `whisper-small` (mejor con acentos y ruido)
- **Idioma:** auto-detect por defecto, configurable via `WHISPER_LANGUAGE=es`
- **Output:** texto limpio + segmentos con timestamps (útiles para el futuro)

```json
{
  "text": "recordar llamar al proveedor el jueves",
  "language": "es",
  "duration": 4.2,
  "segments": [...]
}
```

---

## Paso 3: Clasificación por IA

El clasificador recibe el texto transcrito y devuelve metadatos estructurados.

**Tipos de nota:**

| Tipo | Descripción | Ejemplos |
|---|---|---|
| `task` | Algo que hay que hacer | "llamar a X", "revisar Y" |
| `idea` | Pensamiento creativo o especulativo | "y si el dispositivo pudiese...", "concepto para..." |
| `reminder` | Nota temporal/urgente | "mañana a las 10", "antes del viernes" |
| `note` | Dato o información factual | "la contraseña es...", "el teléfono de X es..." |
| `dictation` | Texto para copiar directamente | modo dictado activo |

**Prompt de clasificación (v1 — simple y determinista):**

```
Analiza la siguiente nota de voz transcrita y responde en JSON con:
- type: uno de [task, idea, reminder, note, dictation]
- tags: lista de strings (máx 5, minúsculas, sin espacios)
- entities: fechas, nombres, lugares mencionados
- summary: resumen en ≤ 10 palabras

Nota: "{text}"
```

**Output esperado:**

```json
{
  "type": "task",
  "tags": ["proveedor", "llamada", "pendiente"],
  "entities": {"date": "jueves", "person": "proveedor"},
  "summary": "Llamar al proveedor el jueves"
}
```

---

## Paso 4: Almacenamiento

### Esquema de base de datos (SQLite → PostgreSQL)

```sql
CREATE TABLE notes (
    id          TEXT PRIMARY KEY,          -- UUID v4
    device_id   TEXT NOT NULL,
    text        TEXT NOT NULL,             -- transcripción completa
    type        TEXT NOT NULL,             -- task | idea | reminder | note | dictation
    tags        TEXT,                      -- JSON array
    entities    TEXT,                      -- JSON object
    summary     TEXT,
    audio_path  TEXT,                      -- ruta relativa al audio original
    duration_s  REAL,
    capture_mode TEXT,                     -- wake_word | manual
    created_at  TEXT NOT NULL,             -- ISO 8601
    synced_at   TEXT                       -- cuando se procesó completamente
);
```

- Los audios se guardan en `data/audio/{YYYY-MM}/{note_id}.wav`
- El audio se retiene por defecto 30 días, configurable con `NOTES_AUDIO_RETENTION_DAYS`
- Las notas de texto son permanentes salvo borrado explícito

---

## Paso 5: Notificación al cliente

Inmediatamente después de guardar la nota, el backend emite un evento WebSocket a todos los clientes conectados:

```json
{
  "event": "note.created",
  "data": {
    "id": "abc-123",
    "type": "task",
    "summary": "Llamar al proveedor el jueves",
    "text": "recordar llamar al proveedor el jueves",
    "tags": ["proveedor", "llamada"],
    "created_at": "2026-05-30T10:34:05Z"
  }
}
```

---

## Casos especiales

### Dictado activo
- El campo `capture_mode` es `wake_word` pero la nota empieza con una keyword de dictado (configurable, e.g. "dictado:", "escribir:")
- El clasificador fuerza `type: dictation`
- El frontend muestra la nota en posición destacada con botón "Copiar"
- Futuro: el daemon de clipboard en el PC copia automáticamente al portapapeles del sistema

### Audio no inteligible
- Whisper devuelve texto vacío o con confianza muy baja
- El backend guarda igualmente el audio con `type: null`, `text: ""`
- El cliente recibe un evento `note.unrecognized` con el audio disponible para escuchar manualmente

### Timeout de red desde el dispositivo
- El dispositivo reintenta la subida hasta 3 veces con backoff exponencial (2s, 4s, 8s)
- Si los 3 reintentos fallan, el audio se encola localmente
- El dispositivo muestra "Sin conexión" en el display
- La cola se procesa en orden FIFO cuando se restaura la conexión

---

## Variables de entorno relevantes

| Variable | Por defecto | Descripción |
|---|---|---|
| `WHISPER_MODEL` | `base` | Modelo de Whisper a cargar |
| `WHISPER_LANGUAGE` | `auto` | Fuerza idioma (ej: `es`) |
| `NOTES_AUDIO_RETENTION_DAYS` | `30` | Días de retención de audio |
| `NOTES_DB_URL` | `sqlite:///data/notes.db` | URL de la base de datos |
| `NOTES_AUDIO_DIR` | `data/audio` | Directorio de audios |
| `AI_CLASSIFIER_MODEL` | `claude-haiku-4-5` | Modelo para clasificación |
