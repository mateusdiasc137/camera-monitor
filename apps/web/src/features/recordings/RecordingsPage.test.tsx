import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Recordings } from '../../api/client'
import { fakeServer } from '../../test/fakeServer'
import { RecordingsPage } from './RecordingsPage'

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
const at = (h: number, m = 0, s = 0) => new Date(day.getTime() + ((h * 60 + m) * 60 + s) * 1000)
const DAY_MS = 24 * 3600_000
const xOf = (t: Date) => ((t.getTime() - day.getTime()) / DAY_MS) * 1000 // barra de 1000 px com o dia inteiro

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

function tap(element: Element, clientX: number) {
  fireEvent.pointerDown(element, { clientX, pointerId: 1 })
  fireEvent.pointerUp(element, { clientX, pointerId: 1 })
}

function drag(element: Element, fromX: number, toX: number) {
  fireEvent.pointerDown(element, { clientX: fromX, pointerId: 1 })
  fireEvent.pointerMove(element, { clientX: toX, pointerId: 1 })
  fireEvent.pointerUp(element, { clientX: toX, pointerId: 1 })
}

const srcStart = (el: Element) => new Date(new URL(el.getAttribute('src')!, 'http://x').searchParams.get('start')!)

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

  async function renderPage() {
    fakeServer({
      'GET /api/v1/cameras': () => Response.json([CAMERA]),
      'GET /api/v1/recordings': () => Response.json(RECORDINGS),
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <RecordingsPage />
      </QueryClientProvider>,
    )
    await screen.findByText('1 h 30 min gravados neste dia')
    return screen.getByRole('slider', { name: 'Linha do tempo das gravações' })
  }

  const cut = () => screen.getByRole('group', { name: 'Trecho para baixar' })
  const downloadUrl = () => new URL(within(cut()).getByRole('link').getAttribute('href')!, 'http://x')

  it('tocar na barra toca o vídeo e cria um corte de 1 min logo abaixo dele', async () => {
    const bar = await renderPage()
    expect(screen.getAllByTestId('timeline-interval')).toHaveLength(2)
    tap(bar, xOf(at(6, 30)))

    const video = await screen.findByLabelText('Gravação de Quintal')
    expect(Math.abs(srcStart(video).getTime() - at(6, 30).getTime())).toBeLessThan(1000)
    expect(new URL(video.getAttribute('src')!, 'http://x').searchParams.get('format')).toBe('fmp4')

    expect(within(cut()).getByLabelText('Início')).toHaveValue('06:30:00')
    expect(within(cut()).getByLabelText('Fim')).toHaveValue('06:31:00')
    expect(screen.getByRole('slider', { name: 'Início do corte' })).toHaveAttribute('aria-valuetext', '06:30:00')
    const url = downloadUrl()
    expect(new Date(url.searchParams.get('start')!).getTime()).toBe(at(6, 30).getTime())
    expect(url.searchParams.get('duration')).toBe('60')
    expect(url.searchParams.get('format')).toBe('mp4')
    expect(url.searchParams.get('download')).toBe('true')
    expect(url.searchParams.has('lens')).toBe(false)
    expect(screen.queryByText(/Baixar a partir de/)).not.toBeInTheDocument() // sem os botões de 5/10/15 min
  })

  it('arrastar as alças muda o corte e mostra o quadro do ponto de corte', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(6, 30)))
    await screen.findByLabelText('Gravação de Quintal')

    drag(screen.getByRole('slider', { name: 'Fim do corte' }), xOf(at(6, 31)), xOf(at(6, 45)))
    expect(within(cut()).getByLabelText('Fim')).toHaveValue('06:45:00')
    // ao soltar a alça do fim, o vídeo vai para alguns segundos antes dela
    expect(srcStart(screen.getByLabelText('Gravação de Quintal')).getTime()).toBe(at(6, 44, 57).getTime())

    drag(screen.getByRole('slider', { name: 'Início do corte' }), xOf(at(6, 30)), xOf(at(6, 50)))
    expect(within(cut()).getByLabelText('Início')).toHaveValue('06:44:59') // não passa do fim
    expect(downloadUrl().searchParams.get('duration')).toBe('1')
  })

  it('soltar a alça num buraco não mexe no vídeo nem avisa', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(6, 30)))
    const video = await screen.findByLabelText('Gravação de Quintal')
    const before = video.getAttribute('src')
    drag(screen.getByRole('slider', { name: 'Fim do corte' }), xOf(at(6, 31)), xOf(at(8)))
    expect(within(cut()).getByLabelText('Fim')).toHaveValue('08:00:00')
    expect(screen.getByLabelText('Gravação de Quintal').getAttribute('src')).toBe(before)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('as alças também andam pelo teclado (1 s; Shift: 10 s)', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(6, 30)))
    await screen.findByLabelText('Gravação de Quintal')
    const start = screen.getByRole('slider', { name: 'Início do corte' })
    fireEvent.keyDown(start, { key: 'ArrowRight' })
    fireEvent.keyDown(start, { key: 'ArrowRight', shiftKey: true })
    expect(within(cut()).getByLabelText('Início')).toHaveValue('06:30:11')
  })

  it('horários digitados ajustam o corte; mais de 2 h não pode ser baixado', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(6, 30)))
    await screen.findByLabelText('Gravação de Quintal')

    fireEvent.change(within(cut()).getByLabelText('Início'), { target: { value: '06:10:00' } })
    fireEvent.change(within(cut()).getByLabelText('Fim'), { target: { value: '06:40:30' } })
    expect(within(cut()).getByText('30 min 30 s')).toBeInTheDocument()
    expect(downloadUrl().searchParams.get('duration')).toBe('1830')
    expect(screen.getByRole('slider', { name: 'Fim do corte' })).toHaveAttribute('aria-valuetext', '06:40:30')

    fireEvent.change(within(cut()).getByLabelText('Fim'), { target: { value: '08:30:00' } })
    expect(within(cut()).getByRole('alert')).toHaveTextContent('o máximo por download é 2 h')
    expect(within(cut()).queryByRole('link')).not.toBeInTheDocument()
    expect(within(cut()).getByRole('button', { name: /Baixar/ })).toBeDisabled()

    // Início depois do fim: o fim é empurrado mantendo a duração (como num editor).
    fireEvent.change(within(cut()).getByLabelText('Fim'), { target: { value: '06:40:00' } })
    fireEvent.change(within(cut()).getByLabelText('Início'), { target: { value: '06:50:00' } })
    expect(within(cut()).getByLabelText('Fim')).toHaveValue('07:20:00')
    expect(within(cut()).getByText(/o arquivo junta só os 10 min gravados/)).toBeInTheDocument()
  })

  it('a lente escolhida vale para o vídeo e para o download', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(6, 30)))
    await screen.findByLabelText('Gravação de Quintal')
    fireEvent.click(screen.getByRole('radio', { name: 'Inferior' }))
    expect(screen.getByLabelText('Gravação de Quintal').className).toContain('live-video--bottom')
    expect(downloadUrl().searchParams.get('lens')).toBe('bottom')
    expect(within(cut()).getByRole('link')).toHaveTextContent('só a lente inferior')
    fireEvent.click(screen.getByRole('radio', { name: 'Superior' }))
    expect(downloadUrl().searchParams.get('lens')).toBe('top')
    fireEvent.click(screen.getByRole('radio', { name: 'Ambas' }))
    expect(downloadUrl().searchParams.has('lens')).toBe(false)
  })

  it('zoom e arrasto da barra mudam a janela sem tocar o vídeo', async () => {
    const bar = await renderPage()
    const zoom = screen.getByRole('group', { name: 'Zoom da linha do tempo' })
    expect(within(zoom).getByText(/00:00–00:00 · 24 h/)).toBeInTheDocument()
    fireEvent.click(within(zoom).getByRole('button', { name: 'Aproximar' }))
    expect(within(zoom).getByText(/· 12 h/)).toBeInTheDocument()
    fireEvent.click(within(zoom).getByRole('button', { name: 'Aproximar' }))
    fireEvent.click(within(zoom).getByRole('button', { name: 'Aproximar' }))
    expect(within(zoom).getByText(/10:30–13:30 · 3 h/)).toBeInTheDocument() // aproxima no centro

    drag(bar, 500, 250) // arrastar para a esquerda avança no tempo: 1/4 de 3 h = 45 min
    expect(within(zoom).getByText(/11:15–14:15 · 3 h/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Gravação de Quintal')).not.toBeInTheDocument()

    fireEvent.click(within(zoom).getByRole('button', { name: 'Dia inteiro' }))
    expect(within(zoom).getByText(/· 24 h/)).toBeInTheDocument()
  })

  it('pinça com dois dedos aproxima a barra', async () => {
    const bar = await renderPage()
    fireEvent.pointerDown(bar, { clientX: 400, pointerId: 1 })
    fireEvent.pointerDown(bar, { clientX: 600, pointerId: 2 })
    fireEvent.pointerMove(bar, { clientX: 300, pointerId: 1 })
    fireEvent.pointerMove(bar, { clientX: 700, pointerId: 2 }) // dedos 2x mais afastados
    fireEvent.pointerUp(bar, { clientX: 300, pointerId: 1 })
    fireEvent.pointerUp(bar, { clientX: 700, pointerId: 2 })
    const zoom = screen.getByRole('group', { name: 'Zoom da linha do tempo' })
    expect(within(zoom).getByText(/06:00–18:00 · 12 h/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Gravação de Quintal')).not.toBeInTheDocument() // pinça não é toque
  })

  it('tocar num buraco avisa e pula para o próximo trecho', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(9)))
    expect(await screen.findByRole('status')).toHaveTextContent('indo para o próximo trecho')
    expect(srcStart(screen.getByLabelText('Gravação de Quintal')).getTime()).toBe(at(12).getTime())
  })

  it('se o trecho acaba antes (buraco curto no servidor), continua de onde parou', async () => {
    const bar = await renderPage()
    tap(bar, xOf(at(12, 6)))
    const video = await screen.findByLabelText('Gravação de Quintal')
    const first = srcStart(video)

    Object.defineProperty(video, 'currentTime', { value: 100, configurable: true })
    fireEvent.ended(video)
    const next = await screen.findByLabelText('Gravação de Quintal')
    expect(srcStart(next).getTime()).toBe(first.getTime() + 100_500) // e não o fim planejado (12:30)
  })
})
