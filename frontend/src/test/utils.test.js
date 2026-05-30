import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { resolveWsUrl, timeAgo } from '../utils'

// ── resolveWsUrl ──────────────────────────────────────────────────────────────

describe('resolveWsUrl', () => {
  const httpLoc  = { protocol: 'http:',  host: 'localhost:3000' }
  const httpsLoc = { protocol: 'https:', host: 'example.com' }

  // With explicit apiUrl
  it('converts http apiUrl to ws', () => {
    expect(resolveWsUrl('http://localhost:8000', { location: httpLoc }))
      .toBe('ws://localhost:8000/ws/client')
  })

  it('converts https apiUrl to wss', () => {
    expect(resolveWsUrl('https://api.example.com', { location: httpsLoc }))
      .toBe('wss://api.example.com/ws/client')
  })

  it('appends /ws/client to apiUrl', () => {
    const url = resolveWsUrl('http://localhost:8000', { location: httpLoc })
    expect(url.endsWith('/ws/client')).toBe(true)
  })

  // Same-origin fallback (empty apiUrl → nginx serves frontend and proxies /ws/)
  it('derives ws:// from http: page location when apiUrl is empty', () => {
    expect(resolveWsUrl('', { location: httpLoc }))
      .toBe('ws://localhost:3000/ws/client')
  })

  it('derives wss:// from https: page location when apiUrl is empty', () => {
    expect(resolveWsUrl('', { location: httpsLoc }))
      .toBe('wss://example.com/ws/client')
  })

  it('derives ws:// when apiUrl is undefined', () => {
    expect(resolveWsUrl(undefined, { location: httpLoc }))
      .toBe('ws://localhost:3000/ws/client')
  })

  it('preserves host and port from location', () => {
    const loc = { protocol: 'http:', host: '192.168.1.10:4000' }
    expect(resolveWsUrl('', { location: loc }))
      .toBe('ws://192.168.1.10:4000/ws/client')
  })

  it('falls back gracefully when location is missing', () => {
    expect(resolveWsUrl('', { location: null }))
      .toBe('ws://localhost/ws/client')
  })
})

// ── timeAgo ───────────────────────────────────────────────────────────────────

describe('timeAgo', () => {
  let now

  beforeEach(() => {
    now = new Date('2024-06-15T12:00:00Z').getTime()
    vi.spyOn(Date, 'now').mockReturnValue(now)
  })

  afterEach(() => vi.restoreAllMocks())

  it('returns "ahora" for timestamps less than 1 minute ago', () => {
    const ts = new Date(now - 30_000).toISOString()  // 30s ago
    expect(timeAgo(ts)).toBe('ahora')
  })

  it('returns minutes for timestamps less than 1 hour ago', () => {
    const ts = new Date(now - 5 * 60_000).toISOString()  // 5 min ago
    expect(timeAgo(ts)).toBe('hace 5m')
  })

  it('returns hours for timestamps less than 24 hours ago', () => {
    const ts = new Date(now - 3 * 3600_000).toISOString()  // 3 h ago
    expect(timeAgo(ts)).toBe('hace 3h')
  })

  it('returns days for timestamps 24+ hours ago', () => {
    const ts = new Date(now - 2 * 24 * 3600_000).toISOString()  // 2 days ago
    expect(timeAgo(ts)).toBe('hace 2d')
  })

  it('returns "ahora" for exactly now', () => {
    expect(timeAgo(new Date(now).toISOString())).toBe('ahora')
  })

  it('returns 1 minute correctly', () => {
    const ts = new Date(now - 60_000).toISOString()
    expect(timeAgo(ts)).toBe('hace 1m')
  })

  it('returns 1 hour correctly', () => {
    const ts = new Date(now - 3600_000).toISOString()
    expect(timeAgo(ts)).toBe('hace 1h')
  })

  it('returns 1 day correctly', () => {
    const ts = new Date(now - 24 * 3600_000).toISOString()
    expect(timeAgo(ts)).toBe('hace 1d')
  })
})
