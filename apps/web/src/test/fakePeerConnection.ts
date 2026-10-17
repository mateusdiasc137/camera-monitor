/** RTCPeerConnection falso: suficiente para a lógica de negociação do cliente. */
export class FakePeerConnection extends EventTarget {
  static instances: FakePeerConnection[] = []
  iceGatheringState: RTCIceGatheringState = 'complete'
  connectionState: RTCPeerConnectionState = 'new'
  localDescription: RTCSessionDescriptionInit | null = null
  remoteDescription: RTCSessionDescriptionInit | null = null
  transceivers: string[] = []
  closed = false

  constructor() {
    super()
    FakePeerConnection.instances.push(this)
  }
  addTransceiver(kind: string) {
    this.transceivers.push(kind)
  }
  async createOffer() {
    return { type: 'offer' as const, sdp: 'v=0\r\noferta\r\n' }
  }
  async setLocalDescription(d: RTCSessionDescriptionInit) {
    this.localDescription = d
  }
  async setRemoteDescription(d: RTCSessionDescriptionInit) {
    this.remoteDescription = d
  }
  setConnectionState(state: RTCPeerConnectionState) {
    this.connectionState = state
    this.dispatchEvent(new Event('connectionstatechange'))
  }
  close() {
    this.closed = true
  }
}
