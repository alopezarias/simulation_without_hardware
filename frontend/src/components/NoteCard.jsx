import { useState } from 'react'
import { NOTE_TYPES, timeAgo } from '../utils'
import { audioUrl } from '../api'

export default function NoteCard({ note, onDelete, onEdit }) {
  const [copied, setCopied] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(note.text)
  const [editAnnotation, setEditAnnotation] = useState(note.annotation || '')
  const [saving, setSaving] = useState(false)

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

  function startEdit() {
    setEditText(note.text)
    setEditAnnotation(note.annotation || '')
    setEditing(true)
  }

  function cancelEdit() {
    setEditing(false)
  }

  async function saveEdit() {
    const patch = {}
    if (editText !== note.text) patch.text = editText
    if (editAnnotation !== (note.annotation || '')) patch.annotation = editAnnotation
    if (Object.keys(patch).length === 0) { setEditing(false); return }
    setSaving(true)
    try {
      await onEdit(note.id, patch)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div className={`rounded-xl border ${isDictation ? meta.border : 'border-indigo-600'} ${isDictation ? meta.bg : 'bg-gray-900'} p-4`}>
        <p className="text-xs text-indigo-400 font-semibold uppercase tracking-wide mb-3">Editando nota</p>
        <label className="block text-xs text-gray-400 mb-1">Texto</label>
        <textarea
          className="w-full bg-gray-800 text-white text-sm rounded-lg p-2 mb-3 resize-none border border-gray-700 focus:outline-none focus:border-indigo-500"
          rows={4}
          value={editText}
          onChange={e => setEditText(e.target.value)}
        />
        <label className="block text-xs text-gray-400 mb-1">Anotacion personal</label>
        <textarea
          className="w-full bg-gray-800 text-white text-sm rounded-lg p-2 mb-4 resize-none border border-gray-700 focus:outline-none focus:border-indigo-500"
          rows={2}
          placeholder="Escribe algo sobre esta nota..."
          value={editAnnotation}
          onChange={e => setEditAnnotation(e.target.value)}
        />
        <div className="flex gap-2">
          <button
            onClick={saveEdit}
            disabled={saving}
            className="flex-1 py-2 rounded-lg text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50 transition-colors"
          >
            {saving ? 'Guardando...' : 'Guardar'}
          </button>
          <button
            onClick={cancelEdit}
            className="px-4 py-2 rounded-lg text-sm text-gray-400 hover:text-white transition-colors"
          >
            Cancelar
          </button>
        </div>
      </div>
    )
  }

  if (isDictation) {
    return (
      <div className={`rounded-xl border ${meta.border} ${meta.bg} p-4`}>
        <div className="flex items-center gap-2 mb-3">
          <span>{meta.icon}</span>
          <span className={`text-xs font-bold uppercase tracking-wider ${meta.color}`}>{meta.label}</span>
          <span className="text-gray-500 text-xs ml-auto">{timeAgo(note.created_at)}</span>
          <button
            onClick={startEdit}
            aria-label="Editar nota"
            className="text-gray-600 hover:text-indigo-400 text-sm ml-1 transition-colors"
          >
            ✏️
          </button>
          <button
            onClick={handleDelete}
            aria-label="Eliminar nota"
            className="text-gray-600 hover:text-red-400 text-sm ml-1 transition-colors"
          >
            {confirmDelete ? '¿Confirmar?' : '🗑'}
          </button>
        </div>
        <p className="text-white text-sm leading-relaxed whitespace-pre-wrap">{note.text}</p>
        {note.annotation && (
          <p className="mt-2 text-xs text-indigo-300 italic border-l-2 border-indigo-600 pl-2">
            {note.annotation}
          </p>
        )}
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

      {note.annotation && (
        <p className="mt-2 text-xs text-indigo-300 italic border-l-2 border-indigo-600 pl-2">
          {note.annotation}
        </p>
      )}

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
          onClick={startEdit}
          aria-label="Editar nota"
          className="text-xs px-2 py-1 rounded text-gray-500 hover:text-indigo-400 transition-colors"
        >
          ✏️
        </button>
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
