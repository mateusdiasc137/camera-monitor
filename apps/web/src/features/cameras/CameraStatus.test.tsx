import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../../test/fakeServer'
import { CamerasPage } from './CamerasPage'
import { formatSince } from './status'

const CAMERA = {
  id: 'c1',
  name: 'Quintal',
  driver: 'xiongmai',
  host: '10.0.0.210',
  rtsp_port: 554,
  main_path: '/a',
  sub_path: null,
  ingest: 'relay',
  enabled: true,
  recording_enabled: true,
  created_at: '2026-10-17T12:00:00Z',
  updated_at: '2026-10-17T12:00:00Z',
}

/** EventSource falso: guarda os ouvintes para o teste disparar eventos SSE. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners: Record<string, ((e: MessageEvent<string>) => void)[]> = {}
  closed = false
  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    ;(this.listeners[type] ??= []).push(fn)
  }

  emit(type: string, data: unknown) {
    for (const fn of this.listeners[type] ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }))
  }

  close() {
    this.closed = true
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CamerasPage isAdmin={false} />
    </QueryClientProvider>,
  )
}

describe('estado das câmeras', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    FakeEventSource.instances = []
  })

  it('mostra o estado inicial e atualiza em tempo real pelo SSE', async () => {
    vi.stubGlobal('EventSource', FakeEventSource)
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([CAMERA]),
      'GET /api/v1/cameras/status': () =>
        Response.json([
          { camera_id: 'c1', state: 'offline', detail: 'sem conexão com a câmera', recording: false, since: '2026-10-17T12:00:00Z' },
        ]),
    })
    const view = renderPage()
    const card = await screen.findByRole('article', { name: 'Quintal' })
    expect(await within(card).findByText('Offline')).toBeInTheDocument()
    expect(within(card).queryByText('Gravando')).not.toBeInTheDocument()

    const source = FakeEventSource.instances[0]
    expect(source.url).toBe('/api/v1/events/stream')
    act(() =>
      source.emit('camera.status', {
        camera_id: 'c1',
        state: 'online',
        detail: 'recebendo vídeo',
        recording: true,
        since: '2026-10-17T12:05:00Z',
      }),
    )
    expect(await within(card).findByText('Online')).toBeInTheDocument()
    expect(within(card).getByText('Gravando')).toBeInTheDocument()
    expect(within(card).getByText(/recebendo vídeo/)).toBeInTheDocument()

    view.unmount()
    expect(source.closed).toBe(true) // conexão SSE encerrada ao sair da página
  })

  it('formata "desde" com hora no mesmo dia e data em outro dia', () => {
    const now = new Date('2026-10-17T15:00:00')
    expect(formatSince('2026-10-17T14:05:00', now)).toMatch(/14:05/)
    expect(formatSince('2026-10-16T14:05:00', now)).toMatch(/16\/10 14:05/)
  })
})
