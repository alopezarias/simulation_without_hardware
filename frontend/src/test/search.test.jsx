/**
 * Tests for note search — useDebounce hook and query propagation through useNotes.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDebounce } from '../hooks/useDebounce'
import { useNotes } from '../hooks/useNotes'

const TOKEN = 'tok'

function makeNote(id = '1') {
  return { id, text: `note ${id}`, summary: 'summary', type: 'note', tags: [], entities: {}, created_at: new Date().toISOString(), audio_path: '', capture_mode: 'manual', duration_s: 1 }
}

function stubFetch(data) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => data,
  }))
}

afterEach(() => vi.unstubAllGlobals())


// ── useDebounce ───────────────────────────────────────────────────────────────

describe('useDebounce', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('returns initial value immediately', () => {
    const { result } = renderHook(() => useDebounce('hello', 300))
    expect(result.current).toBe('hello')
  })

  it('does not update before delay elapses', () => {
    const { result, rerender } = renderHook(
      ({ val }) => useDebounce(val, 300),
      { initialProps: { val: 'a' } }
    )
    rerender({ val: 'ab' })
    expect(result.current).toBe('a')
  })

  it('updates after delay elapses', async () => {
    const { result, rerender } = renderHook(
      ({ val }) => useDebounce(val, 300),
      { initialProps: { val: 'a' } }
    )
    rerender({ val: 'ab' })
    act(() => vi.advanceTimersByTime(300))
    expect(result.current).toBe('ab')
  })

  it('cancels previous timer on rapid updates', async () => {
    const { result, rerender } = renderHook(
      ({ val }) => useDebounce(val, 300),
      { initialProps: { val: '' } }
    )
    rerender({ val: 'x' })
    act(() => vi.advanceTimersByTime(100))
    rerender({ val: 'xy' })
    act(() => vi.advanceTimersByTime(100))
    rerender({ val: 'xyz' })
    expect(result.current).toBe('')   // none have settled yet
    act(() => vi.advanceTimersByTime(300))
    expect(result.current).toBe('xyz')  // only final value
  })

  it('returns empty string when value is cleared', () => {
    const { result, rerender } = renderHook(
      ({ val }) => useDebounce(val, 300),
      { initialProps: { val: 'hello' } }
    )
    rerender({ val: '' })
    act(() => vi.advanceTimersByTime(300))
    expect(result.current).toBe('')
  })
})


// ── useNotes + query ──────────────────────────────────────────────────────────

describe('useNotes search query', () => {
  it('passes q param in the fetch URL when query is set', async () => {
    stubFetch({ items: [makeNote()], total: 1, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null, 'meeting'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const url = fetch.mock.calls[0][0]
    expect(url).toContain('q=meeting')
  })

  it('does not include q param when query is empty', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null, ''))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const url = fetch.mock.calls[0][0]
    expect(url).not.toContain('q=')
  })

  it('re-fetches when query changes', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { rerender } = renderHook(
      ({ q }) => useNotes(TOKEN, null, q),
      { initialProps: { q: '' } }
    )
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    rerender({ q: 'meeting' })
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls[1][0]).toContain('q=meeting')
  })

  it('addNote is ignored when query is active', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null, 'meeting'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.addNote(makeNote('ws-new')))

    // Should not be added because we're in search mode
    expect(result.current.notes).toHaveLength(0)
  })

  it('addNote works normally when query is empty', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, null, ''))
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.addNote(makeNote('ws-new')))

    expect(result.current.notes).toHaveLength(1)
  })

  it('passes both q and type params when both are set', async () => {
    stubFetch({ items: [], total: 0, page: 1, pages: 1 })
    const { result } = renderHook(() => useNotes(TOKEN, 'task', 'meeting'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    const url = fetch.mock.calls[0][0]
    expect(url).toContain('q=meeting')
    expect(url).toContain('type=task')
  })
})
