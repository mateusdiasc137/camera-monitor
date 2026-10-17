import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiError, type Camera, type CameraCreate, type CameraUpdate, type ProbeResult } from '../../api/client'

const CAMERAS = ['cameras'] as const

export function useCameras() {
  return useQuery({
    queryKey: CAMERAS,
    queryFn: async (): Promise<Camera[]> => {
      const { data, error, response } = await api.GET('/api/v1/cameras')
      if (error || !data) throw apiError(response, error, 'Não foi possível listar as câmeras')
      return data
    },
  })
}

export function useCreateCamera() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: CameraCreate): Promise<Camera> => {
      const { data, error, response } = await api.POST('/api/v1/cameras', { body })
      if (error || !data) throw apiError(response, error, 'Não foi possível cadastrar a câmera')
      return data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CAMERAS }),
  })
}

export function useUpdateCamera() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: CameraUpdate }): Promise<Camera> => {
      const { data, error, response } = await api.PATCH('/api/v1/cameras/{camera_id}', {
        params: { path: { camera_id: id } },
        body,
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível salvar a câmera')
      return data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CAMERAS }),
  })
}

export function useDeleteCamera() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error, response } = await api.DELETE('/api/v1/cameras/{camera_id}', {
        params: { path: { camera_id: id } },
      })
      if (!response.ok) throw apiError(response, error, 'Não foi possível remover a câmera')
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CAMERAS }),
  })
}

export function useTestCamera() {
  return useMutation({
    mutationFn: async (id: string): Promise<ProbeResult[]> => {
      const { data, error, response } = await api.POST('/api/v1/cameras/{camera_id}/test', {
        params: { path: { camera_id: id } },
      })
      if (error || !data) throw apiError(response, error, 'Não foi possível testar a câmera')
      return data
    },
  })
}
