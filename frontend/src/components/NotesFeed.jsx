import NoteCard from './NoteCard'

export default function NotesFeed({ notes, loading, error, onDelete }) {
  if (loading) {
    return (
      <div className="flex justify-center py-16 text-gray-500 text-sm">
        Cargando notas…
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-red-900/30 border border-red-700 rounded-xl p-4 text-red-300 text-sm">
        Error al cargar notas: {error}
      </div>
    )
  }

  if (notes.length === 0) {
    return (
      <div className="flex flex-col items-center py-20 text-gray-600 gap-3">
        <span className="text-4xl">🎙</span>
        <p className="text-sm">Sin notas todavía. Di la frase de activación para empezar.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {notes.map(note => (
        <NoteCard key={note.id} note={note} onDelete={onDelete} />
      ))}
    </div>
  )
}
