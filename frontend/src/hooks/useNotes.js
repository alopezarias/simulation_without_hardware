import { useCallback, useEffect, useState } from 'react'
import { deleteNote, fetchNotes, updateNote } from '../api'

export function useNotes(token, typeFilter, query = '') {
  const [notes, setNotes] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)

  const LIMIT = 20

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const data = await fetchNotes(token, { type: typeFilter, q: query || null, page: 1, limit: LIMIT })
      setNotes(data.items)
      setTotal(data.total)
      setPage(1)
      setHasMore(data.page < data.pages)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [token, typeFilter, query])

  useEffect(() => { load() }, [load])

  const loadMore = useCallback(async () => {
    if (!token || loadingMore) return
    const nextPage = page + 1
    setLoadingMore(true)
    try {
      const data = await fetchNotes(token, { type: typeFilter, q: query || null, page: nextPage, limit: LIMIT })
      setNotes(prev => [...prev, ...data.items])
      setPage(nextPage)
      setHasMore(nextPage < data.pages)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoadingMore(false)
    }
  }, [token, typeFilter, query, page, loadingMore])

  const addNote = useCallback((note) => {
    if (query) return
    if (typeFilter && note.type !== typeFilter) return
    setNotes(prev => [note, ...prev])
    setTotal(prev => prev + 1)
  }, [typeFilter, query])

  const editNote = useCallback(async (noteId, patch) => {
    try {
      const updated = await updateNote(token, noteId, patch)
      setNotes(prev => prev.map(n => n.id === noteId ? updated : n))
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  const removeNote = useCallback(async (noteId) => {
    try {
      await deleteNote(token, noteId)
      setNotes(prev => prev.filter(n => n.id !== noteId))
      setTotal(prev => Math.max(0, prev - 1))
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  return { notes, total, hasMore, loading, loadingMore, error, addNote, editNote, loadMore, removeNote }
}
