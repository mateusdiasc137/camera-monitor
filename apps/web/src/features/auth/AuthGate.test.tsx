import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../../test/fakeServer'
import { AuthGate } from './AuthGate'

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AuthGate>{() => <p>conteúdo protegido</p>}</AuthGate>
    </QueryClientProvider>,
  )
}

const ADMIN = { id: '1', username: 'admin', role: 'admin' }

describe('AuthGate', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sem sessão, mostra o login e não o conteúdo', async () => {
    fakeServer({ 'GET /api/v1/auth/me': () => Response.json({ error: 'unauthorized' }, { status: 401 }) })
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Entrar' })).toBeInTheDocument()
    expect(screen.queryByText('conteúdo protegido')).not.toBeInTheDocument()
  })

  it('faz login e libera o conteúdo', async () => {
    let loggedIn = false
    const calls = fakeServer({
      'GET /api/v1/auth/me': () =>
        loggedIn ? Response.json(ADMIN) : Response.json({ error: 'unauthorized' }, { status: 401 }),
      'POST /api/v1/auth/login': () => {
        loggedIn = true
        return Response.json(ADMIN)
      },
    })
    renderGate()
    fireEvent.change(await screen.findByLabelText('Usuário'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('Senha'), { target: { value: 'senha-forte-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByText('conteúdo protegido')).toBeInTheDocument()
    expect(screen.getByText('admin')).toBeInTheDocument()

    const loginCall = calls.find((c) => c.method === 'POST')!
    expect(await loginCall.clone().json()).toEqual({ username: 'admin', password: 'senha-forte-123' })
  })

  it('mostra a mensagem do servidor quando o login falha', async () => {
    fakeServer({
      'GET /api/v1/auth/me': () => Response.json({ error: 'unauthorized' }, { status: 401 }),
      'POST /api/v1/auth/login': () =>
        Response.json({ error: 'unauthorized', message: 'usuário ou senha incorretos' }, { status: 401 }),
    })
    renderGate()
    fireEvent.change(await screen.findByLabelText('Usuário'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('Senha'), { target: { value: 'errada' } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('usuário ou senha incorretos')
  })

  it('sair volta para o login', async () => {
    let loggedIn = true
    fakeServer({
      'GET /api/v1/auth/me': () =>
        loggedIn ? Response.json(ADMIN) : Response.json({ error: 'unauthorized' }, { status: 401 }),
      'POST /api/v1/auth/logout': () => {
        loggedIn = false
        return new Response(null, { status: 204 })
      },
    })
    renderGate()
    fireEvent.click(await screen.findByRole('button', { name: 'Sair' }))
    expect(await screen.findByRole('heading', { name: 'Entrar' })).toBeInTheDocument()
  })
})
