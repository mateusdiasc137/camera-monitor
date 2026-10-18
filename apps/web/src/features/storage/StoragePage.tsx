import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, ApiError, apiError, type Storage, type StoragePolicyIn } from '../../api/client'
import { formatBytes } from './format'
import { LocationCard } from './LocationPanel'

const STORAGE = ['storage'] as const
const GB = 1024 ** 3

function useStorage() {
  return useQuery({
    queryKey: STORAGE,
    // Migração em andamento: o SSE traz o progresso; a consulta periódica é só a rede de segurança.
    refetchInterval: (query) => (query.state.data?.migration?.state === 'running' ? 5_000 : 30_000),
    queryFn: async (): Promise<Storage> => {
      const { data, error, response } = await api.GET('/api/v1/storage')
      if (error || !data) throw apiError(response, error, 'Não foi possível consultar o armazenamento')
      return data
    },
  })
}

function useUpdatePolicy() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: StoragePolicyIn) => {
      const { data, error, response } = await api.PUT('/api/v1/storage/policy', { body })
      if (error || !data) throw apiError(response, error, 'Não foi possível salvar a política')
      return data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STORAGE }),
  })
}

function UsageBar({ storage }: { storage: Storage }) {
  const quota = storage.policy.quota_gb !== null ? storage.policy.quota_gb * GB : null
  const capacity = quota ?? storage.used_bytes + storage.disk_free_bytes
  const pct = capacity > 0 ? Math.min(100, (storage.used_bytes / capacity) * 100) : 0
  const level = quota !== null && storage.used_bytes > quota * storage.policy.high_watermark ? 'high' : 'ok'
  return (
    <div className="usage">
      <div
        className={`usage__bar usage__bar--${level}`}
        role="meter"
        aria-label="Uso do armazenamento"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <p>
        <strong>{formatBytes(storage.used_bytes)}</strong> em gravações
        {quota !== null ? ` de ${formatBytes(quota)} (cota)` : ' — sem cota definida'} · disco com{' '}
        {formatBytes(storage.disk_free_bytes)} livres de {formatBytes(storage.disk_total_bytes)}
      </p>
      {storage.days_covered !== null && (
        <p className="muted">
          No ritmo atual de gravação, {quota !== null ? 'a cota' : 'o espaço disponível'} guarda cerca de{' '}
          <strong>{storage.days_covered.toLocaleString('pt-BR')} dias</strong> de vídeo.
        </p>
      )}
    </div>
  )
}

function PolicyForm({ storage }: { storage: Storage }) {
  const update = useUpdatePolicy()
  const [quota, setQuota] = useState(storage.policy.quota_gb?.toString() ?? '')
  const [replacement, setReplacement] = useState(storage.policy.replacement)
  const [pendingConfirm, setPendingConfirm] = useState<string | null>(null)
  const error = update.error instanceof ApiError ? update.error : null

  function send(confirm: boolean) {
    const body: StoragePolicyIn = {
      quota_gb: quota.trim() === '' ? null : Number(quota.replace(',', '.')),
      replacement,
      confirm,
    }
    update.mutate(body, {
      onSuccess: () => setPendingConfirm(null),
      onError: (e) => {
        if (e instanceof ApiError && e.code === 'confirmation_required') setPendingConfirm(e.message)
      },
    })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    send(false)
  }

  return (
    <form className="card form" onSubmit={submit} aria-label="Política de armazenamento">
      <h3>Política de armazenamento</h3>
      <label>
        Cota para as gravações (GB)
        <input
          inputMode="decimal"
          value={quota}
          placeholder="sem cota"
          onChange={(e) => setQuota(e.target.value)}
        />
        <small className="muted">Vazio = sem cota (vale só o mínimo de {storage.policy.min_free_gb} GB livres no disco).</small>
        {error?.fields.quota_gb && (
          <span className="field-error" role="alert">
            {error.fields.quota_gb}
          </span>
        )}
      </label>
      <fieldset className="radio-group">
        <legend>Quando o espaço acabar</legend>
        <label className="checkbox">
          <input
            type="radio"
            name="replacement"
            checked={replacement === 'overwrite_oldest'}
            onChange={() => setReplacement('overwrite_oldest')}
          />
          <span>
            <strong>Sobrescrever as gravações mais antigas</strong>
            <small className="muted"> — a gravação nunca para; ao chegar a 95% da cota, apaga as mais antigas até 90%.</small>
          </span>
        </label>
        <label className="checkbox">
          <input
            type="radio"
            name="replacement"
            checked={replacement === 'stop_when_full'}
            onChange={() => setReplacement('stop_when_full')}
          />
          <span>
            <strong>Parar de gravar</strong>
            <small className="muted"> — nada é apagado; a gravação pausa até liberar espaço ou aumentar a cota.</small>
          </span>
        </label>
      </fieldset>

      {pendingConfirm && (
        <div className="confirm" role="alertdialog" aria-label="Confirmar exclusão">
          <p>{pendingConfirm}.</p>
          <div className="form-actions">
            <button type="button" className="secondary" onClick={() => setPendingConfirm(null)}>
              Cancelar
            </button>
            <button type="button" className="danger-solid" onClick={() => send(true)} disabled={update.isPending}>
              Confirmar e apagar
            </button>
          </div>
        </div>
      )}
      {error && !pendingConfirm && Object.keys(error.fields).length === 0 && error.code !== 'confirmation_required' && (
        <p className="error" role="alert">
          {error.message}
        </p>
      )}
      {update.isSuccess && !pendingConfirm && <p className="muted">Política salva.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          {update.isPending ? 'Salvando…' : 'Salvar'}
        </button>
      </div>
    </form>
  )
}

export function StoragePage({ isAdmin }: { isAdmin: boolean }) {
  const storage = useStorage()
  if (storage.isPending) return <p className="muted">Carregando…</p>
  if (storage.isError) return <p className="error">{storage.error.message}</p>
  const s = storage.data
  return (
    <section aria-labelledby="storage-title" className="storage">
      <div className="section-header">
        <h2 id="storage-title">Armazenamento</h2>
      </div>
      {s.recording_paused && (
        <p className="notice notice--danger" role="alert">
          Gravação pausada: {s.pause_reason}. Aumente a cota, libere espaço ou mude a política para sobrescrever as
          mais antigas.
        </p>
      )}
      <LocationCard storage={s} isAdmin={isAdmin} />
      <div className="card">
        <UsageBar storage={s} />
        {s.last_cleanup && s.last_cleanup.deleted_files > 0 && (
          <p className="muted">
            Última limpeza ({s.last_cleanup.reason}) em {new Date(s.last_cleanup.at).toLocaleString('pt-BR')}:{' '}
            {s.last_cleanup.deleted_files} arquivo(s) antigos apagados, {formatBytes(s.last_cleanup.freed_bytes)}{' '}
            liberados.
          </p>
        )}
      </div>

      <table className="usage-table">
        <caption>Uso por câmera</caption>
        <thead>
          <tr>
            <th scope="col">Câmera</th>
            <th scope="col">Espaço</th>
            <th scope="col">Gravações desde</th>
            <th scope="col">Ritmo</th>
          </tr>
        </thead>
        <tbody>
          {s.cameras.map((c) => (
            <tr key={c.camera_id}>
              <td>{c.camera_name ?? <span className="muted">câmera removida</span>}</td>
              <td>{formatBytes(c.bytes)}</td>
              <td>{c.oldest ? new Date(c.oldest).toLocaleString('pt-BR') : '—'}</td>
              <td>{c.gb_per_day !== null ? `${c.gb_per_day.toLocaleString('pt-BR')} GB/dia` : '—'}</td>
            </tr>
          ))}
          {s.cameras.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                Nenhuma gravação ainda.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {isAdmin ? (
        <PolicyForm key={`${s.policy.quota_gb}-${s.policy.replacement}`} storage={s} />
      ) : (
        <p className="muted">Apenas administradores alteram a política de armazenamento.</p>
      )}
    </section>
  )
}
