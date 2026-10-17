import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

describe('SystemStatus', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('mostra o estado agregado e os componentes', async () => {
    mockFetch(
      Response.json({
        version: '0.1.0',
        state: 'degraded',
        components: [{ name: 'mediamtx', state: 'down', detail: 'sem resposta' }],
      }),
    )
    renderWithClient()
    expect(await screen.findByText('Operando com problemas')).toBeInTheDocument()
    expect(screen.getByText(/mediamtx: Fora do ar — sem resposta/)).toBeInTheDocument()
  })

  it('informa quando o servidor não responde', async () => {
    mockFetch(new Response('erro', { status: 500 }))
    renderWithClient()
    expect(await screen.findByText('Servidor indisponível')).toBeInTheDocument()
  })
})
