const MINUS = '\u2212'
const THIN_SPACE = '\u2009'

/** Fixed decimals, thin-space thousands, a real minus sign; `sign` adds + to positives. */
export function formatNumber(value: number, decimals = 2, sign = false): string {
  const rounded = Number(value.toFixed(decimals))
  const negative = rounded < 0
  const [whole, fraction] = Math.abs(rounded).toFixed(decimals).split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, THIN_SPACE)
  const body = fraction !== undefined ? `${grouped}.${fraction}` : grouped
  if (negative) return `${MINUS}${body}`
  return sign && rounded > 0 ? `+${body}` : body
}

/** Server time as MetaTrader shows it: 2025.03.07 10:00:00 reads as 2025-03-07 10:00:00. */
export function formatDateTime(value: string | null | undefined, seconds = true): string {
  if (!value) return ''
  const text = value.replace('T', ' ').replace(/(\.\d+)?(Z|[+-]\d\d:\d\d)?$/, '')
  return seconds ? text.slice(0, 19) : text.slice(0, 16)
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return ''
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes} min ${rest} s`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}
