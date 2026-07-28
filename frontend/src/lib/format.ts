/** Small display helpers shared by the publishing components. */

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return minutes > 0 ? `${minutes} dk ${rest} sn` : `${rest} sn`
}

export function formatBytes(bytes: number): string {
  if (bytes > 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(2)} GB`
  if (bytes > 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`
  if (bytes > 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

/**
 * A timestamp as the user's own clock shows it.
 *
 * Everything scheduled in this app is entered and read in Europe/Istanbul, so
 * the zone is pinned rather than left to whatever the machine is set to.
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('tr-TR', {
    timeZone: 'Europe/Istanbul',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * The current Istanbul wall-clock time as a `datetime-local` value.
 *
 * Used as the `min` of the schedule input so a past time cannot be picked by
 * accident — the backend still rejects one, but the field should not offer it.
 */
export function istanbulNowInputValue(offsetMinutes = 0): string {
  const now = new Date(Date.now() + offsetMinutes * 60_000)
  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Europe/Istanbul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(now)
  return parts.replace(' ', 'T')
}
