import { formatDuration, useRecordings } from './queries'

/** Resumo do que foi gravado nas últimas 24 h (a linha do tempo completa vem na F1-10). */
export function RecordingSummary({ cameraId }: { cameraId: string }) {
  const recordings = useRecordings(cameraId)
  if (recordings.isPending) return null
  if (recordings.isError) return <p className="muted small">Gravações: indisponível</p>
  const { recorded_s, recording_now } = recordings.data
  return (
    <p className="muted small" aria-label="Resumo das gravações">
      Últimas 24 h: {recorded_s > 0 ? `${formatDuration(recorded_s)} gravados` : 'nada gravado'}
      {recording_now && ' · gravando agora'}
    </p>
  )
}
