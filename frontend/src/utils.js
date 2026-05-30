export const NOTE_TYPES = {
  task:      { label: 'Tarea',         icon: '✅', color: 'text-green-400',  border: 'border-green-800',  bg: 'bg-green-950/40'  },
  idea:      { label: 'Idea',          icon: '💡', color: 'text-purple-400', border: 'border-purple-800', bg: 'bg-purple-950/40' },
  reminder:  { label: 'Recordatorio', icon: '⏰', color: 'text-yellow-400', border: 'border-yellow-800', bg: 'bg-yellow-950/40' },
  note:      { label: 'Nota',          icon: '📝', color: 'text-blue-400',   border: 'border-blue-800',   bg: 'bg-blue-950/40'   },
  dictation: { label: 'Dictado',       icon: '📋', color: 'text-amber-400',  border: 'border-amber-600',  bg: 'bg-amber-950/50'  },
}

export const TYPE_FILTERS = [
  { value: null,        label: 'Todas' },
  { value: 'task',      label: 'Tareas' },
  { value: 'idea',      label: 'Ideas' },
  { value: 'reminder',  label: 'Recordatorios' },
  { value: 'note',      label: 'Notas' },
  { value: 'dictation', label: 'Dictados' },
]

/**
 * Resolve the WebSocket URL for the backend.
 *
 * When apiUrl is set (e.g. "https://example.com") it produces
 * "wss://example.com/ws/client".  When apiUrl is empty the page is served
 * by nginx on the same origin, so we derive the WS URL from location —
 * nginx proxies /ws/ to the backend.
 */
export function resolveWsUrl(apiUrl, { location: loc = globalThis.location } = {}) {
  if (apiUrl) return apiUrl.replace(/^http/, 'ws') + '/ws/client'
  const proto = loc?.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${loc?.host ?? 'localhost'}/ws/client`
}

export function timeAgo(isoString) {
  const diff = Date.now() - new Date(isoString).getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return 'ahora'
  if (minutes < 60) return `hace ${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `hace ${hours}h`
  return `hace ${Math.floor(hours / 24)}d`
}
