import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'
import type { Recordings } from '../../api/client'
import {
  formatClock,
  formatSpan,
  GAP_LABEL,
  normalizeSelection,
  panBy,
  placeView,
  ratioOf,
  revealRange,
  spanMs,
  ticks,
  timeAt,
  zoomBy,
  zoomStep,
  MIN_SELECTION_MS,
  type Range,
} from './trim'

type Gap = Recordings['gaps'][number]

type Props = {
  /** O dia inteiro (limites da navegação). */
  bounds: Range
  data: Recordings | undefined
  /** Instante em reprodução (cursor). */
  position: Date | null
  /** Trecho marcado para download (alças de início e fim). */
  selection: Range | null
  /** `preview`: mostrar o quadro do ponto de corte (sem pular para outro trecho se ali não há gravação). */
  onSeek: (moment: Date, kind?: 'seek' | 'preview') => void
  onSelectionChange: (selection: Range) => void
}

/** Largura do elemento (régua com menos marcas em telas estreitas). */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(1000)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width || 1000))
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])
  return width
}

type Gesture =
  | { mode: 'tap' | 'pan'; x0: number; view0: Range }
  | { mode: 'pinch'; dist0: number; view0: Range; anchor: Date }
  | { mode: 'done' }

const TAP_SLOP_PX = 6
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

function Segments({ data, view }: { data: Recordings | undefined; view: Range }) {
  const place = (start: string, end: string, minPct = 0) => {
    const l = ratioOf(new Date(start), view)
    const r = ratioOf(new Date(end), view)
    if (r < 0 || l > 1) return null
    const left = clamp01(l) * 100
    return { left: `${left}%`, width: `${Math.max(minPct, clamp01(r) * 100 - left)}%` }
  }
  return (
    <>
      {data?.gaps.map((g) => {
        const style = place(g.start, g.end)
        return style ? (
          <span
            key={`g-${g.start}`}
            className={`timeline__gap timeline__gap--${g.reason}`}
            style={style}
            title={`${GAP_LABEL[g.reason]}: ${formatClock(new Date(g.start), false)}–${formatClock(new Date(g.end), false)}`}
          />
        ) : null
      })}
      {data?.intervals.map((i) => {
        const style = place(i.start, i.end, 0.3)
        return style ? (
          <span
            key={`i-${i.start}`}
            className="timeline__rec"
            data-testid="timeline-interval"
            style={style}
            title={`Gravado: ${formatClock(new Date(i.start))}–${formatClock(new Date(i.end))}`}
          />
        ) : null
      })}
    </>
  )
}

/**
 * Barra de gravações com corte, como a de um editor de vídeo simples: trechos gravados e buracos, cursor
 * de reprodução e duas alças (início/fim do trecho a baixar). Zoom por botões, pinça ou roda do mouse;
 * arrastar a barra desloca a janela; tocar leva a reprodução para o ponto tocado.
 */
export function Timeline({ bounds, data, position, selection, onSeek, onSelectionChange }: Props) {
  const [view, setView] = useState<Range>(bounds)
  const [viewDay, setViewDay] = useState(bounds.start.getTime())
  if (viewDay !== bounds.start.getTime()) {
    setViewDay(bounds.start.getTime()) // outro dia: volta a mostrar as 24 h
    setView(bounds)
  }
  const barRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef(view)
  const pointers = useRef(new Map<number, number>())
  const gesture = useRef<Gesture | null>(null)
  const barWidth = useWidth(barRef)
  useEffect(() => {
    viewRef.current = view // para o ouvinte da roda do mouse
  }, [view])

  // Trecho alterado por fora (horários digitados): mantê-lo à vista.
  const selKey = selection ? `${selection.start.getTime()}-${selection.end.getTime()}` : ''
  const [seenSel, setSeenSel] = useState(selKey)
  if (seenSel !== selKey) {
    setSeenSel(selKey)
    if (selection) setView(revealRange(view, selection, bounds))
  }

  // Reprodução saiu pela direita da janela: a janela acompanha (só se o cursor estava visível).
  const [seenPosition, setSeenPosition] = useState(position)
  if (seenPosition !== position) {
    setSeenPosition(position)
    const prev = seenPosition
    if (prev && position && prev >= view.start && prev <= view.end && position > view.end && position <= bounds.end) {
      setView(placeView(spanMs(view), position, 0.1, bounds))
    }
  }

  useEffect(() => {
    // Roda do mouse: zoom no ponto do cursor (vertical) ou deslocamento (horizontal). Não passivo.
    const bar = barRef.current
    if (!bar) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = bar.getBoundingClientRect()
      const v = viewRef.current
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        setView(panBy(v, (e.deltaX / rect.width) * spanMs(v), bounds))
      } else if (e.deltaY !== 0) {
        const anchor = timeAt(clamp01((e.clientX - rect.left) / rect.width), v)
        setView(zoomBy(v, e.deltaY < 0 ? 1.25 : 1 / 1.25, anchor, bounds))
      }
    }
    bar.addEventListener('wheel', onWheel, { passive: false })
    return () => bar.removeEventListener('wheel', onWheel)
  }, [bounds])

  function ratioAtX(x: number): number {
    const rect = barRef.current!.getBoundingClientRect()
    return rect.width > 0 ? clamp01((x - rect.left) / rect.width) : 0
  }

  // ---- barra: tocar (ir para), arrastar (deslocar), pinça (zoom)
  function onBarDown(e: PointerEvent<HTMLDivElement>) {
    e.currentTarget.setPointerCapture?.(e.pointerId)
    pointers.current.set(e.pointerId, e.clientX)
    if (pointers.current.size === 1) {
      gesture.current = { mode: 'tap', x0: e.clientX, view0: view }
    } else if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      gesture.current = {
        mode: 'pinch',
        dist0: Math.max(10, Math.abs(a - b)),
        view0: view,
        anchor: timeAt(ratioAtX((a + b) / 2), view),
      }
    }
  }

  function onBarMove(e: PointerEvent<HTMLDivElement>) {
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.set(e.pointerId, e.clientX)
    const g = gesture.current
    if (!g || g.mode === 'done') return
    const width = barRef.current!.getBoundingClientRect().width || 1
    if (g.mode === 'pinch' && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()]
      const factor = Math.max(10, Math.abs(a - b)) / g.dist0
      setView(placeView(spanMs(g.view0) / factor, g.anchor, ratioAtX((a + b) / 2), bounds))
    } else if (g.mode !== 'pinch') {
      const dx = e.clientX - g.x0
      if (g.mode === 'tap' && Math.abs(dx) > TAP_SLOP_PX) g.mode = 'pan'
      if (g.mode === 'pan') setView(panBy(g.view0, (-dx / width) * spanMs(g.view0), bounds))
    }
  }

  function onBarUp(e: PointerEvent<HTMLDivElement>) {
    if (!pointers.current.delete(e.pointerId)) return
    const g = gesture.current
    if (g?.mode === 'tap' && pointers.current.size === 0) onSeek(timeAt(ratioAtX(e.clientX), view))
    gesture.current = pointers.current.size === 0 ? null : { mode: 'done' }
  }

  function onBarCancel(e: PointerEvent<HTMLDivElement>) {
    pointers.current.delete(e.pointerId)
    gesture.current = pointers.current.size === 0 ? null : { mode: 'done' }
  }

  function onBarKey(e: KeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? 60_000 : 10_000
    const base = position ?? timeAt(0.5, view)
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      onSeek(new Date(base.getTime() + (e.key === 'ArrowRight' ? step : -step)))
    } else if (e.key === '+' || e.key === '=' || e.key === '-') {
      e.preventDefault()
      setView(zoomStep(view, e.key === '-' ? -1 : 1, base, bounds))
    }
  }

  // ---- alças de corte
  function moveHandle(which: 'start' | 'end', t: Date) {
    if (!selection) return
    const next =
      which === 'start'
        ? { start: new Date(Math.min(t.getTime(), selection.end.getTime() - MIN_SELECTION_MS)), end: selection.end }
        : { start: selection.start, end: new Date(Math.max(t.getTime(), selection.start.getTime() + MIN_SELECTION_MS)) }
    onSelectionChange(normalizeSelection(next, bounds))
  }

  function preview(which: 'start' | 'end') {
    if (!selection) return
    // Mostra o quadro do ponto de corte: o início, ou alguns segundos antes do fim.
    const moment = which === 'start' ? selection.start : new Date(Math.max(selection.start.getTime(), selection.end.getTime() - 3000))
    onSeek(moment, 'preview')
  }

  function handleProps(which: 'start' | 'end') {
    const value = which === 'start' ? selection!.start : selection!.end
    return {
      role: 'slider',
      tabIndex: 0,
      'aria-label': which === 'start' ? 'Início do corte' : 'Fim do corte',
      'aria-valuemin': bounds.start.getTime(),
      'aria-valuemax': bounds.end.getTime(),
      'aria-valuenow': value.getTime(),
      'aria-valuetext': formatClock(value),
      className: `trim__handle trim__handle--${which}`,
      style: { left: `${ratioOf(value, view) * 100}%` },
      onPointerDown: (e: PointerEvent<HTMLDivElement>) => {
        e.stopPropagation()
        e.currentTarget.setPointerCapture?.(e.pointerId)
        e.currentTarget.dataset.dragging = 'true'
      },
      onPointerMove: (e: PointerEvent<HTMLDivElement>) => {
        if (e.currentTarget.dataset.dragging !== 'true') return
        moveHandle(which, timeAt(ratioAtX(e.clientX), view))
      },
      onPointerUp: (e: PointerEvent<HTMLDivElement>) => {
        if (e.currentTarget.dataset.dragging !== 'true') return
        delete e.currentTarget.dataset.dragging
        preview(which)
      },
      onPointerCancel: (e: PointerEvent<HTMLDivElement>) => {
        delete e.currentTarget.dataset.dragging
      },
      onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => {
        const steps: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, PageDown: -60, PageUp: 60 }
        if (!(e.key in steps)) return
        e.preventDefault()
        moveHandle(which, new Date(value.getTime() + steps[e.key] * (e.shiftKey ? 10_000 : 1000)))
      },
    } as const
  }

  const visible = (t: Date) => t >= view.start && t <= view.end
  const selLeft = selection ? clamp01(ratioOf(selection.start, view)) * 100 : 0
  const selRight = selection ? clamp01(ratioOf(selection.end, view)) * 100 : 0
  const marks = ticks(view, bounds.start, Math.max(3, Math.floor(barWidth / 95))).filter((m) => {
    const r = ratioOf(m.at, view)
    return r >= 0.03 && r <= 0.97 // rótulo colado na borda sairia cortado
  })
  const anchor = position && visible(position) ? position : selection ? new Date((selection.start.getTime() + selection.end.getTime()) / 2) : timeAt(0.5, view)

  return (
    <div className="timeline trim">
      <div className="trim__track">
        <div
          ref={barRef}
          className="timeline__bar"
          role="slider"
          aria-label="Linha do tempo das gravações"
          aria-valuemin={bounds.start.getTime()}
          aria-valuemax={bounds.end.getTime()}
          aria-valuenow={position?.getTime() ?? view.start.getTime()}
          aria-valuetext={position ? position.toLocaleString('pt-BR') : 'nenhum trecho em reprodução'}
          tabIndex={0}
          onPointerDown={onBarDown}
          onPointerMove={onBarMove}
          onPointerUp={onBarUp}
          onPointerCancel={onBarCancel}
          onKeyDown={onBarKey}
        >
          <Segments data={data} view={view} />
          {selection && (
            <>
              <span className="trim__mask" style={{ left: 0, width: `${selLeft}%` }} />
              <span className="trim__mask" style={{ left: `${selRight}%`, right: 0 }} />
              <span
                className="trim__selection"
                data-testid="trim-selection"
                style={{ left: `${selLeft}%`, width: `${selRight - selLeft}%` }}
              />
            </>
          )}
          {position && visible(position) && (
            <span className="timeline__cursor" style={{ left: `${ratioOf(position, view) * 100}%` }} />
          )}
        </div>
        {selection && visible(selection.start) && <div {...handleProps('start')} />}
        {selection && visible(selection.end) && <div {...handleProps('end')} />}
      </div>
      <div className="trim__ticks" aria-hidden="true">
        {marks.map((m) => (
          <span key={m.at.getTime()} style={{ left: `${ratioOf(m.at, view) * 100}%` }}>
            {formatClock(m.at, m.step < 60_000)}
          </span>
        ))}
      </div>
      <div className="trim__zoom" role="group" aria-label="Zoom da linha do tempo">
        <button type="button" className="secondary" aria-label="Afastar" onClick={() => setView(zoomStep(view, -1, anchor, bounds))}>
          −
        </button>
        <span className="trim__span" aria-live="polite">
          {formatClock(view.start, false)}–{formatClock(view.end, false)} · {formatSpan(spanMs(view))}
        </span>
        <button type="button" className="secondary" aria-label="Aproximar" onClick={() => setView(zoomStep(view, 1, anchor, bounds))}>
          +
        </button>
        <button type="button" className="secondary" aria-label="Voltar no tempo" onClick={() => setView(panBy(view, -spanMs(view) / 2, bounds))}>
          ◀
        </button>
        <button type="button" className="secondary" aria-label="Avançar no tempo" onClick={() => setView(panBy(view, spanMs(view) / 2, bounds))}>
          ▶
        </button>
        {selection && (
          <button
            type="button"
            className="secondary"
            onClick={() => setView(placeView(spanMs(selection) * 1.25, new Date((selection.start.getTime() + selection.end.getTime()) / 2), 0.5, bounds))}
          >
            Ajustar ao corte
          </button>
        )}
        <button type="button" className="secondary" onClick={() => setView(bounds)}>
          Dia inteiro
        </button>
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
        <li>
          <span className="swatch trim__selection-swatch" /> Trecho a baixar
        </li>
      </ul>
    </div>
  )
}
