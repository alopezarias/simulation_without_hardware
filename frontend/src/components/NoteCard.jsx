import { useState } from 'react'
import { NOTE_TYPES, timeAgo } from '../utils'
import { audioUrl } from '../api'

export default function NoteCard({ note, onDelete }) {
  const [copied, setCopied] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const meta = NOTE_TYPES[note.type] ?? NOTE_TYPES.note
  const isDictation = note.type === 'dictation'

  async function copyText() {
    await navigator.clipboard.writeText(note.text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  function handleDelete() {
    if (!confirmDelete) { setConfirmDelete(true); return }
    onDelete(note.id)
  }

  if (isDictation) {
    return (
      <div className={`rounded-xl border ${meta.border} ${meta.bg} p-4`}>
        <div className="flex items-center gap-2 mb-3">
          <span>{meta.icon}</span>
          <span className={`text-xs font-bold uppercase tracking-wider ${meta.color}`}>{meta.label}</span>
          <span className="text-gray-500 text-xs ml-auto">{timeAgo(note.created_at)}</span>
          <button
            onClick={handleDelete}
            aria-label="Eliminar nota"
            className="text-gray-600 hover:text-red-400 text-sm ml-1 transition-colors"
          >
            {confirmDelete ? '¿Confirmar?' : '🗑'}
          </button>
        </div>
        <p className="text-white text-sm leading-relaxed whitespace-pre-wrap">{note.text}</p>
        <button
          onClick={copyText}
          className={`mt-3 w-full py-2 rounded-lg text-sm font-semibold transition-colors
            ${copied
              ? 'bg-green-600 text-white'
              : 'bg-amber-500 hover:bg-amber-400 text-black'}`}
        >
          {copied ? '¡Copiado!' : 'Copiar al portapapeles'}
        </button>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 hover:border-gray-700 transition-colors">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-base">{meta.icon}</span>
        <span className={`text-xs font-semibold uppercase tracking-wide ${meta.color}`}>{meta.label}</span>
        <span className="text-gray-500 text-xs ml-auto">{timeAgo(note.created_at)}</span>
      </div>

      <p className="text-white text-sm leading-snug">
        {note.summary || note.text}
      </p>

      {note.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {note.tags.map(tag => (
            <span key={tag} className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded-full">
              #{tag}
            </span>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-3">
        {note.audio_path && (
          <audio controls src={audioUrl(note.id)} className="h-6 w-32" aria-label="Reproducir audio" />
        )}
        <button
          onClick={handleDelete}
          aria-label="Eliminar nota"
          className={`text-xs px-2 py-1 rounded transition-colors
            ${confirmDelete
              ? 'bg-red-600 text-white'
              : 'text-gray-500 hover:text-red-400'}`}
        >
          {confirmDelete ? 'Confirmar' : '🗑'}
        </button>
      </div>
    </div>
  )
}
