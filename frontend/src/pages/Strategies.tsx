import { FileArrowUp, Play, WarningCircle, XCircle } from '@phosphor-icons/react'
import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, ApiError } from '../api/client'
import { useResource } from '../api/hooks'
import type { Diagnostic, Run, Strategy, StrategyDetail } from '../api/types'
import { Button } from '../ui/Button'
import { EngineBadge } from '../ui/EngineBadge'
import { Figure } from '../ui/Figure'
import { formatDateTime } from '../ui/format'
import { EmptyState, Notice } from '../ui/Notice'
import { PageHeader, Section } from '../ui/PageHeader'
import { SkeletonLines } from '../ui/Skeleton'
import { StatusBadge } from '../ui/StatusBadge'
import { Table } from '../ui/Table'
import type { Column } from '../ui/Table'
import { useToast } from '../ui/toast-context'
import './Strategies.css'

const KIND: Record<Strategy['kind'], string> = {
  mq5: 'MQL5 source',
  ex5: 'Compiled expert',
  zip: 'MQL5 bundle',
  py: 'Python script',
}
const ACCEPTED = ['.mq5', '.ex5', '.zip', '.py']

function errors(strategy: Strategy) {
  return strategy.diagnostics.filter((d) => d.severity === 'error').length
}

function DiagnosticsList({ items }: { items: Diagnostic[] }) {
  return (
    <ol className="diagnostics">
      {items.map((d, index) => (
        <li key={index} className={`diagnostics__item diagnostics__item--${d.severity}`}>
          {d.severity === 'error' ? <XCircle size={14} aria-hidden /> : <WarningCircle size={14} aria-hidden />}
          <span className="diagnostics__where num">
            {d.file ?? ''}
            {d.line !== null ? `:${d.line}${d.column !== null ? `:${d.column}` : ''}` : ''}
          </span>
          <span className="diagnostics__code num">
            {d.severity}
            {d.code !== null ? ` ${d.code}` : ''}
          </span>
          <span className="diagnostics__message">{d.message}</span>
        </li>
      ))}
    </ol>
  )
}

function Dropzone({ onUploaded }: { onUploaded: (strategy: Strategy) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toast = useToast()

  const send = async (files: File[]) => {
    setError(null)
    const strategy = files.find((f) => ACCEPTED.some((s) => f.name.toLowerCase().endsWith(s)))
    const setFile = files.find((f) => f.name.toLowerCase().endsWith('.set'))
    if (!strategy) {
      const names = files.map((f) => f.name).join(', ')
      setError(`${names || 'That'} is not a strategy StrategyLab can run. Use an .mq5, .ex5, .zip or .py file.`)
      return
    }
    const form = new FormData()
    form.append('file', strategy)
    if (setFile && strategy.name.toLowerCase().endsWith('.ex5')) form.append('set_file', setFile)
    setBusy(strategy.name)
    try {
      const saved = await api.post<Strategy>('/strategies', form)
      const count = errors(saved)
      toast.push(
        count
          ? { tone: 'error', title: `${saved.name} has ${count} ${count === 1 ? 'error' : 'errors'}`, message: 'They are listed with the strategy.' }
          : { tone: 'done', title: `${saved.name} is ready`, message: `${saved.parameters.length} inputs found.` },
      )
      onUploaded(saved)
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : String(reason))
    } finally {
      setBusy(null)
    }
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    void send(Array.from(event.dataTransfer.files))
  }

  return (
    <div className="dropzone-block">
      <div
        className={`dropzone ${over ? 'dropzone--over' : ''} ${busy ? 'dropzone--busy' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <FileArrowUp size={20} aria-hidden className="dropzone__icon" />
        <div className="dropzone__text">
          <p className="dropzone__title">{busy ? `Checking ${busy}` : 'Drop a strategy here'}</p>
          <p className="dropzone__hint">
            {busy
              ? 'MQL5 is compiled with MetaEditor; Python is read without being run.'
              : 'MQL5 source (.mq5), a compiled expert (.ex5, with its .set file if you have one), a .zip of an expert and its includes, or a Python script (.py).'}
          </p>
        </div>
        <Button busy={Boolean(busy)} onClick={() => input.current?.click()}>
          Choose file
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          accept=".mq5,.ex5,.zip,.py,.set"
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden
          onChange={(event) => {
            void send(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
      </div>
      {error ? (
        <Notice tone="danger" title="The upload was refused">
          {error}
        </Notice>
      ) : null}
    </div>
  )
}

const RUN_COLUMNS: Column<Run>[] = [
  { key: 'status', header: 'Status', render: (r) => <StatusBadge status={r.status} />, width: '9rem' },
  { key: 'symbol', header: 'Market', render: (r) => `${r.settings.symbol} ${r.settings.timeframe}` },
  { key: 'period', header: 'Period', render: (r) => <span className="num">{`${r.settings.date_from} to ${r.settings.date_to}`}</span> },
  { key: 'trades', header: 'Trades', numeric: true, render: (r) => r.trade_count ?? '' },
  { key: 'net', header: 'Net profit', numeric: true, render: (r) => (r.status === 'done' ? <Figure kind="money" value={r.metrics.net_profit} /> : '') },
  { key: 'created', header: 'Started', render: (r) => <span className="num faint">{formatDateTime(r.created_at, false)}</span> },
]

function StrategyPanel({ hash }: { hash: string }) {
  const { data, error, loading } = useResource<StrategyDetail>(`/strategies/${encodeURIComponent(hash)}`)
  const navigate = useNavigate()
  if (loading) return <SkeletonLines lines={6} />
  if (error || !data) {
    return (
      <Notice tone="danger" title="This strategy could not be loaded">
        {error?.message}
      </Notice>
    )
  }
  const { strategy, runs } = data
  const count = errors(strategy)
  const groups = new Map<string, Strategy['parameters']>()
  for (const p of strategy.parameters) {
    const key = p.group ?? ''
    groups.set(key, [...(groups.get(key) ?? []), p])
  }
  return (
    <div className="strategy-panel">
      <header className="strategy-panel__head">
        <div className="strategy-panel__title">
          <h2 className="truncate" title={strategy.name}>
            {strategy.name}
          </h2>
          <div className="strategy-panel__meta">
            <EngineBadge engine={strategy.engine} size="sm" />
            <span className="faint">{KIND[strategy.kind]}</span>
            <span className="faint num">{strategy.hash}</span>
          </div>
        </div>
        <Button
          variant="primary"
          icon={<Play size={16} />}
          disabled={!strategy.ok}
          onClick={() => navigate(`/new?strategy=${strategy.hash}`)}
        >
          New run
        </Button>
      </header>

      {count ? (
        <Notice tone="danger" title={`${count} ${count === 1 ? 'error' : 'errors'}: this strategy cannot run`}>
          <p>Fix them in the source and upload it again; the corrected file gets its own entry.</p>
        </Notice>
      ) : null}
      {strategy.diagnostics.length ? <DiagnosticsList items={strategy.diagnostics} /> : null}
      {strategy.notes.length ? (
        <ul className="strategy-panel__notes">
          {strategy.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      <Section title={`Inputs (${strategy.parameters.length})`}>
        {strategy.parameters.length === 0 ? (
          <p className="muted">
            {strategy.parameter_source === 'none'
              ? 'A compiled expert without its source: its inputs cannot be read. Upload it again together with a .set file saved from the Strategy Tester to choose them.'
              : 'This strategy declares no inputs.'}
          </p>
        ) : (
          [...groups.entries()].map(([group, params]) => (
            <div key={group} className="strategy-panel__group">
              {group ? <h3 className="strategy-panel__group-title">{group}</h3> : null}
              <Table
                caption={`Inputs ${group}`}
                density="compact"
                rows={params}
                rowKey={(p) => p.name}
                columns={[
                  { key: 'name', header: 'Name', render: (p) => <span className="num">{p.name}</span> },
                  { key: 'label', header: 'Label', render: (p) => p.label ?? '', title: (p) => p.label ?? '' },
                  { key: 'kind', header: 'Type', render: (p) => <span className="faint">{p.type_name ?? p.kind}</span> },
                  {
                    key: 'default',
                    header: 'Default',
                    numeric: true,
                    render: (p) => p.options.find((o) => o.value === p.default)?.label ?? p.default,
                  },
                ]}
              />
            </div>
          ))
        )}
      </Section>

      <Section title={`Runs (${runs.length})`}>
        <Table
          caption="Runs of this strategy"
          density="compact"
          rows={runs}
          rowKey={(r) => r.id}
          columns={RUN_COLUMNS}
          onRowSelect={(r) => navigate(`/runs/${r.id}`)}
          maxHeight="22rem"
          empty={<p className="muted">No runs yet.</p>}
        />
      </Section>
    </div>
  )
}

export function Strategies() {
  const { hash } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useResource<Strategy[]>('/strategies')
  const strategies = data ?? []

  const columns: Column<Strategy>[] = [
    { key: 'name', header: 'Name', render: (s) => s.name, title: (s) => s.name, sortValue: (s) => s.name },
    { key: 'engine', header: 'Engine', render: (s) => <EngineBadge engine={s.engine} size="sm" /> },
    { key: 'inputs', header: 'Inputs', numeric: true, render: (s) => s.parameters.length },
    {
      key: 'state',
      header: 'State',
      render: (s) =>
        errors(s) ? <span className="strategy-state strategy-state--bad">{errors(s)} errors</span> : <span className="strategy-state">Ready</span>,
    },
    { key: 'uploaded', header: 'Uploaded', render: (s) => <span className="num faint">{formatDateTime(s.uploaded_at, false)}</span> },
  ]

  return (
    <div className="strategies">
      <PageHeader title="Strategies" description="Upload a strategy to see its inputs, then start runs from it." />
      <Dropzone
        onUploaded={(strategy) => {
          reload()
          navigate(`/strategies/${strategy.hash}`)
        }}
      />
      {error ? (
        <Notice tone="danger" title="Strategies could not be loaded" action={<Button size="sm" onClick={reload}>Try again</Button>}>
          {error.message}
        </Notice>
      ) : null}
      {!loading && !error && strategies.length === 0 ? (
        <div className="strategies__empty">
          <EmptyState title="No strategies yet">
            <p>
              Upload one above. To start with something that works, use the Moving Average example that
              ships with MetaTrader (MQL5\Experts\Examples\Moving Average), or <code>samples/python/ma_cross.py</code>.
            </p>
          </EmptyState>
        </div>
      ) : (
        <div className="strategies__grid">
          <div className="strategies__list">
            <Table
              caption="Strategies"
              rows={strategies}
              rowKey={(s) => s.hash}
              columns={columns}
              loading={loading}
              selectedKeys={new Set(hash ? [hash] : [])}
              onRowSelect={(s) => navigate(`/strategies/${s.hash}`)}
              maxHeight="calc(100dvh - 18rem)"
            />
          </div>
          <div className="strategies__detail">
            {hash ? (
              <StrategyPanel key={hash} hash={hash} />
            ) : (
              <EmptyState title="Choose a strategy">
                Its compile messages, inputs and runs appear here. <Link to="/new">Start a run</Link> from any strategy that is ready.
              </EmptyState>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
