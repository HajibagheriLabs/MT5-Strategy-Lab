import { FileArrowUp, Info, MagnifyingGlass, Play, Trash, WarningCircle } from '@phosphor-icons/react'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import type { Health, RunStatus } from '../api/types'
import { TerminalStatusView } from '../shell/TerminalStatus'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Combobox } from '../ui/Combobox'
import { EngineBadge } from '../ui/EngineBadge'
import { LogView } from '../ui/LogView'
import { RadioList } from '../ui/RadioList'
import { DateRange } from '../ui/DateRange'
import type { Range } from '../ui/DateRange'
import { Dialog } from '../ui/Dialog'
import { NumberInput, Select, TextInput } from '../ui/Field'
import { Figure } from '../ui/Figure'
import { EmptyState, Notice } from '../ui/Notice'
import { PageHeader, Section } from '../ui/PageHeader'
import { SegmentedControl } from '../ui/SegmentedControl'
import { Skeleton, SkeletonLines } from '../ui/Skeleton'
import { StatusBadge } from '../ui/StatusBadge'
import { Table } from '../ui/Table'
import type { Column, Sort } from '../ui/Table'
import { sortRows } from '../ui/sort'
import { Tabs } from '../ui/Tabs'
import { useToast } from '../ui/toast-context'
import './Kit.css'

type Theme = 'light' | 'dark'

const COLORS = [
  ['--bg', 'Page'],
  ['--surface', 'Surface'],
  ['--raised', 'Raised'],
  ['--sunken', 'Sunken'],
  ['--line', 'Line'],
  ['--line-strong', 'Line strong'],
  ['--control-border', 'Control border'],
  ['--fg', 'Text'],
  ['--fg-2', 'Text 2'],
  ['--fg-3', 'Text 3'],
  ['--accent', 'Accent'],
  ['--profit', 'Profit'],
  ['--loss', 'Loss'],
  ['--warning', 'Warning'],
  ['--danger', 'Danger'],
]

const TYPE = [
  ['--text-2xl', 'Headline figure', true],
  ['--text-xl', 'Page title', false],
  ['--text-lg', 'Section title', false],
  ['--text-md', 'Body and controls', false],
  ['--text-sm', 'Labels, dense tables', false],
  ['--text-xs', 'Units and hints', false],
] as const

type Sample = { id: string; strategy: string; symbol: string; period: string; trades: number; net: number; drawdown: number; status: RunStatus }

const RUNS: Sample[] = [
  { id: 'a', strategy: 'Moving Average', symbol: 'EURUSD@', period: '2025-01-01 to 2026-01-01', trades: 267, net: 989.59, drawdown: 684.68, status: 'done' },
  { id: 'b', strategy: 'ma_cross', symbol: 'EURUSD@', period: '2025-01-01 to 2025-07-01', trades: 102, net: 77.3, drawdown: 211.4, status: 'done' },
  { id: 'c', strategy: 'ParityMACross', symbol: 'USDJPY@', period: '2025-01-01 to 2025-04-01', trades: 56, net: -229.85, drawdown: 229.85, status: 'done' },
  { id: 'd', strategy: 'MACD Sample with a long name that has to be cut short somewhere', symbol: 'GBPUSD@', period: '2024-06-01 to 2025-06-01', trades: 0, net: 0, drawdown: 0, status: 'failed' },
  { id: 'e', strategy: 'Moving Average', symbol: 'USDJPY@', period: '2025-03-01 to 2025-04-01', trades: 0, net: 0, drawdown: 0, status: 'running' },
]

const COLUMNS: Column<Sample>[] = [
  { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} />, sortValue: (r) => r.status, width: '9rem' },
  { key: 'strategy', header: 'Strategy', sortValue: (r) => r.strategy, title: (r) => r.strategy },
  { key: 'symbol', header: 'Symbol', sortValue: (r) => r.symbol },
  { key: 'period', header: 'Period', render: (r) => <span className="num">{r.period}</span> },
  { key: 'trades', header: 'Trades', numeric: true, sortValue: (r) => r.trades, render: (r) => r.trades },
  { key: 'net', header: 'Net profit', numeric: true, sortValue: (r) => r.net, render: (r) => <Figure kind="money" value={r.net} /> },
  { key: 'drawdown', header: 'Max drawdown', numeric: true, sortValue: (r) => r.drawdown, render: (r) => <Figure value={r.drawdown} /> },
]

function health(overrides: Partial<Health> & { busy?: string | null; pids?: number[] }): Health {
  return {
    status: overrides.status ?? 'ok',
    message: overrides.message ?? null,
    terminal: {
      found: overrides.status !== 'unavailable',
      error: null,
      install_dir: 'C:\\MT5-Lab',
      data_dir: 'C:\\MT5-Lab',
      portable: true,
      server: overrides.status === 'unavailable' ? null : 'WMMarkets-Demo',
      source: 'local.toml',
      notes: [],
      running_pids: overrides.pids ?? [],
      busy_with_run: overrides.busy ?? null,
      reading_history: false,
    },
    queue: { current: overrides.busy ?? null, queued: overrides.busy ? 2 : 0 },
    version: '0.1.0',
  }
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kit-row">
      <span className="kit-row__label">{label}</span>
      <div className="kit-row__items">{children}</div>
    </div>
  )
}

function KitPanel({ theme }: { theme: Theme }) {
  const toast = useToast()
  const [sort, setSort] = useState<Sort | null>({ key: 'net', direction: 'desc' })
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set(['b']))
  const [model, setModel] = useState('ohlc_m1')
  const [tab, setTab] = useState('overview')
  const [range, setRange] = useState<Range>({ from: '2025-01-01', to: '2026-01-01' })
  const [dialog, setDialog] = useState(false)
  const [symbol, setSymbol] = useState('EURUSD@')
  const [radio, setRadio] = useState('ohlc_m1')

  return (
    <div className="kit-panel" data-theme={theme}>
      <h2 className="kit-panel__title">{theme === 'light' ? 'Light' : 'Dark'}</h2>

      <Section title="Colour">
        <div className="kit-swatches">
          {COLORS.map(([token, name]) => (
            <div key={token} className="kit-swatch">
              <span className="kit-swatch__chip" style={{ background: `var(${token})` }} />
              <span className="kit-swatch__name">{name}</span>
              <code className="kit-swatch__token faint">{token}</code>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Type">
        <div className="kit-type">
          {TYPE.map(([token, use, figure]) => (
            <div key={token} className="kit-type__row">
              <span style={{ fontSize: `var(${token})`, lineHeight: `var(${token}-line)` }} className={figure ? 'num' : ''}>
                {figure ? <Figure kind="money" value={12480.35} size="lg" /> : 'Net profit after swap'}
              </span>
              <span className="faint kit-type__note">
                {use} <code>{token}</code>
              </span>
            </div>
          ))}
          <div className="kit-type__row">
            <span className="num kit-type__figures">
              <span>1 234 567.89</span>
              <span>0.00012</span>
              <span>98.40%</span>
            </span>
            <span className="faint kit-type__note">Figures: Plex Mono, tabular</span>
          </div>
        </div>
      </Section>

      <Section title="Buttons">
        <Row label="Default">
          <Button variant="primary" icon={<Play size={16} />}>Start run</Button>
          <Button>Upload</Button>
          <Button variant="quiet">Clear filters</Button>
          <Button variant="danger" icon={<Trash size={16} />}>Delete run</Button>
        </Row>
        <Row label="Hover">
          <Button variant="primary" className="is-hover">Start run</Button>
          <Button className="is-hover">Upload</Button>
          <Button variant="quiet" className="is-hover">Clear filters</Button>
          <Button variant="danger" className="is-hover">Delete run</Button>
        </Row>
        <Row label="Focus">
          <Button variant="primary" className="is-focus">Start run</Button>
          <Button className="is-focus">Upload</Button>
          <Button variant="quiet" className="is-focus">Clear filters</Button>
        </Row>
        <Row label="Pressed">
          <Button variant="primary" className="is-active is-hover">Start run</Button>
          <Button className="is-active is-hover">Upload</Button>
        </Row>
        <Row label="Busy">
          <Button variant="primary" busy>Starting</Button>
          <Button busy>Uploading</Button>
        </Row>
        <Row label="Disabled">
          <Button variant="primary" disabled>Start run</Button>
          <Button disabled>Upload</Button>
          <Button variant="danger" disabled>Delete run</Button>
        </Row>
        <Row label="Small, icon">
          <Button size="sm" variant="primary">Rerun</Button>
          <Button size="sm">Cancel</Button>
          <Button size="sm" icon={<FileArrowUp size={14} />}>Report</Button>
          <Button size="sm" variant="quiet" icon={<MagnifyingGlass size={14} />} aria-label="Search" />
        </Row>
      </Section>

      <Section title="Inputs">
        <div className="kit-grid">
          <TextInput label="Search runs" placeholder="Strategy or symbol" />
          <TextInput label="Comment" defaultValue="parity check" help="Shown in the runs table." />
          <div className="force-hover">
            <TextInput label="Hover" defaultValue="EURUSD@" />
          </div>
          <div className="force-focus">
            <TextInput label="Focus" defaultValue="EURUSD@" />
          </div>
          <NumberInput label="Deposit" defaultValue="10000.00" suffix="USD" />
          <NumberInput label="Stop loss" defaultValue="-20" suffix="points" error="Needs a whole number of points, 1 or more." />
          <NumberInput label="Leverage" defaultValue="100" suffix=": 1" disabled />
          <Select
            label="Timeframe"
            defaultValue="H1"
            options={['M1', 'M5', 'M15', 'H1', 'H4', 'D1'].map((value) => ({ value, label: value }))}
          />
          <Select label="Strategy" placeholder="Choose a strategy" defaultValue="" options={[{ value: 'ma', label: 'Moving Average' }]} />
          <Select label="Currency" defaultValue="USD" options={[{ value: 'USD', label: 'USD' }]} disabled />
        </div>
        <div className="kit-stack">
          <DateRange label="Period" value={range} onChange={setRange} min="2000-07-05" max="2026-10-09" presetEnd="2026-10-09" help="History for EURUSD@ covers 2000-07-05 to 2026-10-09. The end date is not included." />
          <DateRange label="Period with an error" value={{ from: '1999-01-01', to: '2000-01-01' }} onChange={() => undefined} min="2000-07-05" max="2026-10-09" error="History for EURUSD@ starts on 2000-07-05; choose a later start." />
        </div>
      </Section>

      <Section title="Segmented control">
        <div className="kit-stack">
          <SegmentedControl
            label="Tick model"
            value={model}
            onChange={setModel}
            options={[
              { value: 'real_ticks', label: 'Real ticks' },
              { value: 'every_tick', label: 'Every tick' },
              { value: 'ohlc_m1', label: '1 minute OHLC' },
              { value: 'open_prices', label: 'Open prices', disabled: true },
            ]}
          />
          <SegmentedControl label="Disabled" value="a" onChange={() => undefined} disabled size="sm" options={[{ value: 'a', label: 'Bars' }, { value: 'b', label: 'Ticks' }]} />
        </div>
      </Section>

      <Section title="Choices">
        <Row label="Checkbox">
          <Checkbox label="Unchecked" />
          <Checkbox label="Checked" defaultChecked />
          <Checkbox label="Some" indeterminate />
          <Checkbox label="Disabled" disabled />
        </Row>
        <div className="kit-grid kit-stack">
          <Combobox
            label="Symbol"
            value={symbol}
            onChange={setSymbol}
            options={[
              { value: 'EURUSD@', label: 'EURUSD@', detail: '2000-07-05 to 2026-10-09', keywords: 'Euro vs US Dollar' },
              { value: 'GBPUSD@', label: 'GBPUSD@', detail: '2000-01-02 to 2026-10-09', keywords: 'Great British Pound' },
              { value: 'USDJPY@', label: 'USDJPY@', detail: '2000-01-07 to 2026-10-09', keywords: 'Japanese Yen' },
            ]}
            help="Type to narrow; only symbols with history are listed."
          />
          <Combobox label="Symbol with an error" value="" onChange={() => undefined} options={[]} error="Choose a symbol from the list." />
        </div>
        <div className="kit-stack">
          <RadioList
            label="Tick model"
            value={radio}
            onChange={setRadio}
            options={[
              { value: 'real_ticks', label: 'Real ticks', description: "The broker's recorded ticks: the closest to how orders would have filled." },
              { value: 'ohlc_m1', label: '1 minute OHLC', description: 'Four prices per minute: fast, and accurate for strategies that act on closed bars.' },
              { value: 'open_prices', label: 'Open prices', description: 'Not available here.', disabled: true },
            ]}
          />
        </div>
      </Section>

      <Section title="Tabs">
        <Tabs
          label="Result"
          value={tab}
          onChange={setTab}
          tabs={[
            { value: 'overview', label: 'Overview' },
            { value: 'deals', label: 'Deals', count: 534 },
            { value: 'metrics', label: 'Metrics' },
            { value: 'log', label: 'Log' },
            { value: 'report', label: 'MetaTrader report' },
          ]}
        >
          <p className="muted">The {tab} panel. Arrow keys move between tabs.</p>
        </Tabs>
      </Section>

      <Section title="Table">
        <div className="kit-stack">
          <div className="force-hover">
            <Table
              caption="Runs"
              columns={COLUMNS}
              rows={sortRows(RUNS, COLUMNS, sort)}
              rowKey={(r) => r.id}
              sort={sort}
              onSort={setSort}
              selectedKeys={selected}
              onRowSelect={(row) => setSelected(new Set([row.id]))}
            />
          </div>
          <p className="faint kit-note">Sorted by net profit; the first row shows hover, the selected row carries the accent rule. Long names are cut and shown in full on hover.</p>
          <Table caption="Compact" density="compact" columns={COLUMNS.slice(1, 6)} rows={RUNS.slice(0, 3)} rowKey={(r) => r.id} />
          <Table caption="Loading" columns={COLUMNS.slice(1, 6)} rows={[]} rowKey={(r: Sample) => r.id} loading />
          <Table
            caption="Empty"
            columns={COLUMNS.slice(1, 6)}
            rows={[]}
            rowKey={(r: Sample) => r.id}
            empty={<EmptyState title="No runs yet" action={<Button variant="primary" size="sm">New run</Button>}>Start a backtest and it appears here with its results.</EmptyState>}
          />
        </div>
      </Section>

      <Section title="Status">
        <Row label="Runs">
          {(['queued', 'compiling', 'running', 'parsing', 'done', 'failed', 'cancelled'] as RunStatus[]).map((status) => (
            <StatusBadge key={status} status={status} />
          ))}
        </Row>
        <Row label="Engine">
          <EngineBadge engine="mt5_tester" />
          <EngineBadge engine="python_sim" />
          <EngineBadge engine="mt5_tester" size="sm" />
          <EngineBadge engine="python_sim" size="sm" />
        </Row>
        <Row label="Terminal">
          <TerminalStatusView health={undefined} error={undefined} />
          <TerminalStatusView health={health({})} error={undefined} />
          <TerminalStatusView health={health({ busy: '20261009-132259-695451' })} error={undefined} />
          <TerminalStatusView health={health({ status: 'attention', pids: [4120], message: 'The terminal is already running from its data folder, so runs cannot start. Close it; StrategyLab starts and stops it itself.' })} error={undefined} />
          <TerminalStatusView health={health({ status: 'unavailable', message: 'No MetaTrader 5 terminal was found. Set [terminal] path in local.toml.' })} error={undefined} />
          <TerminalStatusView health={undefined} error={new ApiError(0, 'StrategyLab is not answering. Check that the server is running (python tasks.py dev).')} />
        </Row>
      </Section>

      <Section title="Figures">
        <Row label="Money">
          <Figure kind="money" value={989.59} />
          <Figure kind="money" value={-229.85} />
          <Figure kind="money" value={0} />
          <Figure kind="money" value={null} />
        </Row>
        <Row label="Other">
          <Figure value={1.08307} decimals={5} />
          <Figure kind="percent" value={5.87} />
          <Figure value={0.6} decimals={2} signed unit="points" />
          <Figure value={1.42} />
        </Row>
        <Row label="Headline">
          <Figure kind="money" value={989.59} size="lg" unit="USD" />
          <Figure kind="money" value={-229.85} size="lg" unit="USD" />
        </Row>
      </Section>

      <Section title="Messages">
        <div className="kit-stack">
          <Notice title="Simulated, not run in the Strategy Tester">
            Prices come from 1-minute bars. In the parity study this overstated results by 1 to 12 points per trade.
          </Notice>
          <Notice tone="warning" title="The terminal is already running" action={<Button size="sm">Check again</Button>}>
            Close the copy at C:\MT5-Lab; StrategyLab starts and stops it itself.
          </Notice>
          <Notice tone="danger" title="Moving Average did not compile">
            <p>Moving Average.mq5:133:5: error 256: &apos;BROKEN&apos; - undeclared identifier</p>
          </Notice>
          <EmptyState title="No strategies yet" action={<Button variant="primary" icon={<FileArrowUp size={16} />}>Upload a strategy</Button>}>
            Drop an .mq5, .ex5, .zip or .py file here, or choose one.
          </EmptyState>
        </div>
      </Section>

      <Section title="Dialog and toast">
        <Row label="Open">
          <Button onClick={() => setDialog(true)}>Open dialog</Button>
          <Button onClick={() => toast.push({ title: 'Run queued', message: 'Moving Average on EURUSD@ H1 is second in line.' })}>Info toast</Button>
          <Button onClick={() => toast.push({ tone: 'done', title: 'Run finished', message: '267 trades, net +989.59.' })}>Done toast</Button>
          <Button onClick={() => toast.push({ tone: 'error', title: 'Upload refused', message: "'notes.txt' is not a strategy StrategyLab can run." })}>Error toast</Button>
        </Row>
        <div className="kit-static">
          <div className="dialog kit-static__dialog">
            <div className="dialog__frame">
              <header className="dialog__header">
                <h3 className="dialog__title">Delete this run?</h3>
              </header>
              <div className="dialog__body">Its deals, report and logs are removed from this machine. The strategy stays.</div>
              <footer className="dialog__actions">
                <Button>Keep it</Button>
                <Button variant="danger">Delete run</Button>
              </footer>
            </div>
          </div>
          <ol className="toasts__list kit-static__toasts">
            <li className="toast toast--info">
              <span className="toast__icon">
                <Info size={16} aria-hidden />
              </span>
              <div className="toast__text">
                <p className="toast__title">Run queued</p>
                <p className="toast__message">Moving Average on EURUSD@ H1 is second in line.</p>
              </div>
            </li>
            <li className="toast toast--error">
              <span className="toast__icon">
                <WarningCircle size={16} aria-hidden />
              </span>
              <div className="toast__text">
                <p className="toast__title">Upload refused</p>
                <p className="toast__message">&apos;notes.txt&apos; is not a strategy StrategyLab can run.</p>
              </div>
            </li>
          </ol>
        </div>
        <Dialog
          open={dialog}
          onClose={() => setDialog(false)}
          title="Delete this run?"
          width="sm"
          actions={
            <>
              <Button onClick={() => setDialog(false)}>Keep it</Button>
              <Button variant="danger" onClick={() => setDialog(false)}>Delete run</Button>
            </>
          }
        >
          Its deals, report and logs are removed from this machine. The strategy stays.
        </Dialog>
      </Section>

      <Section title="Log">
        <LogView
          label="Sample log"
          height="9rem"
          lines={[
            { source: 'agent', line: 'CS\t0\t09:23:05.553\tTester\tinitial deposit 10000.00 USD, leverage 1:100' },
            { source: 'agent', line: 'CS\t2\t09:23:05.600\tTester\tnot enough money for the order, the order is skipped' },
            { source: 'agent', line: 'CS\t3\t09:23:05.700\tTester\tsymbol NOPE not exist' },
            { source: 'agent', line: 'CS\t0\t09:23:06.115\tTester\tfinal balance 10989.59 USD' },
          ]}
        />
      </Section>

      <Section title="Loading">
        <div className="kit-grid">
          <SkeletonLines lines={4} />
          <div className="kit-chart">
            <Skeleton variant="block" />
          </div>
        </div>
      </Section>
    </div>
  )
}

export function Kit() {
  const [view, setView] = useState<'both' | Theme>('both')
  const panels: Theme[] = view === 'both' ? ['light', 'dark'] : [view]
  return (
    <div className="kit">
      <PageHeader
        title="Component kit"
        description="Every component in every state, in both themes. Not part of the app's navigation."
        actions={
          <SegmentedControl
            label="Show"
            hideLabel
            size="sm"
            value={view}
            onChange={setView}
            options={[
              { value: 'both', label: 'Both' },
              { value: 'light', label: 'Light' },
              { value: 'dark', label: 'Dark' },
            ]}
          />
        }
      />
      <div className={`kit__panels kit__panels--${panels.length}`}>
        {panels.map((theme) => (
          <KitPanel key={theme} theme={theme} />
        ))}
      </div>
    </div>
  )
}
