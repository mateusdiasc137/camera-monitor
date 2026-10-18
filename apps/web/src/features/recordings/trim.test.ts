import { describe, expect, it } from 'vitest'
import type { Recordings } from '../../api/client'
import {
  clipFrom,
  formatSpan,
  normalizeSelection,
  panBy,
  parseClock,
  placeView,
  recordedWithin,
  revealRange,
  spanMs,
  ticks,
  zoomBy,
  zoomStep,
} from './trim'

const day = new Date()
day.setHours(0, 0, 0, 0)
const at = (h: number, m = 0, s = 0) => new Date(day.getTime() + ((h * 60 + m) * 60 + s) * 1000)
const BOUNDS = { start: at(0), end: at(24) }
const H = 3600_000

const RECORDINGS = {
  intervals: [
    { start: at(6).toISOString(), end: at(7).toISOString() },
    { start: at(12).toISOString(), end: at(12, 30).toISOString() },
  ],
} as Recordings

describe('clipFrom', () => {
  it('toca a partir do instante, limitado a 30 min e ao fim do trecho', () => {
    expect(clipFrom(at(6, 10), RECORDINGS)).toEqual({ start: at(6, 10), durationS: 30 * 60 })
    expect(clipFrom(at(6, 50), RECORDINGS)).toEqual({ start: at(6, 50), durationS: 10 * 60 })
  })

  it('num buraco, pula para o próximo trecho; depois do último, nada', () => {
    expect(clipFrom(at(9), RECORDINGS)).toEqual({ start: at(12), durationS: 30 * 60 })
    expect(clipFrom(at(13), RECORDINGS)).toBeNull()
  })
})

describe('janela (zoom)', () => {
  it('os botões passam pelos níveis mantendo o ponto de referência', () => {
    const v = zoomStep(BOUNDS, 1, at(6), BOUNDS)
    expect(spanMs(v)).toBe(12 * H)
    expect(v.start).toEqual(at(3)) // 06:00 continua a 25% da janela
    expect(spanMs(zoomStep(v, -1, at(6), BOUNDS))).toBe(24 * H)
    expect(zoomStep(BOUNDS, -1, at(6), BOUNDS)).toEqual(BOUNDS) // não passa do dia
  })

  it('pinça e roda do mouse dão zoom contínuo, sem sair do dia', () => {
    const v = zoomBy(BOUNDS, 4, at(18), BOUNDS) // 18:00 fica a 75% da janela, como antes
    expect(spanMs(v)).toBe(6 * H)
    expect(v).toEqual({ start: at(13, 30), end: at(19, 30) })
    expect(zoomBy(BOUNDS, 4, at(24), BOUNDS).end).toEqual(at(24))
    expect(spanMs(zoomBy(v, 1e6, at(23), BOUNDS))).toBe(30_000) // mínimo de 30 s
  })

  it('deslocar não sai dos limites', () => {
    const v = placeView(H, at(12), 0.5, BOUNDS)
    expect(panBy(v, 30 * 60_000, BOUNDS).start).toEqual(at(12))
    expect(panBy(v, -48 * H, BOUNDS).start).toEqual(at(0))
  })

  it('mostra o trecho escolhido quando ele está fora da janela', () => {
    const v = placeView(H, at(6), 0, BOUNDS)
    expect(revealRange(v, { start: at(6, 10), end: at(6, 20) }, BOUNDS)).toBe(v)
    const moved = revealRange(v, { start: at(12), end: at(12, 10) }, BOUNDS)
    expect(spanMs(moved)).toBe(H)
    expect(moved.start <= at(12) && moved.end >= at(12, 10)).toBe(true)
    expect(spanMs(revealRange(v, { start: at(8), end: at(12) }, BOUNDS))).toBe(5 * H) // maior: afasta
  })

  it('a régua escolhe o passo pela largura da janela', () => {
    expect(ticks(BOUNDS, day).map((t) => t.at.getHours())).toEqual([0, 3, 6, 9, 12, 15, 18, 21, 0])
    const minute = ticks(placeView(60_000, at(10, 0, 20), 0, BOUNDS), day)
    expect(minute[0].step).toBe(10_000)
    expect(minute[0].at).toEqual(at(10, 0, 20))
  })
})

describe('horários e corte', () => {
  it('lê HH:MM e HH:MM:SS no dia', () => {
    expect(parseClock('06:30', day)).toEqual(at(6, 30))
    expect(parseClock('6:30:15', day)).toEqual(at(6, 30, 15))
    expect(parseClock('24:00', day)).toBeNull()
    expect(parseClock('', day)).toBeNull()
  })

  it('arredonda ao segundo e mantém duração mínima dentro do dia', () => {
    const sel = normalizeSelection({ start: new Date(at(6).getTime() + 400), end: new Date(at(6).getTime() + 600) }, BOUNDS)
    expect(sel).toEqual({ start: at(6), end: at(6, 0, 1) })
    expect(normalizeSelection({ start: at(23, 59, 59), end: at(25) }, BOUNDS)).toEqual({
      start: at(23, 59, 59),
      end: at(24),
    })
  })

  it('soma só o que foi gravado dentro do corte', () => {
    expect(recordedWithin({ start: at(6, 50), end: at(12, 10) }, RECORDINGS.intervals)).toBe(20 * 60)
  })

  it('formata durações', () => {
    expect(formatSpan(45_000)).toBe('45 s')
    expect(formatSpan(90_000)).toBe('1 min 30 s')
    expect(formatSpan(2 * H + 5 * 60_000)).toBe('2 h 5 min')
  })
})
