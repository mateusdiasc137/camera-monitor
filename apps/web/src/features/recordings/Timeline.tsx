import type { MouseEvent } from 'react'
import type { Recordings } from '../../api/client'

type Gap = Recordings['gaps'][number]

export const GAP_LABEL: Record<Gap['reason'], string> = {
  paused: 'Gravação pausada',
  camera_unavailable: 'Câmera indisponível',
  disabled: 'Câmera desativada',
  unknown: 'Sem gravação',
}

type Props = {
  start: Date
  end: Date
  data: Recordings | undefined
  /** Instante em reprodução (marcador). */
  position: Date | null
  onSelect: (moment: Date) => void
}

const pct = (t: Date, start: Date, end: Date) =>
  Math.min(100, Math.max(0, ((t.getTime() - start.getTime()) / (end.getTime() - start.getTime())) * 100))

const hhmm = (d: Date) => d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })

/** Barra de 24 h (ou da janela): trechos gravados, buracos coloridos pelo motivo e marcador de reprodução. */
export function Timeline({ start, end, data, position, onSelect }: Props) {
  const spanMs = end.getTime() - start.getTime()
  const ticks = Array.from({ length: 9 }, (_, i) => new Date(start.getTime() + (spanMs * i) / 8))

  function handleClick(event: MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = (event.clientX - rect.left) / rect.width
    onSelect(new Date(start.getTime() + ratio * spanMs))
  }

  return (
    <div className="timeline">
      <div
        className="timeline__bar"
        role="slider"
        aria-label="Linha do tempo das gravações"
        aria-valuemin={start.getTime()}
        aria-valuemax={end.getTime()}
        aria-valuenow={position?.getTime() ?? start.getTime()}
        aria-valuetext={position ? position.toLocaleString('pt-BR') : 'nenhum trecho selecionado'}
        tabIndex={0}
        onClick={handleClick}
      >
        {data?.gaps.map((g) => {
          const s = new Date(g.start)
          const e = new Date(g.end)
          return (
            <span
              key={`g-${g.start}`}
              className={`timeline__gap timeline__gap--${g.reason}`}
              style={{ left: `${pct(s, start, end)}%`, width: `${pct(e, start, end) - pct(s, start, end)}%` }}
              title={`${GAP_LABEL[g.reason]}: ${hhmm(s)}–${hhmm(e)}`}
            />
          )
        })}
        {data?.intervals.map((i) => {
          const s = new Date(i.start)
          const e = new Date(i.end)
          return (
            <span
              key={`i-${i.start}`}
              className="timeline__rec"
              data-testid="timeline-interval"
              style={{ left: `${pct(s, start, end)}%`, width: `${Math.max(0.4, pct(e, start, end) - pct(s, start, end))}%` }}
              title={`Gravado: ${hhmm(s)}–${hhmm(e)}`}
            />
          )
        })}
        {position && position >= start && position <= end && (
          <span className="timeline__cursor" style={{ left: `${pct(position, start, end)}%` }} />
        )}
      </div>
      <div className="timeline__ticks" aria-hidden="true">
        {ticks.map((t) => (
          <span key={t.getTime()}>{hhmm(t)}</span>
        ))}
      </div>
      <ul className="timeline__legend">
        <li>
          <span className="swatch timeline__rec" /> Gravado
        </li>
        {(Object.keys(GAP_LABEL) as Gap['reason'][]).map((r) => (
          <li key={r}>
            <span className={`swatch timeline__gap--${r}`} /> {GAP_LABEL[r]}
          </li>
        ))}
      </ul>
    </div>
  )
}
