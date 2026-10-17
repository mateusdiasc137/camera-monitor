import { useEffect, useRef, useState } from 'react'
import { WhepPlayer, type Profile, type WhepState } from './whep'

export type LensView = 'both' | 'top' | 'bottom'

type Props = {
  cameraId: string
  profile: Profile
  /** Só faz sentido em câmeras com duas lentes empilhadas no mesmo quadro. */
  lensView?: LensView
  muted?: boolean
  label?: string
}

const STATE_TEXT: Partial<Record<WhepState, string>> = {
  connecting: 'Conectando…',
  retrying: 'Reconectando…',
}

/**
 * Player ao vivo. Para "superior"/"inferior", o quadro tem metade da altura do vídeo e o vídeo é
 * deslocado dentro dele (sem recodificar nada no servidor).
 */
export function LivePlayer({ cameraId, profile, lensView = 'both', muted = true, label }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [state, setState] = useState<WhepState>('connecting')
  const [message, setMessage] = useState<string>()
  const [size, setSize] = useState<{ w: number; h: number } | null>(null)

  useEffect(() => {
    const player = new WhepPlayer({
      cameraId,
      profile,
      onStream: (stream) => {
        if (videoRef.current && videoRef.current.srcObject !== stream) videoRef.current.srcObject = stream
      },
      onState: (next, msg) => {
        setState(next)
        setMessage(msg)
      },
    })
    player.start()
    return () => player.stop()
  }, [cameraId, profile])

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted
  }, [muted])

  const half = lensView !== 'both'
  const aspect = size && size.h > 0 ? `${size.w} / ${half ? size.h / 2 : size.h}` : '16 / 9'
  const overlay = state === 'error' ? message : STATE_TEXT[state] && `${STATE_TEXT[state]}${message ? ` (${message})` : ''}`

  return (
    <div className="live-frame" style={{ aspectRatio: aspect }} aria-label={label} role="group">
      <video
        ref={videoRef}
        className={`live-video live-video--${lensView}`}
        autoPlay
        playsInline
        muted={muted}
        onLoadedMetadata={(e) => setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
        onResize={(e) => setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
      />
      {overlay && (
        <div className={`live-overlay ${state === 'error' ? 'live-overlay--error' : ''}`} role="status">
          {overlay}
        </div>
      )}
    </div>
  )
}
