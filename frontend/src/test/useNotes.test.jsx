import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useNotes } from '../hooks/useNotes'

const TOKEN = 'tok'

function makeNote(id = '1', type = 'note') {
  return { id, text: `note ${id}`, summary: '', type, tags: [], entities: {}, created_at: new Date().toISOString(), audio_path: '', capture_mode: 'wake_word', duration_s: 1 }
}

function stubFetch(data) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => data,
  }))
}

afterEach(() => vi.unstubAllGlobals())

describe('useNotes', () => {
  it('fetches notes on mount', async () => {
    stubFetch({ items: [makeNote('a')], total: 1, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.notes).toHaveLength(1)
    expect(result.current.total).toBe(1)
  })

  it('starts with loading=true', async () => {
    // Block fetch resolution
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})))
    const { result } = renderHook(() => useNotes(TOKEN, null))
    expect(result.current.loading).toBe(true)
  })

  it('sets error when fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBeTruthy()
  })

  it('does not fetch when token is empty', () => {
    vi.stubGlobal('fetch', vi.fn())
    renderHook(() => useNotes('', null))
    expect(fetch).not.toHaveBeenCalled()
  })

  it('addNote prepends note to list', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.addNote(makeNote('new')))

    expect(result.current.notes[0].id).toBe('new')
    expect(result.current.total).toBe(1)
  })

  it('addNote ignores note that does not match active type filter', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, 'task'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.addNote(makeNote('x', 'idea')))  // type mismatch

    expect(result.current.notes).toHaveLength(0)
  })

  it('addNote accepts note matching type filter', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, 'idea'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.addNote(makeNote('y', 'idea')))

    expect(result.current.notes).toHaveLength(1)
  })

  it('removeNote calls DELETE and removes from list', async () => {
    stubFetch({ items: [makeNote('del')], total: 1, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))

    // Stub DELETE
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    await act(async () => result.current.removeNote('del'))

    expect(result.current.notes).toHaveLength(0)
    expect(result.current.total).toBe(0)
  })

  it('removeNote decrements total by 1', async () => {
    stubFetch({ items: [makeNote('a'), makeNote('b')], total: 2, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    await act(async () => result.current.removeNote('a'))

    expect(result.current.total).toBe(1)
  })

  it('total never goes below 0 on repeated removes', async () => {
    stubFetch({ items: [makeNote('a')], total: 1, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null))
    await waitFor(() => expect(result.current.loading).toBe(false))

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))
    await act(async () => result.current.removeNote('a'))
    await act(async () => result.current.removeNote('a'))  // already gone

    expect(result.current.total).toBeGreaterThanOrEqual(0)
  })

  it('re-fetches when typeFilter changes', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { rerender } = renderHook(
      ({ filter }) => useNotes(TOKEN, filter),
      { initialProps: { filter: null } }
    )
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))

    rerender({ filter: 'task' })
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))

    const url = fetch.mock.calls[1][0]
    expect(url).toContain('type=task')
  })
})
