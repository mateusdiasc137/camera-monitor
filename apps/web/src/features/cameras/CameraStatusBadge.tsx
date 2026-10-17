import { useState } from 'react'
import type { CameraStatus } from '../../api/client'
import { formatSince, STATE_LABEL, useStatusHistory } from './status'

export function CameraStatusBadge({ status }: { status: CameraStatus | undefined }) {
  if (!status) {
    return <span className="status-pill status-pill--unknown">Aguardando estado…</span>
  }
  return (
    <div className="status-line">
      <span className={`status-pill status-pill--${status.state}`} title={status.detail}>
        {STATE_LABEL[status.state]}
      </span>
      {status.recording && <span className="recording-dot">Gravando</span>}
      <span className="muted">
        {status.detail} · desde {formatSince(status.since)}
      </span>
    </div>
  )
}

export function StatusHistory({ cameraId }: { cameraId: string }) {
  const [open, setOpen] = useState(false)
  const history = useStatusHistory(cameraId, open)
  return (
    <details className="status-history" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Histórico de estado</summary>
      {history.isPending && open && <p className="muted">Carregando…</p>}
      {history.isError && <p className="error">{history.error.message}</p>}
      {history.data?.length === 0 && <p className="muted">Sem mudanças registradas.</p>}
      <ol>
        {history.data?.map((h) => (
          <li key={`${h.since}-${h.state}`}>
            <span className="muted">{formatSince(h.since)}</span> {STATE_LABEL[h.state]} — {h.detail}
          </li>
        ))}
      </ol>
    </details>
  )
}
