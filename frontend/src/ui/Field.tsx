import { CaretDown, WarningCircle } from '@phosphor-icons/react'
import { useId } from 'react'
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import './Field.css'

type FieldProps = {
  label: ReactNode
  help?: ReactNode
  /** Replaces the help text and marks the control invalid. */
  error?: ReactNode
  /** Visually hide the label (it is still read out). */
  hideLabel?: boolean
  children: (control: { id: string; describedBy?: string; invalid: boolean }) => ReactNode
  className?: string
}

export function Field({ label, help, error, hideLabel, children, className }: FieldProps) {
  const id = useId()
  const noteId = `${id}-note`
  const note = error ?? help
  return (
    <div className={`field ${className ?? ''}`}>
      <label htmlFor={id} className={hideLabel ? 'visually-hidden' : 'field__label'}>
        {label}
      </label>
      {children({ id, describedBy: note ? noteId : undefined, invalid: Boolean(error) })}
      {note ? (
        <p id={noteId} className={error ? 'field__error' : 'field__help'} role={error ? 'alert' : undefined}>
          {error ? <WarningCircle size={14} aria-hidden /> : null}
          <span>{note}</span>
        </p>
      ) : null}
    </div>
  )
}

type InputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> & {
  label: ReactNode
  help?: ReactNode
  error?: ReactNode
  hideLabel?: boolean
  /** A unit shown inside the control after the value (USD, points, lots). */
  suffix?: ReactNode
  numeric?: boolean
}

export function TextInput({ label, help, error, hideLabel, suffix, numeric, className, ...rest }: InputProps) {
  return (
    <Field label={label} help={help} error={error} hideLabel={hideLabel} className={className}>
      {({ id, describedBy, invalid }) => (
        <div className={`control ${invalid ? 'control--invalid' : ''} ${rest.disabled ? 'control--disabled' : ''}`}>
          <input
            id={id}
            className={`control__input ${numeric ? 'num control__input--numeric' : ''}`}
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            {...rest}
          />
          {suffix ? <span className="control__suffix">{suffix}</span> : null}
        </div>
      )}
    </Field>
  )
}

export function NumberInput(props: InputProps) {
  return <TextInput inputMode="decimal" autoComplete="off" spellCheck={false} numeric {...props} />
}

export type Option = { value: string; label: string; disabled?: boolean }

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label: ReactNode
  help?: ReactNode
  error?: ReactNode
  hideLabel?: boolean
  options: Option[]
  placeholder?: string
}

export function Select({ label, help, error, hideLabel, options, placeholder, className, ...rest }: SelectProps) {
  return (
    <Field label={label} help={help} error={error} hideLabel={hideLabel} className={className}>
      {({ id, describedBy, invalid }) => (
        <div className={`control control--select ${invalid ? 'control--invalid' : ''} ${rest.disabled ? 'control--disabled' : ''}`}>
          <select
            id={id}
            className="control__input"
            aria-describedby={describedBy}
            aria-invalid={invalid || undefined}
            {...rest}
          >
            {placeholder ? (
              <option value="" disabled>
                {placeholder}
              </option>
            ) : null}
            {options.map((option) => (
              <option key={option.value} value={option.value} disabled={option.disabled}>
                {option.label}
              </option>
            ))}
          </select>
          <CaretDown className="control__caret" size={14} aria-hidden />
        </div>
      )}
    </Field>
  )
}
