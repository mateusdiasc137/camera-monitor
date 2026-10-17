import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiError, type CurrentUser } from '../../api/client'

export const SESSION_KEY = ['auth', 'me'] as const

/** Usuário logado, ou `null` se não houver sessão (401). Outros erros são propagados. */
async function fetchMe(): Promise<CurrentUser | null> {
  const { data, error, response } = await api.GET('/api/v1/auth/me')
  if (response.status === 401) return null
  if (error || !data) throw apiError(response, error, 'Não foi possível consultar a sessão')
  return data
}

export function useSession() {
  return useQuery({ queryKey: SESSION_KEY, queryFn: fetchMe, retry: false, staleTime: 60_000 })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (credentials: { username: string; password: string }) => {
      const { data, error, response } = await api.POST('/api/v1/auth/login', { body: credentials })
      if (error || !data) throw apiError(response, error, 'Não foi possível entrar')
      return data
    },
    onSuccess: (user) => queryClient.setQueryData(SESSION_KEY, user),
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/v1/auth/logout')
    },
    onSettled: () => {
      queryClient.clear()
      queryClient.setQueryData(SESSION_KEY, null)
    },
  })
}
