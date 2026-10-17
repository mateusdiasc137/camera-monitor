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
