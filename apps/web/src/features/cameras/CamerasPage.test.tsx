import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../../test/fakeServer'
import { CamerasPage } from './CamerasPage'

const CAMERA = {
  id: 'c1',
  name: 'Quintal',
  driver: 'xiongmai',
  host: '10.0.0.210',
  rtsp_port: 554,
  main_path: '/user={user}&password={password}&channel=1&stream=0.sdp?',
  sub_path: '/user={user}&password={password}&channel=1&stream=1.sdp?',
  ingest: 'relay',
  enabled: true,
  recording_enabled: true,
  created_at: '2026-10-17T12:00:00Z',
  updated_at: '2026-10-17T12:00:00Z',
}

function renderPage(isAdmin: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CamerasPage isAdmin={isAdmin} />
    </QueryClientProvider>,
  )
}

describe('CamerasPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('visitante vê as câmeras, mas não os controles de administração', async () => {
    fakeServer({ 'GET /api/v1/cameras': () => Response.json([CAMERA]) })
    renderPage(false)
    const card = await screen.findByRole('article', { name: 'Quintal' })
    expect(within(card).getByText(/iCSee\/Xiongmai/)).toBeInTheDocument()
    expect(within(card).getByText('Gravação ligada')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Testar conexão' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Adicionar câmera' })).not.toBeInTheDocument()
  })

  it('testar conexão mostra o resultado de cada perfil', async () => {
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([CAMERA]),
      'POST /api/v1/cameras/{id}/test': () =>
        Response.json([
          {
            profile: 'main',
            outcome: 'online',
            detail: 'video H265',
            tracks: [
              { kind: 'video', codec: 'H265', clock_rate: 90000 },
              { kind: 'audio', codec: 'PCMA', clock_rate: 8000 },
            ],
            latency_ms: 42,
          },
          { profile: 'sub', outcome: 'auth_error', detail: 'a câmera recusou usuário/senha', tracks: [], latency_ms: 30 },
        ]),
    })
    renderPage(true)
    fireEvent.click(await screen.findByRole('button', { name: 'Testar conexão' }))
    const results = await screen.findByRole('list', { name: 'Resultado do teste' })
    expect(results).toHaveTextContent('Principal: Online · H265 + PCMA · 42 ms')
    expect(results).toHaveTextContent('Substream: Usuário ou senha recusados')
  })

  it('cadastro com o atalho iCSee envia os marcadores, não a senha no caminho', async () => {
    const calls = fakeServer({
      'GET /api/v1/cameras': () => Response.json([]),
      'POST /api/v1/cameras': () => Response.json(CAMERA, { status: 201 }),
    })
    renderPage(true)
    fireEvent.click(await screen.findByRole('button', { name: 'Adicionar câmera' }))
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Quintal' } })
    fireEvent.change(screen.getByLabelText('IP ou host'), { target: { value: '10.0.0.210' } })
    fireEvent.click(screen.getByRole('button', { name: 'Usar caminhos iCSee' }))
    fireEvent.change(screen.getByLabelText('Usuário da câmera'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByLabelText('Senha da câmera'), { target: { value: 'segredo-123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))

    expect(await screen.findByRole('heading', { name: 'Câmeras' })).toBeInTheDocument()
    const post = calls.find((c) => c.method === 'POST')!
    const body = await post.json()
    expect(body).toMatchObject({
      name: 'Quintal',
      host: '10.0.0.210',
      driver: 'xiongmai',
      main_path: '/user={user}&password={password}&channel=1&stream=0.sdp?',
      username: 'admin',
      password: 'segredo-123',
    })
    expect(body.main_path).not.toContain('segredo-123')
  })

  it('mostra os erros de validação em cada campo', async () => {
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([]),
      'POST /api/v1/cameras': () =>
        Response.json(
          {
            error: 'validation_failed',
            message: 'inválido',
            fields: { host: 'informe só o IP ou o nome do host', main_path: 'use os marcadores {user} e {password}' },
          },
          { status: 422 },
        ),
    })
    renderPage(true)
    fireEvent.click(await screen.findByRole('button', { name: 'Adicionar câmera' }))
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('IP ou host'), { target: { value: 'rtsp://x' } })
    fireEvent.change(screen.getByLabelText(/Caminho do stream principal/), { target: { value: '/a' } })
    fireEvent.change(screen.getByLabelText('Usuário da câmera'), { target: { value: 'a' } })
    fireEvent.change(screen.getByLabelText('Senha da câmera'), { target: { value: 'b' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText('informe só o IP ou o nome do host')).toBeInTheDocument()
    expect(screen.getByText(/use os marcadores/)).toBeInTheDocument()
  })
})
