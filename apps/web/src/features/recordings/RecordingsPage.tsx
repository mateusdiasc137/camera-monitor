import { useMemo, useRef, useState } from 'react'
import type { Camera, Recordings } from '../../api/client'
import { useCameras } from '../cameras/queries'
import type { LensView } from '../live/LivePlayer'
import { clipUrl, formatDuration, useRecordingsRange } from './queries'
import { Timeline } from './Timeline'
import {
  clipFrom,
  DEFAULT_SELECTION_MS,
  formatClock,
  formatSpan,
  MAX_DOWNLOAD_S,
  normalizeSelection,
  parseClock,
  recordedWithin,
  spanMs,
  type Clip,
  type Range,
} from './trim'

const LENS_LABEL: Record<LensView, string> = { both: 'Ambas', top: 'Superior', bottom: 'Inferior' }
const LENS_DOWNLOAD: Record<LensView, string> = {
  both: '',
  top: ' — só a lente superior',
  bottom: ' — só a lente inferior',
}

function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function RecordingPlayer({ camera, clip, lens, position, onPosition, onEnded }: {
  camera: Camera
  clip: Clip
  lens: LensView
  position: Date | null
  onPosition: (t: Date) => void
  onEnded: (playedS: number) => void
}) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const half = lens !== 'both'
  const ratio = size && size.h > 0 ? size.w / (half ? size.h / 2 : size.h) : 16 / 9
  const src = clipUrl(camera.id, clip.start, clip.durationS)

  return (
    // Altura limitada (as duas lentes empilhadas são altas): a barra de corte fica à vista logo abaixo.
    <div className="live-frame recording-frame" style={{ aspectRatio: String(ratio), width: `min(100%, calc(70vh * ${ratio}))` }}>
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
      {half && <div className="live-overlay">{formatClock(position ?? clip.start)}</div>}
      {error && <div className="live-overlay live-overlay--error">{error}</div>}
    </div>
  )
}

/** Horários do corte (digitáveis), duração e o botão de baixar só o intervalo marcado. */
function CutPanel({ camera, bounds, selection, position, data, lens, onChange }: {
  camera: Camera
  bounds: Range
  selection: Range
  position: Date | null
  data: Recordings | undefined
  lens: LensView
  onChange: (selection: Range) => void
}) {
  const duration = spanMs(selection)

  function setStart(t: Date) {
    // Passou do fim: empurra o fim mantendo a duração (como num editor de vídeo).
    const end = t >= selection.end ? new Date(t.getTime() + duration) : selection.end
    onChange(normalizeSelection({ start: t, end }, bounds))
  }

  function setEnd(t: Date) {
    const start = t <= selection.start ? new Date(t.getTime() - duration) : selection.start
    onChange(normalizeSelection({ start, end: t }, bounds))
  }

  const recorded = recordedWithin(selection, data?.intervals ?? [])
  const seconds = duration / 1000
  let problem: string | null = null
  if (seconds > MAX_DOWNLOAD_S) problem = `O trecho tem ${formatSpan(duration)}: o máximo por download é 2 h.`
  else if (recorded < 1) problem = 'Não há gravação nesse trecho.'
  const hints: string[] = []
  if (!problem && recorded < seconds - 2) {
    hints.push(`O trecho tem buracos: o arquivo junta só os ${formatSpan(recorded * 1000)} gravados.`)
  }
  if (lens !== 'both') hints.push('Recorte da lente sem perda de qualidade; o download começa após alguns segundos de preparo.')
  const label = `Baixar ${formatClock(selection.start)}–${formatClock(selection.end)}${LENS_DOWNLOAD[lens]}`

  return (
    <div className="cut" role="group" aria-label="Trecho para baixar">
      <div className="cut__times">
        <div className="cut__time">
          <label>
            Início
            <input
              type="time"
              step={1}
              value={formatClock(selection.start)}
              onChange={(e) => {
                const t = parseClock(e.target.value, bounds.start)
                if (t) setStart(t)
              }}
            />
          </label>
          {position && (
            <button type="button" className="secondary" onClick={() => setStart(position)}>
              Marcar no vídeo
            </button>
          )}
        </div>
        <div className="cut__time">
          <label>
            Fim
            <input
              type="time"
              step={1}
              value={formatClock(selection.end)}
              onChange={(e) => {
                const t = parseClock(e.target.value, bounds.start)
                if (t) setEnd(t)
              }}
            />
          </label>
          {position && (
            <button type="button" className="secondary" onClick={() => setEnd(position)}>
              Marcar no vídeo
            </button>
          )}
        </div>
        <p className="cut__duration">
          Duração: <strong>{formatSpan(duration)}</strong>
        </p>
      </div>
      {problem && (
        <p className="error" role="alert">
          {problem}
        </p>
      )}
      <div className="cut__actions">
        {problem ? (
          <button type="button" disabled>
            {label}
          </button>
        ) : (
          <a
            className="button-link button-link--primary"
            href={clipUrl(camera.id, selection.start, seconds, {
              format: 'mp4',
              download: true,
              lens: camera.lens_layout === 'stacked2' ? lens : 'both',
            })}
            download
          >
            {label}
          </a>
        )}
      </div>
      {hints.map((h) => (
        <p key={h} className="muted small">
          {h}
        </p>
      ))}
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
  const [lens, setLens] = useState<LensView>('both')
  const [selection, setSelection] = useState<Range | null>(null)
  const playerRef = useRef<HTMLDivElement>(null)

  const selectedId = cameraId || cameras.data?.[0]?.id || ''
  const camera = cameras.data?.find((c) => c.id === selectedId)
  const stacked = camera?.lens_layout === 'stacked2'
  const bounds = useMemo<Range>(() => {
    const s = new Date(`${day}T00:00:00`)
    return { start: s, end: new Date(s.getTime() + 24 * 3600_000) }
  }, [day])
  const isToday = day === localDay(new Date())
  const recordings = useRecordingsRange(selectedId, bounds.start, bounds.end, isToday)

  function reset() {
    setClip(null)
    setPosition(null)
    setSelection(null)
    setNotice(null)
  }

  function play(moment: Date, kind: 'seek' | 'preview' = 'seek') {
    if (kind === 'preview' && recordedWithin({ start: moment, end: new Date(moment.getTime() + 1000) }, recordings.data?.intervals ?? []) < 1) {
      return // prévia de uma alça num buraco: o vídeo fica onde está
    }
    const next = clipFrom(moment, recordings.data)
    if (!next) {
      setNotice('Não há gravação a partir desse horário neste dia.')
      return
    }
    setNotice(next.start.getTime() > moment.getTime() + 1000 ? 'Sem gravação nesse horário; indo para o próximo trecho.' : null)
    setClip(next)
    setPosition(next.start)
    if (!selection) {
      // Primeiro trecho assistido: um corte de 1 min a partir dele, para ajustar pelas alças.
      setSelection(
        normalizeSelection({ start: next.start, end: new Date(next.start.getTime() + DEFAULT_SELECTION_MS) }, bounds),
      )
    }
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
              setLens('both')
              reset()
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
              reset()
            }}
          />
        </label>
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

      <div ref={playerRef} className="recording-stage">
        {camera && clip ? (
          <RecordingPlayer
            camera={camera}
            clip={clip}
            lens={stacked ? lens : 'both'}
            position={position}
            onPosition={setPosition}
            onEnded={playNext}
          />
        ) : (
          <div className="recording-placeholder">
            {recordings.data && recordings.data.intervals.length > 0
              ? 'Toque na barra abaixo para assistir.'
              : 'Nenhuma gravação para assistir.'}
          </div>
        )}
        <Timeline
          bounds={bounds}
          data={recordings.data}
          position={position}
          selection={selection}
          onSeek={play}
          onSelectionChange={setSelection}
        />
      </div>
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {camera && selection && (
        <CutPanel
          camera={camera}
          bounds={bounds}
          selection={selection}
          position={position}
          data={recordings.data}
          lens={stacked ? lens : 'both'}
          onChange={setSelection}
        />
      )}
    </section>
  )
}
