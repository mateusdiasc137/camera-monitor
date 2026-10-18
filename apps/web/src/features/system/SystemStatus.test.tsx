import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SystemHealth } from '../../api/client'
import { ago } from './format'
import { SystemStatus } from './SystemStatus'

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SystemStatus />
    </QueryClientProvider>,
  )
}

function mockFetch(response: Response) {
  vi.stubGlobal('fetch', vi.fn(async () => response))
}

const HEALTH: SystemHealth = {
  version: '0.1.0',
  state: 'degraded',
  checked_at: '2026-10-18T12:00:00Z',
  components: [
    { name: 'database', state: 'ok', detail: '' },
    { name: 'media_server', state: 'ok', detail: 'respondendo; 2 relay(s) de câmera ativos' },
    { name: 'recording', state: 'degraded', detail: 'sem gravar: Garagem (sem arquivo novo há 12 min)' },
  ],
  cameras: [
    {
      camera_id: 'c1',
      name: 'Quintal',
      state: 'online',
      detail: '',
      since: '2026-10-18T08:00:00Z',
      recording_expected: true,
      recording: true,
      last_recording_at: '2026-10-18T11:59:35Z',
    },
    {
      camera_id: 'c2',
      name: 'Garagem',
      state: 'online',
      detail: '',
      since: '2026-10-18T08:00:00Z',
      recording_expected: true,
      recording: false,
      last_recording_at: '2026-10-18T11:48:00Z',
    },
    {
      camera_id: 'c3',
      name: 'Rua',
      state: 'auth_error',
      detail: 'senha recusada',
      since: null,
      recording_expected: false,
      recording: false,
      last_recording_at: null,
    },
  ],
}

describe('SystemStatus', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('mostra o estado agregado, os componentes e as câmeras', async () => {
    mockFetch(Response.json(HEALTH))
    renderWithClient()
    expect(await screen.findByText('Operando com problemas')).toBeInTheDocument()

    const components = screen.getByRole('list', { name: 'Componentes' })
    const recording = within(components).getByText('Gravação').closest('li')!
    expect(recording).toHaveClass('health-item--degraded')
    expect(recording).toHaveTextContent('Atenção')
    expect(recording).toHaveTextContent('sem gravar: Garagem (sem arquivo novo há 12 min)')
    expect(within(components).getByText('Servidor de vídeo')).toBeInTheDocument()

    const table = screen.getByRole('table', { name: 'Câmeras' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('Quintal')
    expect(rows[0]).toHaveTextContent('Gravando')
    expect(rows[0]).toHaveTextContent('há 25 s')
    expect(rows[1]).toHaveTextContent('Deveria gravar')
    expect(rows[1]).toHaveTextContent('há 12 min')
    expect(rows[2]).toHaveTextContent('Senha recusada')
    expect(rows[2]).toHaveTextContent('Desligada')
    expect(rows[2]).toHaveTextContent('—')
  })

  it('informa quando o servidor não responde', async () => {
    mockFetch(new Response('erro', { status: 500 }))
    renderWithClient()
    expect(await screen.findByText('Servidor indisponível')).toBeInTheDocument()
  })

  it('tempo relativo ao instante da verificação', () => {
    const ref = '2026-10-18T12:00:00Z'
    expect(ago('2026-10-18T11:59:59Z', ref)).toBe('há 1 s')
    expect(ago('2026-10-18T11:30:00Z', ref)).toBe('há 30 min')
    expect(ago('2026-10-18T06:00:00Z', ref)).toBe('há 6 h')
    expect(ago('2026-10-15T12:00:00Z', ref)).toBe('há 3 dias')
  })
})
