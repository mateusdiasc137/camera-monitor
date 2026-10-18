import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Recordings } from '../../api/client'
import { fakeServer } from '../../test/fakeServer'
import { clipFrom, RecordingsPage } from './RecordingsPage'

const CAMERA = {
  id: 'c1',
  name: 'Quintal',
  driver: 'xiongmai',
  host: '10.0.0.210',
  rtsp_port: 554,
  main_path: '/a',
  sub_path: '/b',
  ingest: 'relay',
  enabled: true,
  recording_enabled: true,
  lens_layout: 'stacked2',
  created_at: '2026-10-17T12:00:00Z',
  updated_at: '2026-10-17T12:00:00Z',
}

// Dia local de teste: intervalos das 06:00–07:00 e 12:00–12:30 (horário local do ambiente de teste).
const day = new Date()
day.setHours(0, 0, 0, 0)
const at = (h: number, m = 0) => new Date(day.getTime() + (h * 60 + m) * 60_000)

const RECORDINGS: Recordings = {
  camera_id: 'c1',
  start: at(0).toISOString(),
  end: at(24).toISOString(),
  intervals: [
    { start: at(6).toISOString(), end: at(7).toISOString() },
    { start: at(12).toISOString(), end: at(12, 30).toISOString() },
  ],
  gaps: [
    { start: at(0).toISOString(), end: at(6).toISOString(), reason: 'unknown' },
    { start: at(7).toISOString(), end: at(12).toISOString(), reason: 'paused' },
  ],
  segments: 18,
  recorded_s: 5400,
  recording_now: false,
}

describe('clipFrom', () => {
  it('toca a partir do instante, limitado a 30 min e ao fim do trecho', () => {
    expect(clipFrom(at(6, 10), RECORDINGS)).toEqual({ start: at(6, 10), durationS: 30 * 60 })
    expect(clipFrom(at(6, 50), RECORDINGS)).toEqual({ start: at(6, 50), durationS: 10 * 60 })
  })

  it('num buraco, pula para o próximo trecho; depois do último, nada', () => {
    expect(clipFrom(at(9), RECORDINGS)).toEqual({ start: at(12), durationS: 30 * 60 })
    expect(clipFrom(at(13), RECORDINGS)).toBeNull()
  })
})

describe('RecordingsPage', () => {
  beforeEach(() => {
    // jsdom não faz layout: a barra da linha do tempo tem 1000 px de largura.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, width: 1000, height: 40, right: 1000, bottom: 40, x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  function renderPage() {
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([CAMERA]),
      'GET /api/v1/recordings': () => Response.json(RECORDINGS),
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={client}>
        <RecordingsPage />
      </QueryClientProvider>,
    )
  }

  it('mostra a linha do tempo e toca o trecho clicado', async () => {
    renderPage()
    expect(await screen.findByText('1 h 30 min gravados neste dia')).toBeInTheDocument()
    expect(screen.getAllByTestId('timeline-interval')).toHaveLength(2)

    // 06:30 = 27,08% de 24 h
    fireEvent.click(screen.getByRole('slider', { name: 'Linha do tempo das gravações' }), { clientX: 270.84 })
    const video = await screen.findByLabelText('Gravação de Quintal')
    const url = new URL(video.getAttribute('src')!, 'http://x')
    expect(url.pathname).toBe('/api/v1/recordings/c1/video')
    expect(Math.abs(new Date(url.searchParams.get('start')!).getTime() - at(6, 30).getTime())).toBeLessThan(60_000)
    expect(url.searchParams.get('format')).toBe('fmp4')

    // Câmera de duas lentes: escolher a lente inferior recorta o vídeo.
    fireEvent.click(screen.getByRole('radio', { name: 'Inferior' }))
    expect(screen.getByLabelText('Gravação de Quintal').className).toContain('live-video--bottom')

    const download = screen.getByRole('link', { name: /Baixar a partir de/ })
    const durl = new URL(download.getAttribute('href')!, 'http://x')
    expect(durl.searchParams.get('format')).toBe('mp4')
    expect(durl.searchParams.get('download')).toBe('true')
    expect(durl.searchParams.get('duration')).toBe('300')
  })

  it('clicar num buraco avisa e pula para o próximo trecho', async () => {
    renderPage()
    await screen.findByText('1 h 30 min gravados neste dia')
    fireEvent.click(screen.getByRole('slider', { name: 'Linha do tempo das gravações' }), { clientX: 375 }) // 09:00
    expect(await screen.findByRole('status')).toHaveTextContent('indo para o próximo trecho')
    const src = new URL(screen.getByLabelText('Gravação de Quintal').getAttribute('src')!, 'http://x')
    expect(new Date(src.searchParams.get('start')!).getTime()).toBe(at(12).getTime())
  })

  it('se o trecho acaba antes (buraco curto no servidor), continua de onde parou', async () => {
    renderPage()
    await screen.findByText('1 h 30 min gravados neste dia')
    fireEvent.click(screen.getByRole('slider', { name: 'Linha do tempo das gravações' }), { clientX: 504.17 }) // ~12:06
    const video = await screen.findByLabelText('Gravação de Quintal')
    const first = new Date(new URL(video.getAttribute('src')!, 'http://x').searchParams.get('start')!)

    Object.defineProperty(video, 'currentTime', { value: 100, configurable: true })
    fireEvent.ended(video)
    const next = await screen.findByLabelText('Gravação de Quintal')
    const start = new Date(new URL(next.getAttribute('src')!, 'http://x').searchParams.get('start')!)
    expect(start.getTime()).toBe(first.getTime() + 100_500) // e não o fim planejado (12:30)
  })
})
