const BASE = import.meta.env.VITE_API_URL || ''

function headers(token) {
  const h = {}
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function request(path, options = {}, token) {
  const res = await fetch(`${BASE}${path}`, { ...options, headers: { ...headers(token), ...options.headers } })
  if (res.status === 401) throw Object.assign(new Error('Unauthorized'), { status: 401 })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  if (res.status === 204) return null
  return res.json()
}

export async function fetchNotes(token, { type = null, q = null, page = 1, limit = 50 } = {}) {
  const params = new URLSearchParams({ page, limit })
  if (type) params.set('type', type)
  if (q) params.set('q', q)
  return request(`/notes?${params}`, {}, token)
}

export async function updateNote(token, noteId, { text, annotation } = {}) {
  return request(`/notes/${noteId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: text ?? null, annotation: annotation ?? null }),
  }, token)
}

export async function deleteNote(token, noteId) {
  return request(`/notes/${noteId}`, { method: 'DELETE' }, token)
}

export function audioUrl(noteId) {
  return `${BASE}/notes/${noteId}/audio`
}

export async function fetchHealth(token) {
  return request('/health', {}, token)
}
