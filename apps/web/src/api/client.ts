import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

/**
 * Cliente tipado da API (tipos gerados por `npm run gen:api`).
 * - URL absoluta da própria origem: a UI e a API são servidas pelo mesmo proxy (ADR-003).
 * - `fetch` resolvido a cada chamada (e não capturado na criação), o que permite substituí-lo em testes.
 */
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? '',
  credentials: 'same-origin',
  fetch: (request) => globalThis.fetch(request),
})

export type SystemHealth = components['schemas']['SystemHealthOut']
export type CurrentUser = components['schemas']['UserOut']

/** Erro de API com o status HTTP e a mensagem amigável enviada pelo backend. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function apiError(response: Response, body: unknown, fallback: string): ApiError {
  const message =
    typeof body === 'object' && body !== null && 'message' in body && typeof body.message === 'string'
      ? body.message
      : fallback
  return new ApiError(response.status, message)
}
