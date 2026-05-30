import { useState } from 'react'

export default function AuthGate({ onToken }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState(false)

  function handleSubmit(e) {
    e.preventDefault()
    const token = value.trim()
    if (!token) { setError(true); return }
    onToken(token)
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <div className="bg-gray-900 border border-gray-700 rounded-xl p-8 w-full max-w-sm">
        <div className="text-center mb-6">
          <span className="text-3xl">🎙</span>
          <h1 className="text-white text-xl font-bold mt-2">Note-Taker</h1>
          <p className="text-gray-400 text-sm mt-1">Introduce el token de acceso</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="password"
            value={value}
            onChange={e => { setValue(e.target.value); setError(false) }}
            placeholder="Bearer token…"
            aria-label="Token de acceso"
            className={`w-full bg-gray-800 text-white rounded-lg px-4 py-3 text-sm
              border ${error ? 'border-red-500' : 'border-gray-600'}
              focus:outline-none focus:border-indigo-500 placeholder-gray-500`}
          />
          {error && <p className="text-red-400 text-xs">El token no puede estar vacío</p>}
          <button
            type="submit"
            className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-semibold
              py-3 rounded-lg text-sm transition-colors"
          >
            Acceder
          </button>
        </form>
        <p className="text-gray-600 text-xs text-center mt-4">
          Si el backend no requiere auth, introduce cualquier valor.
        </p>
      </div>
    </div>
  )
}
