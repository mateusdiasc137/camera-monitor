import { afterEach, describe, expect, it, vi } from 'vitest'
import { FakePeerConnection } from '../../test/fakePeerConnection'
import { WhepPlayer, type WhepState } from './whep'

function setup(responses: Response[]) {
  const calls: { url: string; init: RequestInit }[] = []
  const fetchImpl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} })
    return responses.shift() ?? new Response(null, { status: 204 })
  }) as unknown as typeof fetch
  const states: [WhepState, string | undefined][] = []
  const player = new WhepPlayer({
    cameraId: 'c1',
    profile: 'sub',
    onStream: () => undefined,
    onState: (s, m) => states.push([s, m]),
    createPeerConnection: () => new FakePeerConnection() as unknown as RTCPeerConnection,
    fetchImpl,
  })
  return { player, calls, states }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('WhepPlayer', () => {
  afterEach(() => {
    vi.useRealTimers()
    FakePeerConnection.instances = []
  })

  it('negocia pelo backend e encerra a sessão ao parar', async () => {
    const answer = new Response('v=0\r\nresposta\r\n', {
      status: 201,
      headers: { Location: '/api/v1/cameras/c1/whep/abc?profile=sub' },
    })
    const { player, calls, states } = setup([answer])
    player.start()
    await flush()
    await flush()
    expect(calls[0].url).toBe(`${location.origin}/api/v1/cameras/c1/whep?profile=sub`)
    expect(calls[0].init.method).toBe('POST')
    expect((calls[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/sdp')
    const pc = FakePeerConnection.instances[0]
    expect(pc.transceivers).toEqual(['video', 'audio'])
    expect(pc.remoteDescription).toEqual({ type: 'answer', sdp: 'v=0\r\nresposta\r\n' })

    pc.setConnectionState('connected')
    expect(states.at(-1)?.[0]).toBe('playing')

    player.stop()
    expect(calls[1]).toMatchObject({ url: `${location.origin}/api/v1/cameras/c1/whep/abc?profile=sub`, init: { method: 'DELETE' } })
    expect(pc.closed).toBe(true)
  })

  it('tenta de novo quando a câmera ainda não transmite (503)', async () => {
    vi.useFakeTimers()
    const offline = Response.json({ message: 'a câmera não está transmitindo agora' }, { status: 503 })
    const answer = new Response('v=0\r\n', { status: 201, headers: { Location: '/x/whep/s1' } })
    const { player, calls, states } = setup([offline, answer])
    player.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(states.at(-1)).toEqual(['retrying', 'a câmera não está transmitindo agora'])
    await vi.advanceTimersByTimeAsync(2000)
    expect(calls).toHaveLength(2)
    expect(FakePeerConnection.instances[1].remoteDescription?.type).toBe('answer')
    player.stop()
  })

  it('para de tentar em erro definitivo (câmera inexistente)', async () => {
    vi.useFakeTimers()
    const { player, calls, states } = setup([Response.json({ message: 'câmera não encontrada' }, { status: 404 })])
    player.start()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(calls).toHaveLength(1)
    expect(states.at(-1)).toEqual(['error', 'câmera não encontrada'])
    player.stop()
  })

  it('reconecta quando a conexão de vídeo cai', async () => {
    vi.useFakeTimers()
    const ok = () => new Response('v=0\r\n', { status: 201, headers: { Location: '/x/whep/s' } })
    const { player, calls } = setup([ok(), ok()])
    player.start()
    await vi.advanceTimersByTimeAsync(0)
    FakePeerConnection.instances[0].setConnectionState('failed')
    await vi.advanceTimersByTimeAsync(2000)
    expect(calls.filter((c) => c.init.method === 'POST')).toHaveLength(2)
    player.stop()
  })
})
