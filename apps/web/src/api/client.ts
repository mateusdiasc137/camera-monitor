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
export type Camera = components['schemas']['CameraOut']
export type CameraCreate = components['schemas']['CameraCreateIn']
export type CameraUpdate = components['schemas']['CameraUpdateIn']
export type ProbeResult = components['schemas']['ProbeResultOut']
export type CameraStatus = components['schemas']['CameraStatusOut']
export type Recordings = components['schemas']['RecordingsOut']
export type Storage = components['schemas']['StorageOut']
export type StoragePolicyIn = components['schemas']['PolicyIn']

/** Erro de API com o status HTTP, a mensagem amigável e, em validações, os erros por campo. */
export class ApiError extends Error {
  readonly status: number
  readonly fields: Record<string, string>
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(
    status: number,
    message: string,
    fields: Record<string, string> = {},
    code = '',
    details: Record<string, unknown> = {},
  ) {
    super(message)
    this.status = status
    this.fields = fields
    this.code = code
    this.details = details
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function apiError(response: Response, body: unknown, fallback: string): ApiError {
  const message = isRecord(body) && typeof body.message === 'string' ? body.message : fallback
  const fields =
    isRecord(body) && isRecord(body.fields)
      ? Object.fromEntries(Object.entries(body.fields).filter((e): e is [string, string] => typeof e[1] === 'string'))
      : {}
  const code = isRecord(body) && typeof body.error === 'string' ? body.error : ''
  const details = isRecord(body) && isRecord(body.details) ? body.details : {}
  return new ApiError(response.status, message, fields, code, details)
}
