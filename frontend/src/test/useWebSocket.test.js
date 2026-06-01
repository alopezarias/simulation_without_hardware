import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from '../hooks/useWebSocket'

// ── MockWebSocket ─────────────────────────────────────────────────────────────

class MockWebSocket {
  static instances = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  constructor(url) {
    this.url = url
    this.readyState = MockWebSocket.CONNECTING
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    MockWebSocket.instances.push(this)
  }

  send(data) { this.sent.push(data) }
  close() { this.readyState = MockWebSocket.CLOSED }

  // Test helpers
  _open()         { this.readyState = MockWebSocket.OPEN;   this.onopen?.() }
  _message(data)  { this.onmessage?.({ data: JSON.stringify(data) }) }
  _close()        { this.readyState = MockWebSocket.CLOSED; this.onclose?.() }
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function latest() {
  return MockWebSocket.instances.at(-1)
}

describe('useWebSocket', () => {
  it('creates a WebSocket with the given URL', () => {
    renderHook(() => useWebSocket('ws://localhost:8000/ws/client'))
    expect(latest().url).toBe('ws://localhost:8000/ws/client')
  })

  it('initial status is connecting', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost'))
    expect(result.current.status).toBe('connecting')
  })

  it('status becomes connected on open', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost'))
    act(() => latest()._open())
    expect(result.current.status).toBe('connected')
  })

  it('status becomes disconnected on close', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost'))
    act(() => { latest()._open(); latest()._close() })
    expect(result.current.status).toBe('disconnected')
  })

  it('calls onMessage when message is received', () => {
    const onMessage = vi.fn()
    renderHook(() => useWebSocket('ws://localhost', { onMessage }))
    act(() => { latest()._open(); latest()._message({ event: 'note.created', data: { id: 'x' } }) })
    expect(onMessage).toHaveBeenCalledWith({ event: 'note.created', data: { id: 'x' } })
  })

  it('ignores malformed (non-JSON) messages', () => {
    const onMessage = vi.fn()
    renderHook(() => useWebSocket('ws://localhost', { onMessage }))
    act(() => {
      latest()._open()
      latest().onmessage?.({ data: 'not json' })
    })
    expect(onMessage).not.toHaveBeenCalled()
  })

  it('reconnects after close with backoff', () => {
    renderHook(() => useWebSocket('ws://localhost'))
    const first = latest()
    act(() => { first._open(); first._close() })
    expect(MockWebSocket.instances).toHaveLength(1)

    act(() => vi.advanceTimersByTime(1100))  // first retry: 1s
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('resets retry count on successful reconnect', () => {
    renderHook(() => useWebSocket('ws://localhost'))
    act(() => { latest()._open(); latest()._close() })
    act(() => vi.advanceTimersByTime(1100))
    act(() => latest()._open())  // reconnected successfully
    // Next close should use delay = 1s (reset), not 2s
    act(() => latest()._close())
    act(() => vi.advanceTimersByTime(1100))
    expect(MockWebSocket.instances).toHaveLength(3)
  })

  it('does not create WebSocket when url is null', () => {
    renderHook(() => useWebSocket(null))
    expect(MockWebSocket.instances).toHaveLength(0)
  })

  it('sends ping every 30 seconds when connected', () => {
    renderHook(() => useWebSocket('ws://localhost'))
    act(() => latest()._open())
    act(() => vi.advanceTimersByTime(30_000))
    const pings = latest().sent.filter(m => JSON.parse(m).type === 'ping')
    expect(pings.length).toBeGreaterThanOrEqual(1)
  })

  it('does not send ping when disconnected', () => {
    renderHook(() => useWebSocket('ws://localhost'))
    // Never open — readyState stays CONNECTING
    act(() => vi.advanceTimersByTime(30_000))
    expect(latest().sent).toHaveLength(0)
  })

  it('closes WebSocket on unmount', () => {
    const { unmount } = renderHook(() => useWebSocket('ws://localhost'))
    act(() => latest()._open())
    unmount()
    expect(latest().readyState).toBe(MockWebSocket.CLOSED)
  })
})
