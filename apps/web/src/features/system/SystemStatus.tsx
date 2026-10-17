import { useQuery } from '@tanstack/react-query'
import { api, type SystemHealth } from '../../api/client'

const LABEL: Record<SystemHealth['state'], string> = {
  ok: 'Operando normalmente',
  degraded: 'Operando com problemas',
  down: 'Fora do ar',
}

async function fetchHealth(): Promise<SystemHealth> {
  const { data, error } = await api.GET('/api/v1/system/health')
  if (error || !data) throw new Error('Não foi possível consultar o servidor')
  return data
}

export function SystemStatus() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['system', 'health'],
    queryFn: fetchHealth,
    refetchInterval: 10_000,
  })

  if (isPending) return <p className="status">Consultando o servidor…</p>
  if (isError) return <p className="status status--down">Servidor indisponível</p>

  return (
    <section className={`status status--${data.state}`} aria-live="polite">
      <strong>{LABEL[data.state]}</strong>
      <span className="muted"> · versão {data.version}</span>
      {data.components.length > 0 && (
        <ul>
          {data.components.map((c) => (
            <li key={c.name}>
              {c.name}: {LABEL[c.state]}
              {c.detail && ` — ${c.detail}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
