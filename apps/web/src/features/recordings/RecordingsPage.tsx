import { useMemo, useRef, useState } from 'react'
import type { Camera, Recordings } from '../../api/client'
import { useCameras } from '../cameras/queries'
import type { LensView } from '../live/LivePlayer'
import { clipUrl, formatDuration, useRecordingsRange } from './queries'
import { Timeline } from './Timeline'

const MAX_CLIP_S = 30 * 60
const LENS_LABEL: Record<LensView, string> = { both: 'Ambas', top: 'Superior', bottom: 'Inferior' }

type Clip = { start: Date; durationS: number }

function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Próximo trecho a tocar a partir de um instante: dentro de um intervalo, ou o início do seguinte. */
export function clipFrom(moment: Date, data: Recordings | undefined): Clip | null {
  for (const interval of data?.intervals ?? []) {
    const s = new Date(interval.start)
    const e = new Date(interval.end)
    if (e <= moment) continue
    const start = moment > s ? moment : s
    const remaining = (e.getTime() - start.getTime()) / 1000
    if (remaining < 1) continue
    return { start, durationS: Math.min(MAX_CLIP_S, remaining) }
  }
  return null
}

function RecordingPlayer({ camera, clip, onPosition, onEnded }: {
  camera: Camera
  clip: Clip
  onPosition: (t: Date) => void
  onEnded: (playedS: number) => void
}) {
  const stacked = camera.lens_layout === 'stacked2'
  const [lens, setLens] = useState<LensView>('both')
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const half = lens !== 'both'
  const aspect = size && size.h > 0 ? `${size.w} / ${half ? size.h / 2 : size.h}` : '16 / 9'
  const src = clipUrl(camera.id, clip.start, clip.durationS)

  return (
    <div className="recording-player">
      {stacked && (
        <div className="segmented" role="radiogroup" aria-label="Lente">
          {(Object.keys(LENS_LABEL) as LensView[]).map((v) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={lens === v}
              className={lens === v ? 'segmented--active' : ''}
              onClick={() => setLens(v)}
            >
              {LENS_LABEL[v]}
            </button>
          ))}
        </div>
      )}
      <div className="live-frame" style={{ aspectRatio: aspect }}>
        <video
          key={src}
          className={`live-video live-video--${lens}`}
          src={src}
          autoPlay
          controls={!half}
          playsInline
          aria-label={`Gravação de ${camera.name}`}
          onLoadedMetadata={(e) => {
            setError(null)
            setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })
          }}
          onTimeUpdate={(e) => onPosition(new Date(clip.start.getTime() + e.currentTarget.currentTime * 1000))}
          onEnded={(e) => onEnded(e.currentTarget.currentTime)}
          onError={() => setError('Não foi possível reproduzir este trecho.')}
        />
        {error && <div className="live-overlay live-overlay--error">{error}</div>}
      </div>
    </div>
  )
}

export function RecordingsPage() {
  const cameras = useCameras()
  const [cameraId, setCameraId] = useState('')
  const [day, setDay] = useState(() => localDay(new Date()))
  const [clip, setClip] = useState<Clip | null>(null)
  const [position, setPosition] = useState<Date | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [downloadMin, setDownloadMin] = useState(5)
  const playerRef = useRef<HTMLDivElement>(null)

  const selectedId = cameraId || cameras.data?.[0]?.id || ''
  const camera = cameras.data?.find((c) => c.id === selectedId)
  const { start, end } = useMemo(() => {
    const s = new Date(`${day}T00:00:00`)
    return { start: s, end: new Date(s.getTime() + 24 * 3600_000) }
  }, [day])
  const isToday = day === localDay(new Date())
  const recordings = useRecordingsRange(selectedId, start, end, isToday)

  function play(moment: Date) {
    const next = clipFrom(moment, recordings.data)
    if (!next) {
      setNotice('Não há gravação a partir desse horário neste dia.')
      return
    }
    setNotice(next.start.getTime() > moment.getTime() + 1000 ? 'Sem gravação nesse horário; indo para o próximo trecho.' : null)
    setClip(next)
    setPosition(next.start)
    playerRef.current?.scrollIntoView?.({ block: 'nearest' })
  }

  function playNext(playedS: number) {
    if (!clip) return
    // Continua de onde o vídeo parou de fato: o servidor encerra o trecho num buraco curto (gravador
    // reiniciado) que a linha do tempo une ao intervalo; o backend pula o buraco no pedido seguinte.
    const played = Math.min(playedS, clip.durationS)
    const after = new Date(clip.start.getTime() + played * 1000 + 500)
    const next = clipFrom(after, recordings.data)
    setClip(next)
    if (!next) setNotice('Fim das gravações deste dia.')
  }

  const downloadFrom = position ?? clip?.start ?? null

  return (
    <section aria-labelledby="recordings-title">
      <div className="section-header">
        <h2 id="recordings-title">Gravações</h2>
      </div>
      <div className="recordings-controls">
        <label>
          Câmera
          <select
            value={selectedId}
            onChange={(e) => {
              setCameraId(e.target.value)
              setClip(null)
              setPosition(null)
            }}
          >
            {cameras.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Dia
          <input
            type="date"
            value={day}
            max={localDay(new Date())}
            onChange={(e) => {
              if (!e.target.value) return
              setDay(e.target.value)
              setClip(null)
              setPosition(null)
            }}
          />
        </label>
      </div>

      {cameras.data?.length === 0 && <p className="muted">Nenhuma câmera cadastrada.</p>}
      {recordings.isError && <p className="error">{recordings.error.message}</p>}
      {recordings.data && (
        <p className="muted">
          {recordings.data.recorded_s > 0
            ? `${formatDuration(recordings.data.recorded_s)} gravados neste dia`
            : 'Nada gravado neste dia'}
          {isToday && recordings.data.recording_now && ' · gravando agora'}
        </p>
      )}

      <Timeline start={start} end={end} data={recordings.data} position={position} onSelect={play} />
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      <div ref={playerRef}>
        {camera && clip && (
          <>
            <RecordingPlayer camera={camera} clip={clip} onPosition={setPosition} onEnded={playNext} />
            <p className="muted">
              Reproduzindo: {(position ?? clip.start).toLocaleString('pt-BR')}
            </p>
          </>
        )}
        {!clip && recordings.data && recordings.data.intervals.length > 0 && (
          <p className="muted">Clique na linha do tempo para assistir.</p>
        )}
      </div>

      {camera && downloadFrom && (
        <div className="download-row">
          <label>
            Baixar
            <select value={downloadMin} onChange={(e) => setDownloadMin(Number(e.target.value))}>
              {[1, 5, 15, 30, 60].map((m) => (
                <option key={m} value={m}>
                  {m} min
                </option>
              ))}
            </select>
          </label>
          <a
            className="button-link"
            href={clipUrl(camera.id, downloadFrom, downloadMin * 60, { format: 'mp4', download: true })}
            download
          >
            Baixar a partir de {downloadFrom.toLocaleTimeString('pt-BR')}
          </a>
        </div>
      )}
    </section>
  )
}
