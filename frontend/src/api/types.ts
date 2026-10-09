/* The API's shapes, as backend/strategylab/api.py and store.py define them. */

export type Engine = 'mt5_tester' | 'python_sim'
export type TickModel = 'every_tick' | 'ohlc_m1' | 'open_prices' | 'real_ticks'
export type RunStatus = 'queued' | 'compiling' | 'running' | 'parsing' | 'done' | 'failed' | 'cancelled'
export type StrategyKind = 'mq5' | 'ex5' | 'zip' | 'py'

export const FINISHED: ReadonlySet<RunStatus> = new Set(['done', 'failed', 'cancelled'])

export type TerminalStatus = {
  found: boolean
  error: string | null
  install_dir: string | null
  data_dir: string | null
  portable: boolean | null
  server: string | null
  source: string | null
  notes: string[]
  running_pids: number[]
  busy_with_run: string | null
  reading_history: boolean
}

export type Health = {
  status: 'ok' | 'attention' | 'unavailable'
  message: string | null
  terminal: TerminalStatus
  queue: { current: string | null; queued: number }
  version: string
}

export type Diagnostic = {
  severity: 'error' | 'warning'
  message: string
  code: number | null
  file: string | null
  line: number | null
  column: number | null
}

export type ParameterKind = 'bool' | 'integer' | 'real' | 'string' | 'datetime' | 'color' | 'enum' | 'unknown'

export type Parameter = {
  name: string
  kind: ParameterKind
  default: string
  label: string | null
  group: string | null
  options: { value: string; label: string }[]
  source: 'input' | 'sinput' | 'set_file' | 'constant' | 'option'
  type_name: string | null
}

export type Strategy = {
  hash: string
  name: string
  kind: StrategyKind
  engine: Engine
  folder: string
  entry: string
  parameters: Parameter[]
  parameter_source: 'source' | 'set_file' | 'none' | 'script'
  ok: boolean
  diagnostics: Diagnostic[]
  notes: string[]
  uploaded_at: string
}

export type RunSettings = {
  symbol: string
  timeframe: string
  date_from: string
  date_to: string
  model: TickModel
  deposit: number
  currency: string
  leverage: number
  parameters: Record<string, string>
  commission_per_lot: number
  timeout_s: number | null
}

export type Metrics = Record<string, number | null>

export type Run = {
  id: string
  strategy_hash: string
  strategy_name: string
  engine: Engine
  status: RunStatus
  settings: RunSettings
  created_at: string
  started_at: string | null
  finished_at: string | null
  stage_seconds: Record<string, number>
  outcome: string | null
  message: string | null
  error: string | null
  metrics: Metrics
  fidelity: string | null
  trade_count: number | null
  rerun_of: string | null
  artefacts: Record<string, string>
}

export type RunDetail = { run: Run; strategy: Strategy | null; queue_position: number | null }
export type StrategyDetail = { strategy: Strategy; runs: Run[] }

export type SymbolInfo = {
  name: string
  description: string | null
  digits: number | null
  path: string | null
  bars_from: string | null
  bars_to: string | null
  history_years: number[]
  ticks_from: string | null
  tick_months: number
  measured: boolean
}

export type Symbols = { server: string | null; symbols: SymbolInfo[]; pending: string[]; message: string | null }

export type RunCreate = {
  strategy_hash: string
  symbol: string
  timeframe: string
  date_from: string
  date_to: string
  model: TickModel
  deposit: number
  currency: string
  leverage: number
  parameters: Record<string, string>
  commission_per_lot?: number
  timeout_s?: number | null
}

export type Deal = {
  ticket: number
  server_time: string
  time: string | null
  symbol: string | null
  type: string
  entry: string | null
  volume: number | null
  price: number | null
  order: number | null
  commission: number
  swap: number
  profit: number
  balance: number | null
  comment: string
}

export type SeriesPoint = {
  time: string | null
  server_time: string
  balance: number
  equity: number | null
  drawdown: number
  drawdown_pct: number
}

export type Series = { points: SeriesPoint[]; total: number; downsampled: boolean }

export type ResultSummary = {
  meta: {
    engine: Engine
    fidelity: string
    strategy_name: string
    symbol: string
    timeframe: string
    date_from: string
    date_to: string
    model: TickModel
    deposit: number
    currency: string
    leverage: number
    parameters: Record<string, string>
    server: string | null
    company: string | null
    terminal_build: number | null
    server_utc_offsets_h: number[]
    notes: string[]
  }
  metrics: Metrics
  reported: Record<string, string>
  orders: number
  deals: number
}

export type LogSource = { source: string; size: number }

export type RunEvent =
  | { kind: 'state'; seq: number | null; run_id: string; status: RunStatus | 'deleted'; outcome?: string | null; message?: string | null; snapshot?: boolean }
  | { kind: 'log'; seq: number | null; run_id: string; source: string; line: string }
