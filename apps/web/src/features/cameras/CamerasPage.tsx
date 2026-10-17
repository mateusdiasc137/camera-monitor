import { useState } from 'react'
import type { Camera, CameraStatus, ProbeResult } from '../../api/client'
import { CameraForm } from './CameraForm'
import { CameraStatusBadge, StatusHistory } from './CameraStatusBadge'
import { useCameras, useDeleteCamera, useTestCamera } from './queries'
import { RecordingSummary } from '../recordings/RecordingSummary'
import { useCameraStatuses } from './status'

const OUTCOME_LABEL: Record<ProbeResult['outcome'], string> = {
  online: 'Online',
  auth_error: 'Usuário ou senha recusados',
  not_found: 'Caminho não encontrado',
  offline: 'Sem conexão',
  protocol_error: 'Resposta inesperada',
}

const PROFILE_LABEL: Record<ProbeResult['profile'], string> = { main: 'Principal', sub: 'Substream' }

function ProbeResults({ results }: { results: ProbeResult[] }) {
  return (
    <ul className="probe-results" aria-label="Resultado do teste">
      {results.map((r) => (
        <li key={r.profile} className={r.outcome === 'online' ? 'ok' : 'bad'}>
          <strong>{PROFILE_LABEL[r.profile]}:</strong> {OUTCOME_LABEL[r.outcome]}
          {r.outcome === 'online' && r.tracks.length > 0 && (
            <span className="muted"> · {r.tracks.map((t) => t.codec).join(' + ')}</span>
          )}
          {r.latency_ms !== null && <span className="muted"> · {r.latency_ms} ms</span>}
          {r.outcome !== 'online' && <span className="muted"> · {r.detail}</span>}
        </li>
      ))}
    </ul>
  )
}

type CardProps = { camera: Camera; status: CameraStatus | undefined; isAdmin: boolean; onEdit: () => void }

function CameraCard({ camera, status, isAdmin, onEdit }: CardProps) {
  const test = useTestCamera()
  const remove = useDeleteCamera()

  function confirmRemove() {
    if (window.confirm(`Remover a câmera "${camera.name}"? As credenciais dela serão apagadas do cofre.`)) {
      remove.mutate(camera.id)
    }
  }

  return (
    <article className="card camera-card" aria-label={camera.name}>
      <header>
        <h3>{camera.name}</h3>
        <span className="muted">
          {camera.host}:{camera.rtsp_port} · {camera.driver === 'xiongmai' ? 'iCSee/Xiongmai' : 'RTSP genérico'}
        </span>
      </header>
      <CameraStatusBadge status={status} />
      <div className="badges">
        <span className={`badge ${camera.recording_enabled ? 'badge--on' : ''}`}>
          {camera.recording_enabled ? 'Gravação ligada' : 'Gravação desligada'}
        </span>
        {camera.sub_path && <span className="badge">Com substream</span>}
        {!camera.enabled && <span className="badge">Desativada</span>}
      </div>
      {test.data && <ProbeResults results={test.data} />}
      {test.isError && <p className="error">{test.error.message}</p>}
      {remove.isError && <p className="error">{remove.error.message}</p>}
      <RecordingSummary cameraId={camera.id} />
      <StatusHistory cameraId={camera.id} />
      {isAdmin && (
        <div className="card-actions">
          <button type="button" onClick={() => test.mutate(camera.id)} disabled={test.isPending}>
            {test.isPending ? 'Testando…' : 'Testar conexão'}
          </button>
          <button type="button" className="secondary" onClick={onEdit}>
            Editar
          </button>
          <button type="button" className="secondary danger" onClick={confirmRemove} disabled={remove.isPending}>
            Remover
          </button>
        </div>
      )}
    </article>
  )
}

export function CamerasPage({ isAdmin }: { isAdmin: boolean }) {
  const cameras = useCameras()
  const statuses = useCameraStatuses()
  const [editing, setEditing] = useState<Camera | 'new' | null>(null)

  if (editing !== null) {
    return <CameraForm camera={editing === 'new' ? undefined : editing} onDone={() => setEditing(null)} />
  }

  return (
    <section aria-labelledby="cameras-title">
      <div className="section-header">
        <h2 id="cameras-title">Câmeras</h2>
        {isAdmin && (
          <button type="button" onClick={() => setEditing('new')}>
            Adicionar câmera
          </button>
        )}
      </div>
      {cameras.isPending && <p className="muted">Carregando…</p>}
      {cameras.isError && <p className="error">{cameras.error.message}</p>}
      {cameras.data?.length === 0 && (
        <p className="muted">
          Nenhuma câmera cadastrada{isAdmin ? '. Use "Adicionar câmera" para começar.' : '.'}
        </p>
      )}
      <div className="camera-grid">
        {cameras.data?.map((c) => (
          <CameraCard
            key={c.id}
            camera={c}
            status={statuses.data?.[c.id]}
            isAdmin={isAdmin}
            onEdit={() => setEditing(c)}
          />
        ))}
      </div>
    </section>
  )
}
