import { useQuery } from '@tanstack/react-query'
import { api, apiError, type Recordings } from '../../api/client'

/** Intervalos gravados e buracos de uma câmera numa janela [start, end). */
export function useRecordingsRange(cameraId: string, start: Date, end: Date, refetch = false) {
  return useQuery({
    queryKey: ['recordings', cameraId, start.toISOString(), end.toISOString()],
    enabled: cameraId !== '',
    refetchInterval: refetch ? 30_000 : false,
    queryFn: async (): Promise<Recordings> => {
      const { data, error, response } = await api.GET('/api/v1/recordings', {
        params: { query: { camera_id: cameraId, start: start.toISOString(), end: end.toISOString() } },
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível consultar as gravações')
      return data
    },
  })
}

/** Últimas `hours` horas (janela deslizante, atualizada a cada minuto). */
export function useRecordings(cameraId: string, hours = 24) {
  return useQuery({
    queryKey: ['recordings', cameraId, 'last', hours],
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

/** URL do vídeo de um trecho (mesma origem; a sessão do painel autoriza). */
export function clipUrl(
  cameraId: string,
  start: Date,
  durationS: number,
  opts: { format?: 'fmp4' | 'mp4'; download?: boolean; lens?: 'both' | 'top' | 'bottom' } = {},
): string {
  const params = new URLSearchParams({
    start: start.toISOString(),
    duration: String(Math.max(1, Math.round(durationS))),
    format: opts.format ?? 'fmp4',
  })
  if (opts.download) params.set('download', 'true')
  if (opts.lens && opts.lens !== 'both') params.set('lens', opts.lens)
  return `/api/v1/recordings/${encodeURIComponent(cameraId)}/video?${params}`
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m} min` : `${h} h`
}
