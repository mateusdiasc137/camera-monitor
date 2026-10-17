import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../../test/fakeServer'
import { LivePage } from './LivePage'
import { FakePeerConnection } from '../../test/fakePeerConnection'

const base = {
  driver: 'xiongmai',
  host: '10.0.0.210',
  rtsp_port: 554,
  main_path: '/a',
  ingest: 'relay',
  enabled: true,
  recording_enabled: true,
  created_at: '2026-10-17T12:00:00Z',
  updated_at: '2026-10-17T12:00:00Z',
}
const DUAL = { ...base, id: 'c1', name: 'Quintal', sub_path: '/b', lens_layout: 'stacked2' }
const SINGLE = { ...base, id: 'c2', name: 'Garagem', sub_path: null, lens_layout: 'single' }
const OFF = { ...base, id: 'c3', name: 'Desligada', sub_path: null, lens_layout: 'single', enabled: false }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LivePage />
    </QueryClientProvider>,
  )
}

describe('LivePage', () => {
  beforeEach(() => vi.stubGlobal('RTCPeerConnection', FakePeerConnection))
  afterEach(() => vi.unstubAllGlobals())

  it('mostra uma grade com as câmeras ativas, usando o substream quando houver', async () => {
    const calls = fakeServer({
      'GET /api/v1/cameras': () => Response.json([DUAL, SINGLE, OFF]),
      'GET /api/v1/cameras/status': () => Response.json([]),
      'POST /api/v1/cameras/{id}/whep': () => Response.json({ message: 'offline' }, { status: 503 }),
    })
    renderPage()
    expect(await screen.findByRole('article', { name: 'Quintal' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Garagem' })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Desligada' })).not.toBeInTheDocument()
    await vi.waitFor(() => {
      const urls = calls.filter((c) => c.method === 'POST').map((c) => new URL(c.url).pathname + new URL(c.url).search)
      expect(urls).toContain('/api/v1/cameras/c1/whep?profile=sub')
      expect(urls).toContain('/api/v1/cameras/c2/whep?profile=main')
    })
  })

  it('no modo ampliado, câmera de duas lentes permite escolher a lente', async () => {
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([DUAL, SINGLE]),
      'GET /api/v1/cameras/status': () => Response.json([]),
      'POST /api/v1/cameras/{id}/whep': () => Response.json({ message: 'offline' }, { status: 503 }),
    })
    renderPage()
    const tile = await screen.findByRole('article', { name: 'Quintal' })
    fireEvent.click(within(tile).getByRole('button', { name: 'Ampliar' }))
    const lens = await screen.findByRole('radiogroup', { name: 'Lente' })
    fireEvent.click(within(lens).getByRole('radio', { name: 'Inferior' }))
    expect(within(lens).getByRole('radio', { name: 'Inferior' })).toHaveAttribute('aria-checked', 'true')
    expect(document.querySelector('video')?.className).toContain('live-video--bottom')

    fireEvent.click(screen.getByRole('button', { name: 'Voltar à grade' }))
    const single = await screen.findByRole('article', { name: 'Garagem' })
    fireEvent.click(within(single).getByRole('button', { name: 'Ampliar' }))
    expect(await screen.findByRole('heading', { name: 'Garagem' })).toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Lente' })).not.toBeInTheDocument()
  })
})
