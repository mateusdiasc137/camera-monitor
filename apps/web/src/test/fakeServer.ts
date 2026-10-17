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
    vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), location.origin), init)
      calls.push(request.clone())
      const pathname = new URL(request.url).pathname
      const match = compiled.find((r) => r.method === request.method && r.pattern.test(pathname))
      return match ? match.route(request) : new Response('não encontrado', { status: 404 })
    }),
  )
  return calls
}
