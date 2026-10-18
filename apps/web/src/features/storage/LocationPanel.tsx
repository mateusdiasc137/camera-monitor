import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  api,
  apiError,
  type FolderListing,
  type LocationCheck,
  type Storage,
  type StorageMigration,
} from '../../api/client'
import { formatBytes, joinPath } from './format'

const STORAGE = ['storage'] as const

/** Progresso da migração ao vivo pelo SSE (`storage.migration`), enquanto ela estiver em andamento. */
function useMigrationEvents(active: boolean) {
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!active || typeof EventSource === 'undefined') return
    const source = new EventSource('/api/v1/events/stream', { withCredentials: true })
    source.addEventListener('storage.migration', (event) => {
      const migration = JSON.parse((event as MessageEvent<string>).data) as StorageMigration
      queryClient.setQueryData<Storage>(STORAGE, (current) => (current ? { ...current, migration } : current))
      if (migration.state !== 'running') void queryClient.invalidateQueries({ queryKey: STORAGE })
    })
    return () => source.close()
  }, [active, queryClient])
}

function MigrationStatus({ migration, isAdmin }: { migration: StorageMigration; isAdmin: boolean }) {
  const queryClient = useQueryClient()
  const action = useMutation({
    mutationFn: async (kind: 'resume' | 'abandon') => {
      const { data, error, response } = await api.POST(`/api/v1/storage/migration/${kind}`)
      if (error || !data) throw apiError(response, error, 'Não foi possível concluir a ação')
      return data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STORAGE }),
  })
  const [confirmAbandon, setConfirmAbandon] = useState(false)
  const pct = migration.total_bytes > 0 ? Math.min(100, (migration.moved_bytes / migration.total_bytes) * 100) : 100
  const progress = `${migration.moved_files} de ${migration.total_files} arquivo(s), ${formatBytes(migration.moved_bytes)} de ${formatBytes(migration.total_bytes)}`

  if (migration.state === 'running') {
    return (
      <div className="migration" role="status">
        <p>
          Movendo as gravações de <code>{migration.from_path}</code> para <code>{migration.to_path}</code>: {progress}.
        </p>
        <div
          className="usage__bar"
          role="progressbar"
          aria-label="Progresso da migração"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
        >
          <span style={{ width: `${pct}%` }} />
        </div>
        <p className="muted">
          A gravação já acontece no novo local. Trechos que ainda não foram movidos ficam indisponíveis até chegarem.
        </p>
      </div>
    )
  }
  if (migration.state === 'failed') {
    return (
      <div className="migration notice notice--danger" role="alert">
        <p>
          A migração parou ({progress}): {migration.error}
        </p>
        <p className="muted">
          O que falta continua em <code>{migration.from_path}</code>. Corrija o problema e retome, ou abandone: o
          restante fica nessa pasta, fora da linha do tempo, sem ser apagado.
        </p>
        {isAdmin && !confirmAbandon && (
          <div className="form-actions">
            <button type="button" onClick={() => action.mutate('resume')} disabled={action.isPending}>
              Retomar a migração
            </button>
            <button type="button" className="secondary" onClick={() => setConfirmAbandon(true)}>
              Abandonar…
            </button>
          </div>
        )}
        {isAdmin && confirmAbandon && (
          <div className="confirm" role="alertdialog" aria-label="Confirmar abandono">
            <p>As gravações que não foram movidas saem da linha do tempo (os arquivos continuam na pasta antiga).</p>
            <div className="form-actions">
              <button type="button" className="secondary" onClick={() => setConfirmAbandon(false)}>
                Cancelar
              </button>
              <button type="button" className="danger-solid" onClick={() => action.mutate('abandon')}>
                Abandonar a migração
              </button>
            </div>
          </div>
        )}
        {action.error && <p className="error">{action.error.message}</p>}
      </div>
    )
  }
  const finished = migration.finished_at ? new Date(migration.finished_at) : null
  if (migration.state === 'done' && finished) {
    return (
      <p className="muted" role="status">
        Migração concluída em {finished.toLocaleString('pt-BR')}: {migration.moved_files} arquivo(s),{' '}
        {formatBytes(migration.moved_bytes)} movidos.
      </p>
    )
  }
  return null
}

function CheckResult({ check }: { check: LocationCheck }) {
  return (
    <div className="location-check">
      <p>
        <code>{check.path}</code>
        {check.will_create && <span className="muted"> (será criada)</span>}
        {check.disk_free_bytes !== null && check.disk_total_bytes !== null && (
          <span className="muted">
            {' '}
            · disco com {formatBytes(check.disk_free_bytes)} livres de {formatBytes(check.disk_total_bytes)}
          </span>
        )}
      </p>
      {check.errors.length > 0 && (
        <ul className="check-list check-list--error" role="alert">
          {check.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {check.warnings.length > 0 && (
        <ul className="check-list check-list--warning">
          {check.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function LocationPicker({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [browsePath, setBrowsePath] = useState<string | null>(null)
  const [newFolder, setNewFolder] = useState('')
  const [mode, setMode] = useState<'migrate' | 'fresh'>('migrate')

  const listing = useQuery({
    queryKey: ['storage', 'browse', browsePath],
    queryFn: async (): Promise<FolderListing> => {
      const { data, error, response } = await api.GET('/api/v1/storage/browse', {
        params: { query: browsePath ? { path: browsePath } : {} },
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível abrir a pasta')
      return data
    },
  })
  const validate = useMutation({
    mutationFn: async (path: string): Promise<LocationCheck> => {
      const { data, error, response } = await api.POST('/api/v1/storage/location/validate', { body: { path } })
      if (error || !data) throw apiError(response, error, 'Não foi possível verificar a pasta')
      return data
    },
    onSuccess: (check) => setMode(check.migrate_problem || check.current_bytes === 0 ? 'fresh' : 'migrate'),
  })
  const change = useMutation({
    mutationFn: async (body: { path: string; mode: 'migrate' | 'fresh' }) => {
      const { data, error, response } = await api.PUT('/api/v1/storage/location', { body })
      if (error || !data) throw apiError(response, error, 'Não foi possível trocar o local')
      return data
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: STORAGE })
      onClose()
    },
  })

  function open(path: string | null) {
    setBrowsePath(path)
    validate.reset()
    change.reset()
  }

  const check = validate.data
  const folderName = newFolder.trim()
  const invalidName = /[\\/]/.test(folderName) || folderName === '.' || folderName === '..'
  const current = listing.data?.path ?? null

  return (
    <div className="card location-picker" role="dialog" aria-label="Escolher local de gravação">
      <h3>Novo local de gravação</h3>
      <p className="muted">Só aparecem as pastas liberadas na instalação do servidor.</p>
      <div className="picker-path">
        <strong>{current ?? 'Pastas permitidas'}</strong>
        {current !== null && (
          <button type="button" className="secondary" onClick={() => open(listing.data?.parent ?? null)}>
            Voltar
          </button>
        )}
      </div>
      {listing.isPending && <p className="muted">Carregando…</p>}
      {listing.isError && <p className="error">{listing.error.message}</p>}
      {listing.data && (
        <ul className="folder-list" aria-label="Pastas">
          {listing.data.folders.map((f) => (
            <li key={f.path}>
              <button type="button" className="folder" onClick={() => open(f.path)}>
                {f.name}
              </button>
            </li>
          ))}
          {listing.data.folders.length === 0 && <li className="muted">Nenhuma subpasta.</li>}
        </ul>
      )}
      {current !== null && (
        <div className="form-actions picker-actions">
          <button type="button" onClick={() => validate.mutate(current)} disabled={validate.isPending}>
            Usar esta pasta
          </button>
          <label>
            ou criar a subpasta
            <input value={newFolder} onChange={(e) => setNewFolder(e.target.value)} placeholder="ex.: gravacoes" />
          </label>
          <button
            type="button"
            className="secondary"
            disabled={!folderName || invalidName || validate.isPending}
            onClick={() => validate.mutate(joinPath(current, folderName))}
          >
            Usar a nova subpasta
          </button>
        </div>
      )}
      {validate.isError && <p className="error">{validate.error.message}</p>}

      {check && <CheckResult check={check} />}
      {check?.ok && (
        <fieldset className="radio-group">
          <legend>Gravações atuais ({formatBytes(check.current_bytes)})</legend>
          <label className="checkbox">
            <input
              type="radio"
              name="location-mode"
              checked={mode === 'migrate'}
              disabled={Boolean(check.migrate_problem)}
              onChange={() => setMode('migrate')}
            />
            <span>
              <strong>Mover para o novo local</strong>
              <small className="muted">
                {' '}
                — continuam na linha do tempo; a cópia roda em segundo plano
                {check.same_volume ? ' (mesmo disco: é instantânea)' : ''}.
              </small>
              {check.migrate_problem && <small className="field-error"> {check.migrate_problem}</small>}
            </span>
          </label>
          <label className="checkbox">
            <input type="radio" name="location-mode" checked={mode === 'fresh'} onChange={() => setMode('fresh')} />
            <span>
              <strong>Começar vazio</strong>
              <small className="muted">
                {' '}
                — as atuais ficam em <code>{check.current_path}</code>, fora da linha do tempo e da cota (nada é
                apagado).
              </small>
            </span>
          </label>
        </fieldset>
      )}
      {change.isError && (
        <p className="error" role="alert">
          {change.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="button" className="secondary" onClick={onClose}>
          Cancelar
        </button>
        {check?.ok && (
          <button
            type="button"
            onClick={() => change.mutate({ path: check.path, mode })}
            disabled={change.isPending}
          >
            {change.isPending ? 'Trocando…' : 'Trocar o local'}
          </button>
        )}
      </div>
    </div>
  )
}

export function LocationCard({ storage, isAdmin }: { storage: Storage; isAdmin: boolean }) {
  const [picking, setPicking] = useState(false)
  const migration = storage.migration ?? null
  const busy = migration?.state === 'running' || migration?.state === 'failed'
  useMigrationEvents(migration?.state === 'running')

  return (
    <>
      <div className="card location">
        <p>
          Local das gravações: <code>{storage.location_path}</code>
        </p>
        {migration && <MigrationStatus migration={migration} isAdmin={isAdmin} />}
        {isAdmin && !picking && (
          <div className="form-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => setPicking(true)}
              disabled={busy}
              title={busy ? 'Conclua ou resolva a migração antes de trocar de novo' : undefined}
            >
              Trocar local…
            </button>
          </div>
        )}
      </div>
      {picking && <LocationPicker onClose={() => setPicking(false)} />}
    </>
  )
}
