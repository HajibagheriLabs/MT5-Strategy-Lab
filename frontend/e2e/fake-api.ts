import type { Page, Route } from '@playwright/test'

/* A stand-in for the StrategyLab API with just enough state for the browser tests: uploads,
   one symbol with history, runs that go through every stage when their event stream is read. */

const T0 = '2025-01-02T10:00:00'
const HASH_EA = 'ea0001'
const HASH_PY = 'py0001'

type Json = Record<string, unknown>

function strategy(hash: string, name: string, engine: 'mt5_tester' | 'python_sim', extra: Json = {}): Json {
  return {
    hash,
    name,
    kind: engine === 'mt5_tester' ? 'mq5' : 'py',
    engine,
    folder: `C:\\lab\\${hash}`,
    entry: name,
    parameters:
      engine === 'mt5_tester'
        ? [
            { name: 'Lots', kind: 'real', default: '0.1', label: 'Lot size', group: null, options: [], source: 'input', type_name: 'double' },
            { name: 'Period', kind: 'integer', default: '12', label: 'Moving average period', group: null, options: [], source: 'input', type_name: 'int' },
          ]
        : [{ name: 'FAST', kind: 'integer', default: '10', label: 'fast period', group: 'Constants', options: [], source: 'constant', type_name: null }],
    parameter_source: engine === 'mt5_tester' ? 'source' : 'script',
    ok: true,
    diagnostics: [],
    notes: [],
    uploaded_at: '2026-10-09T10:00:00+00:00',
    ...extra,
  }
}

function deal(ticket: number, minutes: number, type: string, entry: string | null, price: number | null, profit: number, balance: number): Json {
  const when = new Date(Date.parse(`${T0}Z`) + minutes * 60_000).toISOString().slice(0, 19)
  return {
    ticket,
    server_time: when,
    time: `${when}+00:00`,
    symbol: type === 'balance' ? null : 'EURUSD@',
    type,
    entry,
    volume: type === 'balance' ? null : 0.1,
    price,
    order: ticket,
    commission: 0,
    swap: 0,
    profit,
    balance,
    comment: entry === 'out' ? 'tp 1.10500' : '',
  }
}

const DEALS = [
  deal(1, 0, 'balance', null, null, 10_000, 10_000),
  deal(2, 60, 'buy', 'in', 1.1, 0, 10_000),
  deal(3, 180, 'sell', 'out', 1.105, 50, 10_050),
  deal(4, 300, 'sell', 'in', 1.104, 0, 10_050),
  deal(5, 420, 'buy', 'out', 1.1064, -24, 10_026),
]

const METRICS = {
  net_profit: 989.59,
  profit_factor: 1.28,
  balance_dd_maximal: 684.68,
  balance_dd_maximal_pct: 5.87,
  trades: 267,
  win_rate_pct: 28.09,
  sharpe_ratio: 0.93,
  recovery_factor: 1.45,
  gross_profit: 4471.61,
  gross_loss: -3482.02,
}

const REPORT = `<!DOCTYPE html><html><head><title>Strategy Tester Report</title></head><body><h1>Strategy Tester Report</h1><p>Total Net Profit 989.59</p><script>document.body.append('scripts ran')</script></body></html>`

export class FakeApi {
  strategies: Json[] = [strategy(HASH_PY, 'ma_cross.py', 'python_sim')]
  runs: Json[] = []
  private next = 1

  async install(page: Page) {
    // Only the API: the app's own modules live under /src/api/ and must load as they are.
    await page.route((url) => url.pathname.startsWith('/api/'), (route) => this.handle(route))
  }

  private json(route: Route, body: unknown, status = 200) {
    return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  }

  private find(id: string) {
    return this.runs.find((r) => r.id === id)
  }

  private async handle(route: Route) {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (path === '/health') {
      return this.json(route, {
        status: 'ok',
        message: null,
        terminal: { found: true, error: null, install_dir: 'C:\\MT5-Lab', data_dir: 'C:\\MT5-Lab', portable: true, server: 'Demo-Server', source: 'local.toml', notes: [], running_pids: [], busy_with_run: null, reading_history: false },
        queue: { current: null, queued: 0 },
        version: '0.1.0',
      })
    }
    if (path === '/events') {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': nothing yet\n\n' })
    }
    if (path === '/symbols') {
      return this.json(route, {
        server: 'Demo-Server',
        symbols: [{ name: 'EURUSD@', description: 'Euro vs US Dollar', digits: 5, path: 'FX\\EURUSD@', bars_from: '2024-01-02', bars_to: '2025-12-31', history_years: [2024, 2025], ticks_from: '2025-03-01', tick_months: 10, measured: true }],
        pending: [],
        message: null,
      })
    }
    if (path === '/fidelity') {
      return this.json(route, {
        mt5_tester: { ohlc_m1: 'MetaTrader 5 Strategy Tester on 1-minute OHLC.', real_ticks: 'MetaTrader 5 Strategy Tester on real ticks.', every_tick: 'Every tick.', open_prices: 'Open prices.' },
        python_sim: { ohlc_m1: 'Simulated by StrategyLab, not the Strategy Tester. Prices come from 1-minute bars.', real_ticks: 'Simulated by StrategyLab, not the Strategy Tester, on recorded ticks.' },
      })
    }
    if (path === '/metrics') {
      return this.json(route, {
        definitions: { net_profit: 'Sum of profit, swap and commission.' },
        metatrader: { net_profit: ['Total Net Profit', 0], profit_factor: ['Profit Factor', 0] },
      })
    }
    if (path === '/strategies' && method === 'GET') return this.json(route, this.strategies)
    if (path === '/strategies' && method === 'POST') {
      const body = request.postDataBuffer()?.toString('latin1') ?? ''
      const name = /filename="([^"]+)"/.exec(body)?.[1] ?? ''
      if (!/\.(mq5|ex5|zip|py)$/i.test(name)) {
        return this.json(route, { detail: `'${name}' is not a strategy StrategyLab can run. Upload an MQL5 source file (.mq5), a compiled expert (.ex5), a .zip with an expert and its include files, or a Python script (.py).` }, 415)
      }
      const broken = body.includes('BROKEN')
      const saved = strategy(broken ? 'bad001' : HASH_EA, name, 'mt5_tester', broken
        ? { ok: false, diagnostics: [{ severity: 'error', message: "'BROKEN' - undeclared identifier", code: 256, file: name, line: 12, column: 5 }] }
        : {})
      this.strategies = [saved, ...this.strategies.filter((s) => s.hash !== saved.hash)]
      return this.json(route, saved, 201)
    }
    const strategyMatch = /^\/strategies\/([^/]+)$/.exec(path)
    if (strategyMatch) {
      const found = this.strategies.find((s) => s.hash === strategyMatch[1])
      if (!found) return this.json(route, { detail: 'No strategy.' }, 404)
      return this.json(route, { strategy: found, runs: this.runs.filter((r) => r.strategy_hash === found.hash) })
    }
    if (path === '/runs' && method === 'GET') return this.json(route, [...this.runs].reverse())
    if (path === '/runs' && method === 'POST') {
      const body = request.postDataJSON() as Json
      if (String(body.date_from) < '2024-01-02') {
        return this.json(route, { detail: 'History for EURUSD@ covers 2024-01-02 to 2025-12-31; choose dates within it (the end date is exclusive, so at most 2026-01-01).' }, 422)
      }
      const chosen = this.strategies.find((s) => s.hash === body.strategy_hash)!
      const run: Json = {
        id: `run-${this.next++}`,
        strategy_hash: chosen.hash,
        strategy_name: String(chosen.name).replace(/\.\w+$/, ''),
        engine: chosen.engine,
        status: 'queued',
        settings: { ...body, commission_per_lot: body.commission_per_lot ?? 0, timeout_s: null },
        created_at: '2026-10-09T10:00:00+00:00',
        started_at: null,
        finished_at: null,
        stage_seconds: {},
        outcome: null,
        message: null,
        error: null,
        metrics: {},
        fidelity: null,
        trade_count: null,
        rerun_of: null,
        artefacts: {},
      }
      this.runs.push(run)
      return this.json(route, run, 201)
    }
    const runMatch = /^\/runs\/([^/]+)(\/.*)?$/.exec(path)
    if (runMatch) {
      const run = this.find(runMatch[1])
      if (!run) return this.json(route, { detail: `No run ${runMatch[1]}.` }, 404)
      const rest = runMatch[2] ?? ''
      if (rest === '') return this.json(route, { run, strategy: this.strategies.find((s) => s.hash === run.strategy_hash), queue_position: null })
      if (rest === '/events') return this.events(route, run)
      if (rest === '/result') {
        return this.json(route, {
          meta: { engine: run.engine, fidelity: run.fidelity, strategy_name: run.strategy_name, server: 'Demo-Server', terminal_build: 6249, server_utc_offsets_h: [2], notes: [], parameters: {} },
          metrics: METRICS,
          reported: { 'Total Net Profit': '989.59', 'Profit Factor': '1.28' },
          orders: 4,
          deals: DEALS.length,
        })
      }
      if (rest === '/deals') return this.json(route, { deals: DEALS, total: DEALS.length })
      if (rest.startsWith('/equity')) {
        return this.json(route, {
          points: DEALS.filter((d) => d.type === 'balance' || d.entry === 'out').map((d, i) => ({
            time: d.time, server_time: d.server_time, balance: d.balance, equity: null, drawdown: i === 2 ? 24 : 0, drawdown_pct: i === 2 ? 0.24 : 0,
          })),
          total: 3,
          downsampled: false,
        })
      }
      if (rest.startsWith('/bars')) {
        const start = Date.parse(`${T0}Z`) / 1000
        const bars = Array.from({ length: 48 }, (_, i) => ({ time: start + i * 3600 - 7200, open: 1.1 + i * 0.0002, high: 1.1012 + i * 0.0002, low: 1.0992 + i * 0.0002, close: 1.1005 + i * 0.0002 }))
        return this.json(route, { timeframe: 'H1', bars, first_index: 0, total: bars.length })
      }
      if (rest === '/logs') return this.json(route, [{ source: 'agent', size: 120 }])
      if (rest === '/logs/agent') return route.fulfill({ status: 200, contentType: 'text/plain', body: 'CS\t0\t10:00:00.000\tTester\tfinal balance 10989.59 USD\n' })
      if (rest === '/report/') {
        return route.fulfill({
          status: 200,
          contentType: 'text/html',
          headers: { 'Content-Security-Policy': "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'" },
          body: REPORT,
        })
      }
    }
    return this.json(route, { detail: `The fake API has no ${method} ${path}.` }, 404)
  }

  /** The stream a real run would send, all at once; the run is done when it has been read. */
  private events(route: Route, run: Json) {
    const lines: string[] = []
    let seq = 1
    const send = (event: string, data: Json) => lines.push(`id: ${seq++}\nevent: ${event}\ndata: ${JSON.stringify({ run_id: run.id, ...data })}\n\n`)
    send('state', { id: run.id, status: 'compiling' })
    send('log', { source: 'compile', line: '0 errors, 0 warnings' })
    send('state', { id: run.id, status: 'running' })
    send('log', { source: 'agent', line: 'CS\t0\t10:00:01.000\tTester\ttesting started' })
    send('state', { id: run.id, status: 'parsing' })
    Object.assign(run, {
      status: 'done',
      outcome: 'success',
      message: '267 trades.',
      metrics: METRICS,
      trade_count: 267,
      fidelity: run.engine === 'mt5_tester' ? 'MetaTrader 5 Strategy Tester on 1-minute OHLC.' : 'Simulated by StrategyLab, not the Strategy Tester. Prices come from 1-minute bars.',
      finished_at: '2026-10-09T10:01:00+00:00',
      stage_seconds: { compiling: 0.5, running: 9.2, parsing: 0.4 },
      artefacts: run.engine === 'mt5_tester' ? { report: 'report.htm', log_agent: 'agent.log' } : { log_strategy: 'strategy.log' },
    })
    send('state', { id: run.id, status: 'done', outcome: 'success' })
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body: lines.join('') })
  }
}
