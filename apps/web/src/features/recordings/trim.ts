/** Funções puras da barra de corte: janela visível (zoom), régua, horários e o intervalo selecionado. */
import type { Recordings } from '../../api/client'

export type Range = { start: Date; end: Date }

export const GAP_LABEL: Record<Recordings['gaps'][number]['reason'], string> = {
  paused: 'Gravação pausada',
  camera_unavailable: 'Câmera indisponível',
  disabled: 'Câmera desativada',
  unknown: 'Sem gravação',
}

const S = 1000
const MIN = 60 * S
const H = 60 * MIN

/** Níveis dos botões de zoom (largura da janela visível). */
export const ZOOM_LEVELS_MS = [24 * H, 12 * H, 6 * H, 3 * H, H, 30 * MIN, 15 * MIN, 5 * MIN, 2 * MIN, MIN]
export const MIN_SPAN_MS = 30 * S
export const MAX_DOWNLOAD_S = 2 * 3600
export const MIN_SELECTION_MS = S
export const DEFAULT_SELECTION_MS = MIN

const TICK_STEPS_MS = [S, 5 * S, 10 * S, 15 * S, 30 * S, MIN, 2 * MIN, 5 * MIN, 10 * MIN, 15 * MIN, 30 * MIN, H, 2 * H, 3 * H, 6 * H]

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export function spanMs(r: Range): number {
  return r.end.getTime() - r.start.getTime()
}

/** Posição (0–1) de um instante na janela. */
export function ratioOf(t: Date, view: Range): number {
  return (t.getTime() - view.start.getTime()) / spanMs(view)
}

export function timeAt(ratio: number, view: Range): Date {
  return new Date(view.start.getTime() + ratio * spanMs(view))
}

/** Janela de largura `span` com `anchor` na posição `ratio`, sem sair dos limites (o dia). */
export function placeView(span: number, anchor: Date, ratio: number, bounds: Range): Range {
  const width = clamp(span, Math.min(MIN_SPAN_MS, spanMs(bounds)), spanMs(bounds))
  const start = clamp(anchor.getTime() - ratio * width, bounds.start.getTime(), bounds.end.getTime() - width)
  return { start: new Date(start), end: new Date(start + width) }
}

/** Zoom contínuo (pinça, roda do mouse): `factor` > 1 aproxima, mantendo `anchor` no mesmo lugar. */
export function zoomBy(view: Range, factor: number, anchor: Date, bounds: Range): Range {
  return placeView(spanMs(view) / factor, anchor, ratioOf(anchor, view), bounds)
}

/** Próximo nível dos botões: `direction` 1 aproxima, -1 afasta. */
export function zoomStep(view: Range, direction: 1 | -1, anchor: Date, bounds: Range): Range {
  const span = spanMs(view)
  const next =
    direction > 0
      ? ZOOM_LEVELS_MS.find((l) => l < span - 1) ?? ZOOM_LEVELS_MS[ZOOM_LEVELS_MS.length - 1]
      : [...ZOOM_LEVELS_MS].reverse().find((l) => l > span + 1) ?? ZOOM_LEVELS_MS[0]
  const ratio = clamp(ratioOf(anchor, view), 0, 1)
  return placeView(next, anchor, ratio, bounds)
}

/** Desloca a janela (arrastar, botões ◀ ▶), sem sair do dia. */
export function panBy(view: Range, deltaMs: number, bounds: Range): Range {
  return placeView(spanMs(view), new Date(view.start.getTime() + deltaMs), 0, bounds)
}

/** Garante que o intervalo apareça na janela (ajusta o zoom se ele for maior que ela). */
export function revealRange(view: Range, target: Range, bounds: Range): Range {
  const width = spanMs(view)
  if (target.start >= view.start && target.end <= view.end) return view
  const needed = spanMs(target) * 1.25
  const span = needed > width ? needed : width
  const center = new Date((target.start.getTime() + target.end.getTime()) / 2)
  return placeView(span, center, 0.5, bounds)
}

/** Marcas da régua alinhadas ao relógio local (a partir da meia-noite do dia). */
export function ticks(view: Range, dayStart: Date, maxTicks = 8): { at: Date; step: number }[] {
  const span = spanMs(view)
  const step = TICK_STEPS_MS.find((s) => span / s <= maxTicks) ?? TICK_STEPS_MS[TICK_STEPS_MS.length - 1]
  const base = dayStart.getTime()
  const first = base + Math.ceil((view.start.getTime() - base) / step) * step
  const out: { at: Date; step: number }[] = []
  for (let t = first; t <= view.end.getTime(); t += step) out.push({ at: new Date(t), step })
  return out
}

const pad = (n: number) => String(n).padStart(2, '0')

/** "HH:MM:SS" (ou "HH:MM") no horário local. */
export function formatClock(d: Date, seconds = true): string {
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return seconds ? `${hm}:${pad(d.getSeconds())}` : hm
}

/** Lê "HH:MM" ou "HH:MM:SS" como horário do dia de `dayStart`. */
export function parseClock(text: string, dayStart: Date): Date | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(text.trim())
  if (!m) return null
  const [h, mi, s] = [Number(m[1]), Number(m[2]), Number(m[3] ?? 0)]
  if (h > 23 || mi > 59 || s > 59) return null
  const d = new Date(dayStart)
  d.setHours(h, mi, s, 0)
  return d
}

export function formatSpan(ms: number): string {
  const total = Math.round(ms / 1000)
  if (total < 60) return `${total} s`
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return m ? `${h} h ${m} min` : `${h} h`
  return s ? `${m} min ${s} s` : `${m} min`
}

/** Arredonda ao segundo e mantém o intervalo dentro do dia, com duração mínima. */
export function normalizeSelection(sel: Range, bounds: Range): Range {
  const round = (d: Date) => Math.round(d.getTime() / S) * S
  const lo = bounds.start.getTime()
  const hi = bounds.end.getTime()
  const start = clamp(round(sel.start), lo, hi - MIN_SELECTION_MS)
  const end = clamp(round(sel.end), start + MIN_SELECTION_MS, hi)
  return { start: new Date(start), end: new Date(end) }
}

/** Segundos gravados dentro do intervalo (o download junta só esses trechos). */
export function recordedWithin(sel: Range, intervals: { start: string; end: string }[]): number {
  let total = 0
  for (const i of intervals) {
    const s = Math.max(sel.start.getTime(), new Date(i.start).getTime())
    const e = Math.min(sel.end.getTime(), new Date(i.end).getTime())
    if (e > s) total += e - s
  }
  return total / 1000
}

export type Clip = { start: Date; durationS: number }
const MAX_CLIP_S = 30 * 60

/** Próximo trecho a tocar a partir de um instante: dentro de um intervalo, ou o início do seguinte. */
export function clipFrom(moment: Date, data: Recordings | undefined): Clip | null {
  for (const interval of data?.intervals ?? []) {
    const s = new Date(interval.start)
    const e = new Date(interval.end)
    if (e <= moment) continue
    const start = moment > s ? moment : s
    const remaining = (e.getTime() - start.getTime()) / 1000
    if (remaining < 1) continue
    return { start, durationS: Math.min(MAX_CLIP_S, remaining) }
  }
  return null
}
