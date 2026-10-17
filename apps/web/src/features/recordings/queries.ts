import { useQuery } from '@tanstack/react-query'
import { api, apiError, type Recordings } from '../../api/client'

/** Intervalos gravados e buracos de uma câmera numa janela (padrão: últimas 24 h). */
export function useRecordings(cameraId: string, hours = 24) {
  return useQuery({
    queryKey: ['recordings', cameraId, hours],
    refetchInterval: 60_000,
    queryFn: async (): Promise<Recordings> => {
      const end = new Date()
      const start = new Date(end.getTime() - hours * 3600_000)
      const { data, error, response } = await api.GET('/api/v1/recordings', {
        params: { query: { camera_id: cameraId, start: start.toISOString(), end: end.toISOString() } },
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível consultar as gravações')
      return data
    },
  })
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m} min` : `${h} h`
}
