import { useQuery } from '@tanstack/react-query'
import { api, type SystemHealth } from '../../api/client'
import { formatSince, STATE_LABEL } from '../cameras/status'
import { ago } from './format'

const LABEL: Record<SystemHealth['state'], string> = {
  ok: 'Operando normalmente',
  degraded: 'Operando com problemas',
  down: 'Com falha',
}

const SHORT: Record<SystemHealth['state'], string> = { ok: 'OK', degraded: 'Atenção', down: 'Falha' }

const COMPONENT_LABEL: Record<string, string> = {
  database: 'Banco de dados',
  media_server: 'Servidor de vídeo',
  storage: 'Armazenamento',
  cameras: 'Câmeras',
  recording: 'Gravação',
}

type CameraSummary = NonNullable<SystemHealth['cameras']>[number]

async function fetchHealth(): Promise<SystemHealth> {
  const { data, error } = await api.GET('/api/v1/system/health')
  if (error || !data) throw new Error('Não foi possível consultar o servidor')
  return data
}

function recordingText(c: CameraSummary): string {
  if (!c.recording_expected) return 'Desligada'
  return c.recording ? 'Gravando' : 'Deveria gravar'
}

function CamerasTable({ cameras, checkedAt }: { cameras: CameraSummary[]; checkedAt: string }) {
  if (cameras.length === 0) return <p className="muted">Nenhuma câmera cadastrada.</p>
  return (
    <div className="table-scroll">
    <table className="usage-table health-cameras">
      <caption>Câmeras</caption>
      <thead>
        <tr>
          <th scope="col">Câmera</th>
          <th scope="col">Estado</th>
          <th scope="col">Gravação</th>
          <th scope="col">Último trecho gravado</th>
        </tr>
      </thead>
      <tbody>
        {cameras.map((c) => (
          <tr key={c.camera_id}>
            <td>{c.name}</td>
            <td>
              <span className={`status-pill status-pill--${c.state}`} title={c.detail}>
                {STATE_LABEL[c.state]}
              </span>
              {c.since && <span className="muted small-inline"> desde {formatSince(c.since)}</span>}
            </td>
            <td className={c.recording_expected && !c.recording ? 'error' : undefined}>{recordingText(c)}</td>
            <td>{c.last_recording_at ? ago(c.last_recording_at, checkedAt) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  )
}

export function SystemStatus() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['system', 'health'],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  })

  if (isPending) return <p className="status">Consultando o servidor…</p>
  if (isError) return <p className="status status--down">Servidor indisponível</p>

  return (
    <section className="system" aria-labelledby="system-title">
      <h2 id="system-title">Sistema</h2>
      <div className={`status status--${data.state}`} aria-live="polite">
        <strong>{LABEL[data.state]}</strong>
        <span className="muted">
          {' '}
          · versão {data.version}
          {data.checked_at && ` · verificado às ${new Date(data.checked_at).toLocaleTimeString('pt-BR')}`}
        </span>
      </div>
      {data.components.length > 0 && (
        <ul className="health-list" aria-label="Componentes">
          {data.components.map((c) => (
            <li key={c.name} className={`health-item health-item--${c.state}`}>
              <div className="health-item__head">
                <strong>{COMPONENT_LABEL[c.name] ?? c.name}</strong>
                <span className={`health-badge health-badge--${c.state}`}>{SHORT[c.state]}</span>
              </div>
              {c.detail && <p className="muted">{c.detail}</p>}
            </li>
          ))}
        </ul>
      )}
      {data.checked_at && <CamerasTable cameras={data.cameras ?? []} checkedAt={data.checked_at} />}
    </section>
  )
}
