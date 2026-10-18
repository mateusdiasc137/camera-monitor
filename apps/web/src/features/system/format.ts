/** "há 25 s" em relação ao instante da verificação (não ao relógio do navegador). */
export function ago(from: string, reference: string): string {
  const seconds = Math.max(0, Math.round((new Date(reference).getTime() - new Date(from).getTime()) / 1000))
  if (seconds < 120) return `há ${seconds} s`
  if (seconds < 7200) return `há ${Math.floor(seconds / 60)} min`
  if (seconds < 172800) return `há ${Math.floor(seconds / 3600)} h`
  return `há ${Math.floor(seconds / 86400)} dias`
}
