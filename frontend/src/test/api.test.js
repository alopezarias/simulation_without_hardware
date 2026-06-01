import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { audioUrl, deleteNote, fetchNotes } from '../api'

const TOKEN = 'test-token'

function mockFetch(body, { status = 200 } = {}) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }))
}

afterEach(() => vi.unstubAllGlobals())

describe('fetchNotes', () => {
  it('calls /notes with page and limit', async () => {
    mockFetch({ items: [], total: 0, page: 1, pages: 1 })
    await fetchNotes(TOKEN)
    const [url] = fetch.mock.calls[0]
    expect(url).toContain('/notes')
    expect(url).toContain('page=1')
    expect(url).toContain('limit=50')
  })

  it('includes Authorization header', async () => {
    mockFetch({ items: [], total: 0, page: 1, pages: 1 })
    await fetchNotes(TOKEN)
    const [, opts] = fetch.mock.calls[0]
    expect(opts.headers['Authorization']).toBe('Bearer test-token')
  })

  it('appends type param when filter is set', async () => {
    mockFetch({ items: [], total: 0, page: 1, pages: 1 })
    await fetchNotes(TOKEN, { type: 'task' })
    const [url] = fetch.mock.calls[0]
    expect(url).toContain('type=task')
  })

  it('does not append type param when null', async () => {
    mockFetch({ items: [], total: 0, page: 1, pages: 1 })
    await fetchNotes(TOKEN, { type: null })
    const [url] = fetch.mock.calls[0]
    expect(url).not.toContain('type=')
  })

  it('throws on non-ok response', async () => {
    mockFetch({}, { status: 500 })
    await expect(fetchNotes(TOKEN)).rejects.toThrow('HTTP 500')
  })

  it('throws Unauthorized on 401', async () => {
    mockFetch({}, { status: 401 })
    await expect(fetchNotes(TOKEN)).rejects.toThrow('Unauthorized')
  })
})

describe('deleteNote', () => {
  it('calls DELETE /notes/:id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    await deleteNote(TOKEN, 'abc-123')
    const [url, opts] = fetch.mock.calls[0]
    expect(url).toContain('/notes/abc-123')
    expect(opts.method).toBe('DELETE')
  })

  it('includes Authorization header', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    await deleteNote(TOKEN, 'abc')
    const [, opts] = fetch.mock.calls[0]
    expect(opts.headers['Authorization']).toBe('Bearer test-token')
  })

  it('throws on error status', async () => {
    mockFetch({}, { status: 404 })
    await expect(deleteNote(TOKEN, 'x')).rejects.toThrow('HTTP 404')
  })
})

describe('audioUrl', () => {
  it('returns correct URL', () => {
    expect(audioUrl('note-123')).toContain('/notes/note-123/audio')
  })
})
