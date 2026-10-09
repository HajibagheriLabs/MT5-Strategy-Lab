import type { RunSettings, TickModel } from '../api/types'
import { formatNumber } from '../ui/format'

export type MetricKind = 'money' | 'number' | 'count' | 'percent' | 'ratio'
export type MetricInfo = { key: string; label: string; kind: MetricKind; decimals?: number }

/** The headline figures of a result, in reading order. */
export const HEADLINE: MetricInfo[] = [
  { key: 'net_profit', label: 'Net profit', kind: 'money' },
  { key: 'profit_factor', label: 'Profit factor', kind: 'ratio' },
  { key: 'balance_dd_maximal', label: 'Max drawdown', kind: 'number' },
  { key: 'trades', label: 'Trades', kind: 'count' },
  { key: 'win_rate_pct', label: 'Won', kind: 'percent' },
  { key: 'sharpe_ratio', label: 'Sharpe', kind: 'ratio' },
  { key: 'recovery_factor', label: 'Recovery', kind: 'ratio' },
]

export const METRIC_GROUPS: { title: string; metrics: MetricInfo[] }[] = [
  {
    title: 'Result',
    metrics: [
      { key: 'net_profit', label: 'Net profit', kind: 'money' },
      { key: 'gross_profit', label: 'Gross profit', kind: 'money' },
      { key: 'gross_loss', label: 'Gross loss', kind: 'money' },
      { key: 'profit_factor', label: 'Profit factor', kind: 'ratio' },
      { key: 'expected_payoff', label: 'Expected payoff', kind: 'money' },
      { key: 'recovery_factor', label: 'Recovery factor', kind: 'ratio' },
      { key: 'sharpe_ratio', label: 'Sharpe ratio', kind: 'ratio' },
      { key: 'ahpr', label: 'AHPR', kind: 'ratio', decimals: 4 },
      { key: 'ghpr', label: 'GHPR', kind: 'ratio', decimals: 4 },
    ],
  },
  {
    title: 'Trades',
    metrics: [
      { key: 'trades', label: 'Trades', kind: 'count' },
      { key: 'total_deals', label: 'Deals', kind: 'count' },
      { key: 'wins', label: 'Winning trades', kind: 'count' },
      { key: 'losses', label: 'Losing trades', kind: 'count' },
      { key: 'win_rate_pct', label: 'Won', kind: 'percent' },
      { key: 'long_trades', label: 'Long trades', kind: 'count' },
      { key: 'long_win_rate_pct', label: 'Long trades won', kind: 'percent' },
      { key: 'short_trades', label: 'Short trades', kind: 'count' },
      { key: 'short_win_rate_pct', label: 'Short trades won', kind: 'percent' },
      { key: 'average_win', label: 'Average win', kind: 'money' },
      { key: 'average_loss', label: 'Average loss', kind: 'money' },
      { key: 'largest_win', label: 'Largest win', kind: 'money' },
      { key: 'largest_loss', label: 'Largest loss', kind: 'money' },
    ],
  },
  {
    title: 'Streaks',
    metrics: [
      { key: 'max_consecutive_wins', label: 'Longest winning streak', kind: 'count' },
      { key: 'max_consecutive_wins_money', label: 'Its result', kind: 'money' },
      { key: 'max_consecutive_losses', label: 'Longest losing streak', kind: 'count' },
      { key: 'max_consecutive_losses_money', label: 'Its result', kind: 'money' },
      { key: 'max_consecutive_profit', label: 'Best streak result', kind: 'money' },
      { key: 'max_consecutive_profit_count', label: 'Its trades', kind: 'count' },
      { key: 'max_consecutive_loss', label: 'Worst streak result', kind: 'money' },
      { key: 'max_consecutive_loss_count', label: 'Its trades', kind: 'count' },
      { key: 'average_consecutive_wins', label: 'Average winning streak', kind: 'number' },
      { key: 'average_consecutive_losses', label: 'Average losing streak', kind: 'number' },
    ],
  },
  {
    title: 'Drawdown',
    metrics: [
      { key: 'initial_deposit', label: 'Initial deposit', kind: 'number' },
      { key: 'balance_dd_absolute', label: 'Absolute drawdown', kind: 'number' },
      { key: 'balance_dd_maximal', label: 'Maximal drawdown', kind: 'number' },
      { key: 'balance_dd_maximal_pct', label: 'Maximal drawdown, % of peak', kind: 'percent' },
      { key: 'balance_dd_relative_pct', label: 'Relative drawdown', kind: 'percent' },
      { key: 'balance_dd_relative', label: 'Relative drawdown, money', kind: 'number' },
    ],
  },
]

export const ALL_METRICS: MetricInfo[] = METRIC_GROUPS.flatMap((group) => group.metrics)

export function formatMetric(info: MetricInfo, value: number | null | undefined): string {
  if (value === null || value === undefined) return 'n/a'
  if (info.kind === 'count') return formatNumber(value, 0)
  if (info.kind === 'percent') return `${formatNumber(value, 2)}%`
  if (info.kind === 'ratio') return formatNumber(value, info.decimals ?? 2)
  return formatNumber(value, 2, info.kind === 'money')
}

export const MODEL_LABEL: Record<TickModel, string> = {
  real_ticks: 'Real ticks',
  every_tick: 'Every tick',
  ohlc_m1: '1 minute OHLC',
  open_prices: 'Open prices',
}

export const MODEL_HELP: Record<TickModel, string> = {
  real_ticks: "The broker's recorded ticks: the closest to how orders would have filled. Slowest.",
  every_tick: 'Ticks generated from 1-minute bars: real bar extremes, invented path between them.',
  ohlc_m1: 'Four prices per minute: fast, and accurate for strategies that act on closed bars.',
  open_prices: 'Only the open of each bar: fastest, valid only for strategies that trade once per bar without stops.',
}

export const PYTHON_MODEL_HELP: Partial<Record<TickModel, string>> = {
  ohlc_m1: "1-minute bars, generated as in the tester's 1 minute OHLC mode. Fast.",
  real_ticks: "The broker's recorded ticks, as the tester's real-tick mode uses them. Large exports, slower.",
}

/** A simulated result's note without its first sentence, for places whose title already says it. */
export function fidelityBody(text: string): string {
  return text.replace(/^Simulated by StrategyLab, not the Strategy Tester[^.]*\.\s*/, '')
}

export function describeSettings(s: RunSettings): string {
  return `${s.symbol} ${s.timeframe}, ${s.date_from} to ${s.date_to}, ${MODEL_LABEL[s.model]}`
}

/** What to do next, for each way a run can end badly. The engine's own message comes first. */
export const NEXT_STEP: Record<string, string> = {
  compile_failed: 'Fix the errors listed on the strategy, then upload it again.',
  terminal_running:
    'Close the dedicated MetaTrader terminal and run again; StrategyLab starts and stops it itself.',
  no_report: 'The journal in the log says why. Check the symbol, the period and the inputs.',
  timeout: 'Use a shorter period or a faster tick model, or raise the time limit.',
  zero_trades: 'Check the inputs, the symbol and the period: the strategy never opened a position.',
  no_history: 'Open a chart of the symbol in the dedicated terminal to download its history, or choose dates inside what is there.',
  interrupted: 'The server stopped while this run was in progress. Run it again.',
  error: 'The log shows where the strategy stopped.',
  invalid_settings: 'Change the settings and run again.',
  cancelled: 'Run it again whenever you like.',
}
