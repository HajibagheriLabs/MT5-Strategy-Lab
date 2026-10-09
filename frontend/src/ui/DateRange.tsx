import { useId } from 'react'
import type { ReactNode } from 'react'
import './DateRange.css'

export type Range = { from: string; to: string }

type Preset = { label: string; range: (end: string) => Range }

function shift(day: string, months: number): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCMonth(date.getUTCMonth() + months)
  return date.toISOString().slice(0, 10)
}

/** Presets count back from the end of what is available; the end date is exclusive. */
const PRESETS: Preset[] = [
  { label: '1M', range: (end) => ({ from: shift(end, -1), to: end }) },
  { label: '3M', range: (end) => ({ from: shift(end, -3), to: end }) },
  { label: '6M', range: (end) => ({ from: shift(end, -6), to: end }) },
  { label: '1Y', range: (end) => ({ from: shift(end, -12), to: end }) },
  { label: 'YTD', range: (end) => ({ from: `${end.slice(0, 4)}-01-01`, to: end }) },
]

type Props = {
  label: ReactNode
  value: Range
  onChange: (range: Range) => void
  /** First and last selectable day (server dates). */
  min?: string
  max?: string
  /** Where presets count back from; defaults to `max`. */
  presetEnd?: string
  help?: ReactNode
  error?: ReactNode
  disabled?: boolean
}

export function DateRange({ label, value, onChange, min, max, presetEnd, help, error, disabled }: Props) {
  const id = useId()
  const note = error ?? help
  const end = presetEnd ?? max
  const clamp = (range: Range): Range => ({
    from: min && range.from < min ? min : range.from,
    to: range.to,
  })
  const active = end ? PRESETS.find((p) => {
    const r = clamp(p.range(end))
    return r.from === value.from && r.to === value.to
  }) : undefined
  return (
    <fieldset className="daterange" disabled={disabled} aria-describedby={note ? `${id}-note` : undefined}>
      <legend className="daterange__legend">{label}</legend>
      <div className="daterange__row">
        <div className={`control daterange__control ${error ? 'control--invalid' : ''}`}>
          <input
            type="date"
            className="control__input num"
            aria-label="Start date"
            value={value.from}
            min={min}
            max={value.to || max}
            onChange={(event) => onChange({ ...value, from: event.target.value })}
          />
        </div>
        <span className="daterange__to faint" aria-hidden>
          to
        </span>
        <div className={`control daterange__control ${error ? 'control--invalid' : ''}`}>
          <input
            type="date"
            className="control__input num"
            aria-label="End date, not included"
            value={value.to}
            min={value.from || min}
            max={max}
            onChange={(event) => onChange({ ...value, to: event.target.value })}
          />
        </div>
        {end ? (
          <div className="daterange__presets" role="group" aria-label="Preset ranges">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                className={`daterange__preset ${active === preset ? 'daterange__preset--on' : ''}`}
                aria-pressed={active === preset}
                onClick={() => onChange(clamp(preset.range(end)))}
              >
                {preset.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {note ? (
        <p id={`${id}-note`} className={error ? 'field__error' : 'field__help'} role={error ? 'alert' : undefined}>
          {note}
        </p>
      ) : null}
    </fieldset>
  )
}
