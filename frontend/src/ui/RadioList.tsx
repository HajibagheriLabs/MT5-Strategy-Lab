import { useId } from 'react'
import type { ReactNode } from 'react'
import './RadioList.css'

export type RadioOption<T extends string> = { value: T; label: ReactNode; description?: ReactNode; disabled?: boolean }

type Props<T extends string> = {
  label: ReactNode
  options: RadioOption<T>[]
  value: T
  onChange: (value: T) => void
}

/** Choices that each need a line of explanation: native radios, one per row. */
export function RadioList<T extends string>({ label, options, value, onChange }: Props<T>) {
  const name = useId()
  return (
    <fieldset className="radios">
      <legend className="radios__legend">{label}</legend>
      {options.map((option) => (
        <label key={option.value} className={`radios__option ${option.value === value ? 'radios__option--on' : ''}`}>
          <input
            type="radio"
            className="radios__input"
            name={name}
            value={option.value}
            checked={option.value === value}
            disabled={option.disabled}
            onChange={() => onChange(option.value)}
          />
          <span className="radios__dot" aria-hidden />
          <span className="radios__text">
            <span className="radios__label">{option.label}</span>
            {option.description ? <span className="radios__description">{option.description}</span> : null}
          </span>
        </label>
      ))}
    </fieldset>
  )
}
