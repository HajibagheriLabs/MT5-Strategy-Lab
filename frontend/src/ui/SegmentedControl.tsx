import { useId } from 'react'
import type { ReactNode } from 'react'
import './SegmentedControl.css'

export type Segment<T extends string> = {
  value: T
  label: ReactNode
  /** Accessible name when the label is only an icon. */
  ariaLabel?: string
  disabled?: boolean
}

type Props<T extends string> = {
  label: ReactNode
  hideLabel?: boolean
  options: Segment<T>[]
  value: T
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  disabled?: boolean
}

/** A radio group drawn as joined buttons, so arrow keys and screen readers behave as for any
 * radio group. */
export function SegmentedControl<T extends string>({
  label,
  hideLabel,
  options,
  value,
  onChange,
  size = 'md',
  disabled,
}: Props<T>) {
  const name = useId()
  return (
    <fieldset className={`segmented segmented--${size}`} disabled={disabled}>
      <legend className={hideLabel ? 'visually-hidden' : 'segmented__legend'}>{label}</legend>
      <div className="segmented__track">
        {options.map((option) => (
          <label
            key={option.value}
            className={`segmented__option ${option.value === value ? 'segmented__option--on' : ''}`}
            title={option.ariaLabel}
          >
            <input
              type="radio"
              className="segmented__radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              disabled={option.disabled}
              aria-label={option.ariaLabel}
              onChange={() => onChange(option.value)}
            />
            <span className="segmented__text">{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
