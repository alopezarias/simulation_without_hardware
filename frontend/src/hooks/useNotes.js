import { useCallback, useEffect, useState } from 'react'
import { deleteNote, fetchNotes } from '../api'

export function useNotes(token, typeFilter, query = '') {
  const [notes, setNotes] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const data = await fetchNotes(token, { type: typeFilter, q: query || null })
      setNotes(data.items)
      setTotal(data.total)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [token, typeFilter, query])

  useEffect(() => { load() }, [load])

  const addNote = useCallback((note) => {
    // In search mode new notes aren't guaranteed to match the query
    if (query) return
    if (typeFilter && note.type !== typeFilter) return
    setNotes(prev => [note, ...prev])
    setTotal(prev => prev + 1)
  }, [typeFilter, query])

  const removeNote = useCallback(async (noteId) => {
    try {
      await deleteNote(token, noteId)
      setNotes(prev => prev.filter(n => n.id !== noteId))
      setTotal(prev => Math.max(0, prev - 1))
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  return { notes, total, loading, error, addNote, removeNote }
}
