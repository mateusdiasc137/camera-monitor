import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, apiError, type CameraStatus } from '../../api/client'

const STATUS = ['cameras', 'status'] as const
type StatusMap = Record<string, CameraStatus>

export const STATE_LABEL: Record<CameraStatus['state'], string> = {
  online: 'Online',
  degraded: 'Com problemas',
  starting: 'Conectando',
  offline: 'Offline',
  auth_error: 'Senha recusada',
  not_found: 'Caminho não encontrado',
  disabled: 'Desativada',
}

async function fetchStatus(): Promise<StatusMap> {
  const { data, error, response } = await api.GET('/api/v1/cameras/status')
  if (error || !data) throw apiError(response, error, 'Não foi possível obter o estado das câmeras')
  return Object.fromEntries(data.map((s) => [s.camera_id, s]))
}

/**
 * Estado atual de todas as câmeras: carrega o snapshot e mantém-no atualizado pelo fluxo SSE.
 * Ao (re)conectar o fluxo, recarrega o snapshot para não perder transições ocorridas no intervalo.
 */
export function useCameraStatuses() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: STATUS, queryFn: fetchStatus })

  useEffect(() => {
    if (typeof EventSource === 'undefined') return
    const source = new EventSource('/api/v1/events/stream', { withCredentials: true })
    source.addEventListener('open', () => {
      void queryClient.invalidateQueries({ queryKey: STATUS })
    })
    source.addEventListener('camera.status', (event) => {
      const status = JSON.parse((event as MessageEvent<string>).data) as CameraStatus
      queryClient.setQueryData<StatusMap>(STATUS, (current) => ({ ...current, [status.camera_id]: status }))
    })
    return () => source.close()
  }, [queryClient])

  return query
}

export function useStatusHistory(cameraId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['cameras', cameraId, 'status-history'],
    enabled,
    queryFn: async (): Promise<CameraStatus[]> => {
      const { data, error, response } = await api.GET('/api/v1/cameras/{camera_id}/status-history', {
        params: { path: { camera_id: cameraId }, query: { limit: 10 } },
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível obter o histórico')
      return data
    },
  })
}

export function formatSince(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  const sameDay = date.toDateString() === now.toDateString()
  const time = date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  return sameDay ? time : `${date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })} ${time}`
}
