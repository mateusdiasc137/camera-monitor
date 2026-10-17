import { vi } from 'vitest'

export type Route = (request: Request) => Response | Promise<Response>

/** Servidor falso: responde por "MÉTODO /caminho" (caminhos com {param} casam qualquer segmento). */
export function fakeServer(routes: Record<string, Route>) {
  const calls: Request[] = []
  const compiled = Object.entries(routes).map(([key, route]) => {
    const [method, path] = key.split(' ')
    const pattern = new RegExp('^' + path.replace(/\{[^}]+\}/g, '[^/]+') + '$')
    return { method, pattern, route }
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      calls.push(request.clone())
      const pathname = new URL(request.url).pathname
      const match = compiled.find((r) => r.method === request.method && r.pattern.test(pathname))
      return match ? match.route(request) : new Response('não encontrado', { status: 404 })
    }),
  )
  return calls
}
