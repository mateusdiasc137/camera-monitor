import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { LocationCheck, Storage, StorageMigration } from '../../api/client'
import { fakeServer, type Route } from '../../test/fakeServer'
import { joinPath } from './format'
import { StoragePage } from './StoragePage'

const GB = 1024 ** 3

const STORAGE: Storage = {
  location_path: '/srv/nvr/hd1',
  policy: { quota_gb: null, replacement: 'overwrite_oldest', high_watermark: 0.95, low_watermark: 0.9, min_free_gb: 2 },
  used_bytes: 12 * GB,
  disk_total_bytes: 500 * GB,
  disk_free_bytes: 300 * GB,
  cameras: [],
  days_covered: null,
  recording_paused: false,
  pause_reason: '',
  last_cleanup: null,
  migration: null,
}

const CHECK: LocationCheck = {
  path: '/mnt/hd2/gravacoes',
  ok: true,
  errors: [],
  warnings: ['fica no mesmo disco do sistema operacional'],
  will_create: true,
  disk_total_bytes: 2000 * GB,
  disk_free_bytes: 1800 * GB,
  current_path: '/srv/nvr/hd1',
  current_bytes: 12 * GB,
  existing_bytes: 0,
  same_volume: false,
  migrate_problem: '',
}

const MIGRATION: StorageMigration = {
  id: 'm1',
  state: 'running',
  from_path: '/srv/nvr/hd1',
  to_path: '/mnt/hd2/gravacoes',
  total_files: 10,
  total_bytes: 10 * GB,
  moved_files: 4,
  moved_bytes: 4 * GB,
  error: '',
  started_at: '2026-10-17T22:00:00Z',
  finished_at: null,
}

function renderPage(routes: Record<string, Route>, storage: Storage = STORAGE) {
  const calls = fakeServer({ 'GET /api/v1/storage': () => Response.json(storage), ...routes })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <StoragePage isAdmin />
    </QueryClientProvider>,
  )
  return calls
}

const BROWSE: Route = (request) => {
  const path = new URL(request.url).searchParams.get('path')
  if (!path) return Response.json({ path: null, parent: null, folders: [{ name: '/mnt', path: '/mnt' }] })
  return Response.json({ path, parent: null, folders: [{ name: 'hd2', path: '/mnt/hd2' }] })
}

describe('local de gravação', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('navega, valida uma subpasta nova e troca movendo as gravações', async () => {
    const calls = renderPage({
      'GET /api/v1/storage/browse': BROWSE,
      'POST /api/v1/storage/location/validate': () => Response.json(CHECK),
      'PUT /api/v1/storage/location': () =>
        Response.json({ location_path: CHECK.path, migration: MIGRATION, unindexed_segments: 0, warnings: [] }),
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Trocar local…' }))
    const picker = await screen.findByRole('dialog', { name: 'Escolher local de gravação' })
    fireEvent.click(await within(picker).findByRole('button', { name: '/mnt' }))
    fireEvent.click(await within(picker).findByRole('button', { name: 'hd2' }))
    await within(picker).findByText('/mnt/hd2')
    fireEvent.change(within(picker).getByLabelText(/criar a subpasta/), { target: { value: 'gravacoes' } })
    fireEvent.click(within(picker).getByRole('button', { name: 'Usar a nova subpasta' }))

    expect(await within(picker).findByText('fica no mesmo disco do sistema operacional')).toBeInTheDocument()
    expect(within(picker).getByText('(será criada)')).toBeInTheDocument()
    expect(within(picker).getByRole('radio', { name: /Mover para o novo local/ })).toBeChecked()
    fireEvent.click(within(picker).getByRole('button', { name: 'Trocar o local' }))
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    const validate = calls.find((c) => c.url.endsWith('/location/validate'))!
    expect(await validate.json()).toEqual({ path: '/mnt/hd2/gravacoes' })
    const put = calls.find((c) => c.method === 'PUT')!
    expect(await put.json()).toEqual({ path: '/mnt/hd2/gravacoes', mode: 'migrate' })
  })

  it('sem espaço para mover, só permite começar vazio', async () => {
    renderPage({
      'GET /api/v1/storage/browse': BROWSE,
      'POST /api/v1/storage/location/validate': () =>
        Response.json({ ...CHECK, migrate_problem: 'as gravações atuais (12,0 GB) não cabem no novo disco' }),
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Trocar local…' }))
    const picker = await screen.findByRole('dialog')
    fireEvent.click(await within(picker).findByRole('button', { name: '/mnt' }))
    fireEvent.click(await within(picker).findByRole('button', { name: 'Usar esta pasta' }))
    expect(await within(picker).findByText(/não cabem no novo disco/)).toBeInTheDocument()
    expect(within(picker).getByRole('radio', { name: /Mover para o novo local/ })).toBeDisabled()
    expect(within(picker).getByRole('radio', { name: /Começar vazio/ })).toBeChecked()
  })

  it('mostra os erros da validação e não deixa trocar', async () => {
    renderPage({
      'GET /api/v1/storage/browse': BROWSE,
      'POST /api/v1/storage/location/validate': () =>
        Response.json({ ...CHECK, ok: false, errors: ['sem permissão de escrita. Para liberar: sudo chown'] }),
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Trocar local…' }))
    const picker = await screen.findByRole('dialog')
    fireEvent.click(await within(picker).findByRole('button', { name: '/mnt' }))
    fireEvent.click(await within(picker).findByRole('button', { name: 'Usar esta pasta' }))
    expect(await within(picker).findByRole('alert')).toHaveTextContent('sudo chown')
    expect(within(picker).queryByRole('button', { name: 'Trocar o local' })).not.toBeInTheDocument()
  })

  it('mostra o progresso da migração e bloqueia nova troca', async () => {
    renderPage({}, { ...STORAGE, migration: MIGRATION })
    expect(await screen.findByRole('progressbar', { name: 'Progresso da migração' })).toHaveAttribute(
      'aria-valuenow',
      '40',
    )
    expect(screen.getByText(/4 de 10 arquivo\(s\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Trocar local…' })).toBeDisabled()
  })

  it('migração interrompida: retomar', async () => {
    const calls = renderPage(
      { 'POST /api/v1/storage/migration/resume': () => Response.json(MIGRATION) },
      { ...STORAGE, migration: { ...MIGRATION, state: 'failed', error: 'x.mp4: Permissão negada' } },
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Permissão negada')
    fireEvent.click(screen.getByRole('button', { name: 'Retomar a migração' }))
    await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/migration/resume'))).toBe(true))
  })
})

describe('joinPath', () => {
  it('usa o separador do servidor', () => {
    expect(joinPath('/mnt/hd2', 'nvr')).toBe('/mnt/hd2/nvr')
    expect(joinPath('/', 'nvr')).toBe('/nvr')
    expect(joinPath('D:\\Cameras', 'nvr')).toBe('D:\\Cameras\\nvr')
    expect(joinPath('D:\\', 'nvr')).toBe('D:\\nvr')
  })
})
