import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Storage } from '../../api/client'
import { fakeServer } from '../../test/fakeServer'
import { formatBytes, StoragePage } from './StoragePage'

const GB = 1024 ** 3

const STORAGE: Storage = {
  location_path: 'C:/nvr/recordings',
  policy: { quota_gb: 100, replacement: 'overwrite_oldest', high_watermark: 0.95, low_watermark: 0.9, min_free_gb: 2 },
  used_bytes: 40 * GB,
  disk_total_bytes: 500 * GB,
  disk_free_bytes: 300 * GB,
  cameras: [
    { camera_id: 'c1', camera_name: 'Quintal', bytes: 38 * GB, oldest: '2026-10-14T10:00:00Z', newest: '2026-10-17T20:00:00Z', gb_per_day: 12.4 },
    { camera_id: 'c9', camera_name: null, bytes: 2 * GB, oldest: '2026-10-10T10:00:00Z', newest: '2026-10-10T11:00:00Z', gb_per_day: null },
  ],
  days_covered: 7.3,
  recording_paused: false,
  pause_reason: '',
  last_cleanup: null,
}

function renderPage(isAdmin: boolean, routes: Parameters<typeof fakeServer>[0] = {}) {
  const calls = fakeServer({ 'GET /api/v1/storage': () => Response.json(STORAGE), ...routes })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <StoragePage isAdmin={isAdmin} />
    </QueryClientProvider>,
  )
  return calls
}

describe('StoragePage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('mostra uso, estimativa de dias e uso por câmera', async () => {
    renderPage(false)
    expect(await screen.findByRole('meter', { name: 'Uso do armazenamento' })).toHaveAttribute('aria-valuenow', '40')
    expect(screen.getByText(/de 100 GB \(cota\)/)).toBeInTheDocument()
    expect(screen.getByText(/7,3 dias/)).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Uso por câmera' })
    expect(within(table).getByText('Quintal')).toBeInTheDocument()
    expect(within(table).getByText('câmera removida')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Política de armazenamento' })).not.toBeInTheDocument()
  })

  it('reduzir a cota abaixo do uso pede confirmação antes de apagar', async () => {
    const calls = renderPage(true, {
      'PUT /api/v1/storage/policy': async (request) => {
        const body = await request.json()
        if (!body.confirm) {
          return Response.json(
            {
              error: 'confirmation_required',
              message: 'a nova cota é menor que o já gravado: cerca de 13,0 GB das gravações mais antigas serão apagados',
              details: { would_delete_bytes: 13 * GB },
            },
            { status: 409 },
          )
        }
        return Response.json({ ...STORAGE.policy, quota_gb: 30 })
      },
    })
    const form = await screen.findByRole('form', { name: 'Política de armazenamento' })
    fireEvent.change(within(form).getByLabelText(/Cota para as gravações/), { target: { value: '30' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Salvar' }))

    const dialog = await screen.findByRole('alertdialog', { name: 'Confirmar exclusão' })
    expect(dialog).toHaveTextContent('13,0 GB das gravações mais antigas serão apagados')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar e apagar' }))
    await vi.waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())

    const puts = calls.filter((c) => c.method === 'PUT')
    expect(await puts[0].json()).toEqual({ quota_gb: 30, replacement: 'overwrite_oldest', confirm: false })
    expect(await puts[1].json()).toEqual({ quota_gb: 30, replacement: 'overwrite_oldest', confirm: true })
  })

  it('mostra o erro do campo quando a cota não cabe no disco', async () => {
    renderPage(true, {
      'PUT /api/v1/storage/policy': () =>
        Response.json(
          { error: 'validation_failed', message: 'x', fields: { quota_gb: 'o máximo possível neste disco é 338,0 GB' } },
          { status: 422 },
        ),
    })
    const form = await screen.findByRole('form', { name: 'Política de armazenamento' })
    fireEvent.change(within(form).getByLabelText(/Cota para as gravações/), { target: { value: '900' } })
    fireEvent.click(within(form).getByRole('radio', { name: /Parar de gravar/ }))
    fireEvent.click(within(form).getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText('o máximo possível neste disco é 338,0 GB')).toBeInTheDocument()
  })

  it('avisa quando a gravação está pausada por falta de espaço', async () => {
    fakeServer({
      'GET /api/v1/storage': () =>
        Response.json({ ...STORAGE, recording_paused: true, pause_reason: 'armazenamento cheio (cota)' }),
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <StoragePage isAdmin={false} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Gravação pausada: armazenamento cheio (cota)')
  })

  it('formata bytes', () => {
    expect(formatBytes(2 * GB)).toBe('2,0 GB')
    expect(formatBytes(150 * GB)).toBe('150 GB')
    expect(formatBytes(5 * 1024 ** 2)).toBe('5 MB')
  })
})
