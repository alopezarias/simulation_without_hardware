import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import NoteCard from '../components/NoteCard'

function makeNote(overrides = {}) {
  return {
    id: 'note-1',
    text: 'Call the supplier on Thursday',
    summary: 'Call supplier Thursday',
    type: 'task',
    tags: ['supplier', 'call'],
    entities: { date: 'Thursday' },
    audio_path: 'data/audio/2026-05/note-1.wav',
    duration_s: 4.2,
    capture_mode: 'wake_word',
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

describe('NoteCard — regular note', () => {
  it('renders the note summary', () => {
    render(<NoteCard note={makeNote()} onDelete={() => {}} />)
    expect(screen.getByText('Call supplier Thursday')).toBeInTheDocument()
  })

  it('renders tags with # prefix', () => {
    render(<NoteCard note={makeNote()} onDelete={() => {}} />)
    expect(screen.getByText('#supplier')).toBeInTheDocument()
    expect(screen.getByText('#call')).toBeInTheDocument()
  })

  it('renders type label', () => {
    render(<NoteCard note={makeNote({ type: 'task' })} onDelete={() => {}} />)
    expect(screen.getByText(/tarea/i)).toBeInTheDocument()
  })

  it('renders audio element when audio_path is set', () => {
    render(<NoteCard note={makeNote()} onDelete={() => {}} />)
    expect(screen.getByLabelText(/reproducir audio/i)).toBeInTheDocument()
  })

  it('does not render audio when audio_path is empty', () => {
    render(<NoteCard note={makeNote({ audio_path: '' })} onDelete={() => {}} />)
    expect(screen.queryByLabelText(/reproducir audio/i)).not.toBeInTheDocument()
  })

  it('calls onDelete after two clicks (confirm flow)', async () => {
    const onDelete = vi.fn()
    render(<NoteCard note={makeNote()} onDelete={onDelete} />)
    const btn = screen.getByLabelText(/eliminar/i)
    await userEvent.click(btn)
    expect(onDelete).not.toHaveBeenCalled()  // first click = confirm prompt
    await userEvent.click(screen.getByText(/confirmar/i))
    expect(onDelete).toHaveBeenCalledWith('note-1')
  })

  it('shows text as fallback when summary is empty', () => {
    render(<NoteCard note={makeNote({ summary: '' })} onDelete={() => {}} />)
    expect(screen.getByText('Call the supplier on Thursday')).toBeInTheDocument()
  })

  it('renders idea type with correct label', () => {
    render(<NoteCard note={makeNote({ type: 'idea' })} onDelete={() => {}} />)
    expect(screen.getByText(/idea/i)).toBeInTheDocument()
  })
})

describe('NoteCard — dictation note', () => {
  const dictNote = makeNote({
    type: 'dictation',
    text: 'La arquitectura hexagonal separa dominio de infraestructura.',
    summary: 'Arquitectura hexagonal',
    audio_path: '',
  })

  it('renders full text (not just summary)', () => {
    render(<NoteCard note={dictNote} onDelete={() => {}} />)
    expect(screen.getByText(/La arquitectura hexagonal/)).toBeInTheDocument()
  })

  it('renders copy button', () => {
    render(<NoteCard note={dictNote} onDelete={() => {}} />)
    expect(screen.getByText(/copiar al portapapeles/i)).toBeInTheDocument()
  })

  it('copy button calls clipboard API', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<NoteCard note={dictNote} onDelete={() => {}} />)
    await userEvent.click(screen.getByText(/copiar al portapapeles/i))
    expect(writeText).toHaveBeenCalledWith(dictNote.text)
  })

  it('shows ¡Copiado! feedback after copy', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })
    render(<NoteCard note={dictNote} onDelete={() => {}} />)
    await userEvent.click(screen.getByText(/copiar al portapapeles/i))
    await waitFor(() => expect(screen.getByText(/copiado/i)).toBeInTheDocument())
  })

  it('does not render audio player for dictation', () => {
    render(<NoteCard note={dictNote} onDelete={() => {}} />)
    expect(screen.queryByLabelText(/reproducir audio/i)).not.toBeInTheDocument()
  })
})
