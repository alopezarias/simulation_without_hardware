import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import AuthGate from '../components/AuthGate'

describe('AuthGate', () => {
  it('renders token input and submit button', () => {
    render(<AuthGate onToken={() => {}} />)
    expect(screen.getByLabelText(/token/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /acceder/i })).toBeInTheDocument()
  })

  it('calls onToken with the entered value on submit', async () => {
    const onToken = vi.fn()
    render(<AuthGate onToken={onToken} />)
    await userEvent.type(screen.getByLabelText(/token/i), 'my-secret-token')
    await userEvent.click(screen.getByRole('button', { name: /acceder/i }))
    expect(onToken).toHaveBeenCalledWith('my-secret-token')
  })

  it('trims whitespace from token', async () => {
    const onToken = vi.fn()
    render(<AuthGate onToken={onToken} />)
    await userEvent.type(screen.getByLabelText(/token/i), '  abc  ')
    await userEvent.click(screen.getByRole('button', { name: /acceder/i }))
    expect(onToken).toHaveBeenCalledWith('abc')
  })

  it('shows error when submitting empty token', async () => {
    const onToken = vi.fn()
    render(<AuthGate onToken={onToken} />)
    await userEvent.click(screen.getByRole('button', { name: /acceder/i }))
    expect(onToken).not.toHaveBeenCalled()
    expect(screen.getByText(/no puede estar vacío/i)).toBeInTheDocument()
  })

  it('clears error when user starts typing', async () => {
    render(<AuthGate onToken={() => {}} />)
    await userEvent.click(screen.getByRole('button', { name: /acceder/i }))
    expect(screen.getByText(/no puede estar vacío/i)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(/token/i), 'x')
    expect(screen.queryByText(/no puede estar vacío/i)).not.toBeInTheDocument()
  })

  it('hides the input value (password type)', () => {
    render(<AuthGate onToken={() => {}} />)
    expect(screen.getByLabelText(/token/i)).toHaveAttribute('type', 'password')
  })
})
