import { useState } from 'react'
import type { Camera } from '../../api/client'
import { useCameras } from '../cameras/queries'
import { STATE_LABEL, useCameraStatuses } from '../cameras/status'
import { LivePlayer, type LensView } from './LivePlayer'
import { supportsH265, type Profile } from './whep'

const LENS_LABEL: Record<LensView, string> = { both: 'Ambas', top: 'Superior', bottom: 'Inferior' }

function CodecWarning() {
  if (supportsH265()) return null
  return (
    <p className="notice" role="alert">
      Este navegador não informa suporte a vídeo H.265 em WebRTC, usado pela câmera. Se a imagem não aparecer, use o
      Google Chrome ou o Microsoft Edge atualizados.
    </p>
  )
}

function FocusView({ camera, onBack }: { camera: Camera; onBack: () => void }) {
  const stacked = camera.lens_layout === 'stacked2'
  const [lens, setLens] = useState<LensView>('both')
  const [profile, setProfile] = useState<Profile>('main')
  const [muted, setMuted] = useState(true)

  return (
    <section className="focus-view" aria-label={`Ao vivo: ${camera.name}`}>
      <div className="section-header">
        <h2>{camera.name}</h2>
        <button type="button" className="secondary" onClick={onBack}>
          Voltar à grade
        </button>
      </div>
      <div className="live-toolbar">
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
        {camera.sub_path && (
          <div className="segmented" role="radiogroup" aria-label="Qualidade">
            <button
              type="button"
              role="radio"
              aria-checked={profile === 'main'}
              className={profile === 'main' ? 'segmented--active' : ''}
              onClick={() => setProfile('main')}
            >
              Alta
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={profile === 'sub'}
              className={profile === 'sub' ? 'segmented--active' : ''}
              onClick={() => setProfile('sub')}
            >
              Leve
            </button>
          </div>
        )}
        <button type="button" className="secondary" onClick={() => setMuted((m) => !m)} aria-pressed={!muted}>
          {muted ? 'Ativar som' : 'Silenciar'}
        </button>
      </div>
      <div className="focus-player">
        <LivePlayer cameraId={camera.id} profile={profile} lensView={lens} muted={muted} label={camera.name} />
      </div>
    </section>
  )
}

export function LivePage() {
  const cameras = useCameras()
  const statuses = useCameraStatuses()
  const [focused, setFocused] = useState<string | null>(null)
  const visible = (cameras.data ?? []).filter((c) => c.enabled)
  const focusedCamera = visible.find((c) => c.id === focused)

  if (focusedCamera) return <FocusView camera={focusedCamera} onBack={() => setFocused(null)} />

  return (
    <section aria-labelledby="live-title">
      <div className="section-header">
        <h2 id="live-title">Ao vivo</h2>
      </div>
      <CodecWarning />
      {cameras.isPending && <p className="muted">Carregando…</p>}
      {cameras.isError && <p className="error">{cameras.error.message}</p>}
      {cameras.data && visible.length === 0 && <p className="muted">Nenhuma câmera ativa.</p>}
      <div className="live-grid">
        {visible.map((camera) => {
          const status = statuses.data?.[camera.id]
          return (
            <article key={camera.id} className="card live-tile" aria-label={camera.name}>
              <header className="live-tile__header">
                <h3>{camera.name}</h3>
                {status && (
                  <span className={`status-pill status-pill--${status.state}`}>{STATE_LABEL[status.state]}</span>
                )}
              </header>
              {/* Na grade usa o substream (leve), quando existir. */}
              <LivePlayer cameraId={camera.id} profile={camera.sub_path ? 'sub' : 'main'} label={camera.name} />
              <button type="button" className="secondary" onClick={() => setFocused(camera.id)}>
                Ampliar
              </button>
            </article>
          )
        })}
      </div>
    </section>
  )
}
