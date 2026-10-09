import { Flask, Gauge } from '@phosphor-icons/react'
import type { Engine } from '../api/types'
import './EngineBadge.css'

/** Which engine produced a result. A simulated result is never shown without this. */
export function EngineBadge({ engine, size = 'md' }: { engine: Engine; size?: 'sm' | 'md' }) {
  const tester = engine === 'mt5_tester'
  return (
    <span
      className={`engine engine--${tester ? 'tester' : 'sim'} engine--${size}`}
      title={tester ? 'Run in the MetaTrader 5 Strategy Tester' : 'Simulated by StrategyLab, not the Strategy Tester'}
    >
      {tester ? <Gauge size={size === 'sm' ? 12 : 14} aria-hidden /> : <Flask size={size === 'sm' ? 12 : 14} aria-hidden />}
      <span>{tester ? 'Strategy Tester' : 'Python simulator'}</span>
    </span>
  )
}
