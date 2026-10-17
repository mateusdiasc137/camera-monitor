import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeServer } from '../../test/fakeServer'
import { formatDuration } from './queries'
import { RecordingSummary } from './RecordingSummary'

function renderSummary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <RecordingSummary cameraId="c1" />
    </QueryClientProvider>,
  )
}

const body = (recorded_s: number, recording_now: boolean) => ({
  camera_id: 'c1',
  start: '2026-10-16T22:00:00Z',
  end: '2026-10-17T22:00:00Z',
  intervals: [],
  gaps: [],
  segments: 3,
  recorded_s,
  recording_now,
})

describe('RecordingSummary', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('consulta as últimas 24 h e mostra o total gravado', async () => {
    const calls = fakeServer({ 'GET /api/v1/recordings': () => Response.json(body(3 * 3600 + 12 * 60, true)) })
    renderSummary()
    expect(await screen.findByLabelText('Resumo das gravações')).toHaveTextContent(
      'Últimas 24 h: 3 h 12 min gravados · gravando agora',
    )
    const url = new URL(calls[0].url)
    expect(url.searchParams.get('camera_id')).toBe('c1')
    const span = new Date(url.searchParams.get('end')!).getTime() - new Date(url.searchParams.get('start')!).getTime()
    expect(span).toBe(24 * 3600_000)
  })

  it('informa quando nada foi gravado', async () => {
    fakeServer({ 'GET /api/v1/recordings': () => Response.json(body(0, false)) })
    renderSummary()
    expect(await screen.findByLabelText('Resumo das gravações')).toHaveTextContent('Últimas 24 h: nada gravado')
  })

  it('formata durações', () => {
    expect(formatDuration(42)).toBe('42 s')
    expect(formatDuration(5 * 60)).toBe('5 min')
    expect(formatDuration(2 * 3600)).toBe('2 h')
    expect(formatDuration(2 * 3600 + 5 * 60)).toBe('2 h 5 min')
  })
})
