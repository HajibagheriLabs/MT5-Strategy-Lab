import { formatNumber } from './format'
import './Figure.css'

type Props = {
  value: number | null | undefined
  decimals?: number
  /** Money takes the profit or loss colour and always shows its sign. */
  kind?: 'money' | 'number' | 'percent'
  unit?: string
  /** Show a + on positive numbers that are not money (differences). */
  signed?: boolean
  size?: 'md' | 'lg'
}

/** A number as StrategyLab shows numbers: monospace, fixed decimals, a real minus sign. A
 * missing value reads "n/a". */
export function Figure({ value, decimals = 2, kind = 'number', unit, signed, size = 'md' }: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className={`figure figure--${size} faint`}>n/a</span>
  }
  const showSign = kind === 'money' || signed
  const text = formatNumber(value, decimals, showSign) + (kind === 'percent' ? '%' : '')
  const tone = kind === 'money' && value !== 0 ? (value > 0 ? 'figure--profit' : 'figure--loss') : ''
  return (
    <span className={`figure figure--${size} num ${tone}`}>
      {text}
      {unit ? <span className="figure__unit">{unit}</span> : null}
    </span>
  )
}
