import { useCallback, useState } from 'react'
import AuthGate from './components/AuthGate'
import DeviceStatus from './components/DeviceStatus'
import NotesFeed from './components/NotesFeed'
import TypeFilter from './components/TypeFilter'
import { useNotes } from './hooks/useNotes'
import { useWebSocket } from './hooks/useWebSocket'

const TOKEN_KEY = 'note_taker_token'
const API_URL = import.meta.env.VITE_API_URL || ''
const WS_URL = API_URL.replace(/^http/, 'ws') + '/ws/client'

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [typeFilter, setTypeFilter] = useState(null)
  const [deviceMode, setDeviceMode] = useState(null)

  const { notes, total, loading, error, addNote, removeNote } = useNotes(token, typeFilter)

  const handleWsMessage = useCallback((msg) => {
    if (msg.event === 'note.created') addNote(msg.data)
    if (msg.event === 'device.status') setDeviceMode(msg.data?.mode ?? null)
  }, [addNote])

  const { status: wsStatus } = useWebSocket(token ? WS_URL : null, { onMessage: handleWsMessage })

  function saveToken(t) {
    localStorage.setItem(TOKEN_KEY, t)
    setToken(t)
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
  }

  if (!token) return <AuthGate onToken={saveToken} />

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-gray-950/90 backdrop-blur border-b border-gray-800 px-4 py-3">
        <div className="max-w-2xl mx-auto flex items-center gap-3">
          <span className="text-lg">🎙</span>
          <h1 className="text-white font-bold text-sm tracking-tight">note-taker</h1>
          <div className="ml-auto flex items-center gap-3">
            <DeviceStatus wsStatus={wsStatus} deviceMode={deviceMode} />
            <span className="text-gray-600 text-xs hidden sm:block">{total} nota{total !== 1 ? 's' : ''}</span>
            <button
              onClick={logout}
              aria-label="Cerrar sesión"
              className="text-gray-600 hover:text-gray-300 text-xs transition-colors"
            >
              ⚙
            </button>
          </div>
        </div>
      </header>

      {/* Filters */}
      <div className="border-b border-gray-800 px-4 py-2">
        <div className="max-w-2xl mx-auto">
          <TypeFilter active={typeFilter} onChange={setTypeFilter} />
        </div>
      </div>

      {/* Feed */}
      <main className="flex-1 px-4 py-4">
        <div className="max-w-2xl mx-auto">
          <NotesFeed notes={notes} loading={loading} error={error} onDelete={removeNote} />
        </div>
      </main>
    </div>
  )
}
