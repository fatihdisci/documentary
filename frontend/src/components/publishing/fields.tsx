/**
 * Small, platform-agnostic form pieces for the Publish panel.
 *
 * Deliberately shared: the YouTube card and the three cards that are still UI
 * only use the same tag editor, the same counters and the same schedule picker,
 * so adding a real Instagram integration later is a matter of giving its card a
 * backend, not rebuilding its form.
 */

import { useState, type ReactNode } from 'react'
import type { PublishMode } from '@/api/publishing-types'
import { istanbulNowInputValue } from '@/lib/format'

interface CountedFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  /** Current usage and its limit, shown live beside the field. */
  used: number
  limit: number
  unit: string
  hint?: ReactNode
  rows?: number
  disabled?: boolean
  id: string
}

/** A single-line field with a live character counter. */
export function CountedInput({
  label, value, onChange, used, limit, unit, hint, disabled, id,
}: CountedFieldProps) {
  const over = used > limit
  return (
    <label className="publish-field" htmlFor={id}>
      <span className="publish-field-head">
        {label}
        <span className={`publish-counter ${over ? 'over' : ''}`}>
          {used} / {limit} {unit}
        </span>
      </span>
      <input
        id={id}
        value={value}
        disabled={disabled}
        aria-invalid={over || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

/** A multi-line field with a live counter. */
export function CountedTextarea({
  label, value, onChange, used, limit, unit, hint, rows = 8, disabled, id,
}: CountedFieldProps) {
  const over = used > limit
  return (
    <label className="publish-field" htmlFor={id}>
      <span className="publish-field-head">
        {label}
        <span className={`publish-counter ${over ? 'over' : ''}`}>
          {used} / {limit} {unit}
        </span>
      </span>
      <textarea
        id={id}
        rows={rows}
        value={value}
        disabled={disabled}
        aria-invalid={over || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && <span className="hint">{hint}</span>}
    </label>
  )
}

interface TagEditorProps {
  label: string
  tags: string[]
  onChange: (tags: string[]) => void
  /** Shown live; the caller decides how the platform counts them. */
  used?: number
  limit?: number
  disabled?: boolean
  id: string
}

/**
 * Tags as chips.
 *
 * Enter or a comma commits a tag; empty and repeated values are dropped rather
 * than quietly sent to a platform that would reject the whole request.
 */
export function TagEditor({ label, tags, onChange, used, limit, disabled, id }: TagEditorProps) {
  const [text, setText] = useState('')
  const over = used !== undefined && limit !== undefined && used > limit

  function commit(raw: string) {
    const additions = raw
      .split(',')
      .map((part) => part.trim().replace(/\s+/g, ' '))
      .filter(Boolean)
    if (additions.length === 0) return
    const next = [...tags]
    for (const tag of additions) {
      if (!next.some((existing) => existing.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
        next.push(tag)
      }
    }
    onChange(next)
    setText('')
  }

  return (
    <div className="publish-field">
      <span className="publish-field-head">
        <label htmlFor={id}>{label}</label>
        {used !== undefined && limit !== undefined && (
          <span className={`publish-counter ${over ? 'over' : ''}`}>
            {used} / {limit} karakter
          </span>
        )}
      </span>
      <div className="tag-chips">
        {tags.map((tag) => (
          <span className="tag-chip" key={tag}>
            {tag}
            <button
              type="button"
              aria-label={`${tag} etiketini kaldır`}
              disabled={disabled}
              onClick={() => onChange(tags.filter((entry) => entry !== tag))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <input
        id={id}
        value={text}
        disabled={disabled}
        placeholder="Etiket yazıp Enter'a basın; virgülle birden fazla ekleyebilirsiniz"
        onChange={(event) => {
          const value = event.target.value
          if (value.includes(',')) commit(value)
          else setText(value)
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            commit(text)
          } else if (event.key === 'Backspace' && !text && tags.length > 0) {
            onChange(tags.slice(0, -1))
          }
        }}
        onBlur={() => commit(text)}
      />
    </div>
  )
}

interface SchedulePickerProps {
  mode: PublishMode
  value: string | null
  onModeChange: (mode: PublishMode) => void
  onValueChange: (value: string | null) => void
  disabled?: boolean
  idPrefix: string
}

/**
 * "Publish now" versus "schedule", plus the time itself.
 *
 * The time is a local wall-clock value in Europe/Istanbul and is sent to the
 * backend exactly as typed; the backend is what binds it to the zone and turns
 * it into an offset-aware instant. The browser's own zone never enters into it.
 */
export function SchedulePicker({
  mode, value, onModeChange, onValueChange, disabled, idPrefix,
}: SchedulePickerProps) {
  return (
    <div className="publish-schedule">
      <div className="publish-modes" role="group" aria-label="Yayın zamanı">
        <label className={mode === 'now' ? 'selected' : ''}>
          <input
            type="radio"
            name={`${idPrefix}-publish-mode`}
            checked={mode === 'now'}
            disabled={disabled}
            onChange={() => onModeChange('now')}
          />
          Hemen yükle
        </label>
        <label className={mode === 'schedule' ? 'selected' : ''}>
          <input
            type="radio"
            name={`${idPrefix}-publish-mode`}
            checked={mode === 'schedule'}
            disabled={disabled}
            onChange={() => onModeChange('schedule')}
          />
          İleri tarihe planla
        </label>
      </div>

      {mode === 'schedule' && (
        <label className="publish-field" htmlFor={`${idPrefix}-publish-at`}>
          <span className="publish-field-head">Planlanan tarih ve saat</span>
          <input
            id={`${idPrefix}-publish-at`}
            type="datetime-local"
            value={value ?? ''}
            min={istanbulNowInputValue(5)}
            disabled={disabled}
            onChange={(event) => onValueChange(event.target.value || null)}
          />
          <span className="hint">Saatler İstanbul saatiyle (Europe/Istanbul) yazılır.</span>
        </label>
      )}
    </div>
  )
}
