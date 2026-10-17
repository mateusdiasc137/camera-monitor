/**
 * Cliente WHEP mínimo (receber vídeo/áudio por WebRTC).
 *
 * A negociação vai para o backend (/api/v1/cameras/{id}/whep), que confere a sessão do painel e a
 * repassa ao MediaMTX; só a mídia (UDP) vem direto do servidor. Sem trickle ICE: os candidatos locais
 * são coletados antes de enviar a oferta (na LAN isso leva poucas centenas de ms).
 */

export type Profile = 'main' | 'sub'
export type WhepState = 'connecting' | 'playing' | 'retrying' | 'error' | 'closed'

export interface WhepOptions {
  cameraId: string
  profile: Profile
  onStream: (stream: MediaStream) => void
  onState: (state: WhepState, message?: string) => void
  /** Testes injetam implementações falsas. */
  createPeerConnection?: () => RTCPeerConnection
  fetchImpl?: typeof fetch
}

const GATHER_TIMEOUT_MS = 1500
const RETRY_MIN_MS = 2000
const RETRY_MAX_MS = 30000

/** O navegador decodifica H.265 em WebRTC? (Chrome/Edge com decodificação por hardware, Safari.) */
export function supportsH265(): boolean {
  if (typeof RTCRtpReceiver === 'undefined' || !RTCRtpReceiver.getCapabilities) return false
  const codecs = RTCRtpReceiver.getCapabilities('video')?.codecs ?? []
  return codecs.some((c) => /h265|hevc/i.test(c.mimeType))
}

class FatalError extends Error {}

/** URL absoluta na própria origem (a API e a UI são servidas pelo mesmo proxy). */
function absolute(path: string): string {
  return new URL(path, globalThis.location?.origin ?? 'http://localhost').toString()
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { message?: string }
    return body.message ?? `erro ${response.status}`
  } catch {
    return `erro ${response.status}`
  }
}

function waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener('icegatheringstatechange', check)
      clearTimeout(timer)
      resolve()
    }
    const check = () => {
      if (pc.iceGatheringState === 'complete') done()
    }
    const timer = setTimeout(done, GATHER_TIMEOUT_MS)
    pc.addEventListener('icegatheringstatechange', check)
  })
}

export class WhepPlayer {
  private pc: RTCPeerConnection | null = null
  private sessionUrl: string | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private retryDelay = RETRY_MIN_MS
  private closed = false
  private readonly opts: WhepOptions
  private readonly fetchImpl: typeof fetch

  constructor(opts: WhepOptions) {
    this.opts = opts
    this.fetchImpl = opts.fetchImpl ?? ((input, init) => globalThis.fetch(input, init))
  }

  start(): void {
    void this.connect()
  }

  private async connect(): Promise<void> {
    if (this.closed) return
    this.opts.onState('connecting')
    const pc = this.opts.createPeerConnection?.() ?? new RTCPeerConnection()
    this.pc = pc
    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })
    pc.addEventListener('track', (event) => {
      if (event.streams[0]) this.opts.onStream(event.streams[0])
    })
    pc.addEventListener('connectionstatechange', () => {
      if (pc !== this.pc || this.closed) return
      if (pc.connectionState === 'connected') {
        this.retryDelay = RETRY_MIN_MS
        this.opts.onState('playing')
      } else if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
        this.scheduleRetry('conexão de vídeo perdida')
      }
    })

    try {
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)
      const sdp = pc.localDescription?.sdp ?? offer.sdp ?? ''
      const path = `/api/v1/cameras/${encodeURIComponent(this.opts.cameraId)}/whep?profile=${this.opts.profile}`
      const url = absolute(path)
      const response = await this.fetchImpl(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: sdp,
        credentials: 'same-origin',
      })
      if (this.closed) return
      if (response.status === 201) {
        const location = response.headers.get('Location')
        this.sessionUrl = location ? absolute(location) : null
        await pc.setRemoteDescription({ type: 'answer', sdp: await response.text() })
        return
      }
      const message = await readError(response)
      // 401/403/404/409/422: não adianta insistir; 503 (câmera não transmitindo) e 5xx: tentar de novo.
      if ([401, 403, 404, 409, 422].includes(response.status)) throw new FatalError(message)
      this.scheduleRetry(message)
    } catch (error) {
      if (this.closed) return
      if (error instanceof FatalError) {
        this.teardown()
        this.opts.onState('error', error.message)
      } else {
        this.scheduleRetry(error instanceof Error ? error.message : 'falha na conexão')
      }
    }
  }

  private scheduleRetry(message: string): void {
    if (this.closed || this.retryTimer) return
    this.teardown()
    this.opts.onState('retrying', message)
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.connect()
    }, this.retryDelay)
    this.retryDelay = Math.min(this.retryDelay * 2, RETRY_MAX_MS)
  }

  private teardown(): void {
    if (this.sessionUrl) {
      // Libera a sessão no servidor (melhor esforço; keepalive sobrevive ao fechamento da página).
      void this.fetchImpl(this.sessionUrl, { method: 'DELETE', credentials: 'same-origin', keepalive: true }).catch(
        () => undefined,
      )
      this.sessionUrl = null
    }
    this.pc?.close()
    this.pc = null
  }

  stop(): void {
    this.closed = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.teardown()
    this.opts.onState('closed')
  }
}
