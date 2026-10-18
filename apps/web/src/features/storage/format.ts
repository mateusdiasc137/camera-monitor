const GB = 1024 ** 3

/** Tamanho em KB/MB/GB com vírgula decimal (pt-BR). */
export function formatBytes(bytes: number): string {
  const fmt = (v: number, digits: number) =>
    v.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  if (bytes >= GB) return `${fmt(bytes / GB, bytes >= 100 * GB ? 0 : 1)} GB`
  if (bytes >= 1024 ** 2) return `${fmt(bytes / 1024 ** 2, 0)} MB`
  return `${fmt(bytes / 1024, 0)} KB`
}

/** Junta uma subpasta ao caminho no estilo do servidor (Windows ou Linux). */
export function joinPath(base: string, name: string): string {
  const sep = base.includes('\\') && !base.includes('/') ? '\\' : '/'
  return base.endsWith(sep) ? base + name : base + sep + name
}
