import { useCallback, useEffect, useRef, useState } from 'react'

const PING_INTERVAL_MS = 30_000
const MAX_BACKOFF_MS = 30_000

export function useWebSocket(url, { onMessage } = {}) {
  const [status, setStatus] = useState('disconnected')
  const wsRef = useRef(null)
  const retries = useRef(0)
  const reconnectTimer = useRef(null)
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  const connect = useCallback(() => {
    if (!url) return
    setStatus('connecting')

    const ws = new WebSocket(url)

    ws.onopen = () => {
      setStatus('connected')
      retries.current = 0
    }

    ws.onmessage = (e) => {
      try {
        onMessageRef.current?.(JSON.parse(e.data))
      } catch {}
    }

    ws.onclose = () => {
      setStatus('disconnected')
      wsRef.current = null
      const delay = Math.min(1000 * 2 ** retries.current, MAX_BACKOFF_MS)
      retries.current += 1
      reconnectTimer.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {}
    wsRef.current = ws
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  useEffect(() => {
    const id = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  return { status }
}
