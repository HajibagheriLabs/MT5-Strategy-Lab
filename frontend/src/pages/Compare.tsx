import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, ApiError } from '../api/client'
import { useResource } from '../api/hooks'
import type { Engine, Run, RunDetail, Series, TickModel } from '../api/types'
import { CompareChart } from '../charts/CompareChart'
import { lineStyle } from '../charts/lineStyle'
import { ALL_METRICS, fidelityBody, formatMetric, HEADLINE, MODEL_LABEL } from '../runs/catalog'
import type { MetricInfo } from '../runs/catalog'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { EngineBadge } from '../ui/EngineBadge'
import { Figure } from '../ui/Figure'
import { formatNumber } from '../ui/format'
import { EmptyState, Notice } from '../ui/Notice'
import { PageHeader, Section } from '../ui/PageHeader'
import { Skeleton, SkeletonLines } from '../ui/Skeleton'
import { Table } from '../ui/Table'
import './Compare.css'

const LETTERS = ['A', 'B', 'C', 'D']

type Loaded = { detail: RunDetail; series: Series }

function useCompared(ids: string[]) {
  const key = ids.join(',')
  const [state, setState] = useState<{ key: string; runs?: Loaded[]; error?: string }>({ key: '' })
  useEffect(() => {
    if (!ids.length) return
    let live = true
    Promise.all(
      ids.map(async (id) => {
        const path = `/runs/${encodeURIComponent(id)}`
        const [detail, series] = await Promise.all([api.get<RunDetail>(path), api.get<Series>(`${path}/equity?max_points=1500`)])
        return { detail, series }
      }),
    )
      .then((runs) => {
        if (live) setState({ key, runs })
      })
      .catch((reason: unknown) => {
        if (live) setState({ key, error: reason instanceof ApiError ? reason.message : String(reason) })
      })
    return () => {
      live = false
    }
    // ids is derived from key; the key is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return state.key === key ? state : { key }
}

function Picker() {
  const navigate = useNavigate()
  const { data, error, loading } = useResource<Run[]>('/runs?status=done&limit=2000')
  const [chosen, setChosen] = useState<string[]>([])
  const toggle = (id: string) =>
    setChosen((current) => (current.includes(id) ? current.filter((x) => x !== id) : current.length < 4 ? [...current, id] : current))
  return (
    <div className="compare">
      <PageHeader
        title="Compare"
        description="Choose two to four finished runs. The first one you choose is the reference the others are measured against."
        actions={
          <Button variant="primary" disabled={chosen.length < 2} onClick={() => navigate(`/compare?runs=${chosen.join(',')}`)}>
            Compare {chosen.length ? chosen.length : ''}
          </Button>
        }
      />
      {error ? (
        <Notice tone="danger" title="Runs could not be loaded">{error.message}</Notice>
      ) : (
        <Table
          caption="Finished runs"
          loading={loading}
          rows={data ?? []}
          rowKey={(r) => r.id}
          selectedKeys={new Set(chosen)}
          onRowSelect={(r) => toggle(r.id)}
          maxHeight="calc(100dvh - 14rem)"
          empty={<EmptyState title="No finished runs yet" action={<Link to="/new">Start a run</Link>}>Runs appear here once they are done.</EmptyState>}
          columns={[
            {
              key: 'pick',
              header: '',
              width: '3rem',
              render: (r) => (
                <span className="compare__pick num">
                  {chosen.includes(r.id) ? LETTERS[chosen.indexOf(r.id)] : <Checkbox label={`Choose run ${r.id}`} hideLabel checked={false} readOnly tabIndex={-1} />}
                </span>
              ),
            },
            { key: 'engine', header: 'Engine', render: (r) => <EngineBadge engine={r.engine} size="sm" /> },
            { key: 'strategy', header: 'Strategy', render: (r) => r.strategy_name, title: (r) => r.strategy_name },
            { key: 'market', header: 'Market', render: (r) => <span className="num">{`${r.settings.symbol} ${r.settings.timeframe}`}</span> },
            { key: 'period', header: 'Period', render: (r) => <span className="num">{`${r.settings.date_from} to ${r.settings.date_to}`}</span> },
            { key: 'net', header: 'Net profit', numeric: true, render: (r) => <Figure kind="money" value={r.metrics.net_profit} /> },
            { key: 'trades', header: 'Trades', numeric: true, render: (r) => r.trade_count ?? '' },
          ]}
        />
      )}
    </div>
  )
}

function Delta({ info, value, reference }: { info: MetricInfo; value: number | null | undefined; reference: number | null | undefined }) {
  if (value === null || value === undefined || reference === null || reference === undefined || value === reference) return null
  const difference = value - reference
  const decimals = info.kind === 'count' ? 0 : info.decimals ?? 2
  return <span className="compare__delta num">{formatNumber(difference, decimals, true)}{info.kind === 'percent' ? ' pts' : ''}</span>
}

export function Compare() {
  const [query] = useSearchParams()
  const ids = useMemo(() => (query.get('runs') ?? '').split(',').filter(Boolean).slice(0, 4), [query])
  const state = useCompared(ids)
  const fidelity = useResource<Record<Engine, Partial<Record<TickModel, string>>>>('/fidelity')

  if (ids.length < 2) return <Picker />
  if (state.error) {
    return (
      <div className="compare">
        <PageHeader title="Compare" />
        <Notice tone="danger" title="These runs could not be loaded" action={<Link to="/compare">Choose runs</Link>}>
          {state.error}
        </Notice>
      </div>
    )
  }
  if (!state.runs) {
    return (
      <div className="compare">
        <PageHeader title="Compare" />
        <SkeletonLines lines={4} />
        <div style={{ height: 340 }}>
          <Skeleton variant="block" />
        </div>
      </div>
    )
  }

  const runs = state.runs
  const notDone = runs.filter((r) => r.detail.run.status !== 'done')
  const engines = new Set(runs.map((r) => r.detail.run.engine))
  const mixed = engines.size > 1
  const simulated = runs.find((r) => r.detail.run.engine === 'python_sim')?.detail.run
  const reference = runs[0].detail.run

  const settingsRows: { name: string; values: string[] }[] = [
    { name: 'Engine', values: runs.map((r) => (r.detail.run.engine === 'mt5_tester' ? 'Strategy Tester' : 'Python simulator')) },
    { name: 'Strategy', values: runs.map((r) => r.detail.run.strategy_name) },
    { name: 'Symbol', values: runs.map((r) => r.detail.run.settings.symbol) },
    { name: 'Timeframe', values: runs.map((r) => r.detail.run.settings.timeframe) },
    { name: 'Period', values: runs.map((r) => `${r.detail.run.settings.date_from} to ${r.detail.run.settings.date_to}`) },
    { name: 'Tick model', values: runs.map((r) => MODEL_LABEL[r.detail.run.settings.model]) },
    { name: 'Deposit', values: runs.map((r) => `${r.detail.run.settings.deposit} ${r.detail.run.settings.currency}`) },
    { name: 'Leverage', values: runs.map((r) => `1:${r.detail.run.settings.leverage}`) },
  ]
  const names = [...new Set(runs.flatMap((r) => [...(r.detail.strategy?.parameters.map((p) => p.name) ?? []), ...Object.keys(r.detail.run.settings.parameters)]))]
  const parameterRows = names.map((name) => ({
    name,
    values: runs.map((r) => r.detail.run.settings.parameters[name] ?? r.detail.strategy?.parameters.find((p) => p.name === name)?.default ?? 'not an input'),
  }))
  const differs = (values: string[]) => values.some((v) => v !== values[0])
  const metrics = [...HEADLINE, ...ALL_METRICS.filter((m) => !HEADLINE.some((h) => h.key === m.key))]

  return (
    <div className="compare">
      <PageHeader
        title="Compare"
        description={`${runs.length} runs. Run A is the reference; the others show their difference from it.`}
        actions={<Link to="/compare">Choose other runs</Link>}
      />

      {mixed ? (
        <Notice tone="warning" title="Strategy Tester and simulated results side by side">
          <p>
            Part of any difference between these runs comes from the engines, not the strategy. The Python results are
            simulated by StrategyLab.{' '}
            {simulated ? fidelityBody(fidelity.data?.python_sim?.[simulated.settings.model] ?? simulated.fidelity ?? '') : null}
          </p>
        </Notice>
      ) : null}
      {notDone.length ? (
        <Notice tone="warning" title="Some of these runs did not finish">
          {notDone.map((r) => r.detail.run.id).join(', ')} {notDone.length === 1 ? 'has' : 'have'} no results to compare.
        </Notice>
      ) : null}

      <div className="compare__heads" style={{ gridTemplateColumns: `repeat(${runs.length}, minmax(0, 1fr))` }}>
        {runs.map(({ detail: { run } }, index) => (
          <div key={run.id} className="compare__head">
            <div className="compare__head-top">
              <span className={`compare__swatch compare__swatch--${lineStyle(index).css} compare__swatch--${lineStyle(index).color}`} aria-hidden />
              <span className="compare__letter num">{LETTERS[index]}</span>
              <EngineBadge engine={run.engine} size="sm" />
            </div>
            <Link to={`/runs/${run.id}`} className="compare__name truncate" title={run.strategy_name}>
              {run.strategy_name}
            </Link>
            <span className="faint num compare__meta">{`${run.settings.symbol} ${run.settings.timeframe}, ${run.settings.date_from} to ${run.settings.date_to}`}</span>
          </div>
        ))}
      </div>

      <Section title="Profit since the start">
        <CompareChart
          lines={runs.map((r, index) => ({
            id: r.detail.run.id,
            label: LETTERS[index],
            points: r.series.points,
            deposit: r.detail.run.settings.deposit,
          }))}
        />
      </Section>

      <Section title="Metrics">
        <div className="compare__frame">
          <table className="compare-table compare-table--metrics">
            <caption className="visually-hidden">Metrics of the compared runs</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                {runs.map((r, index) => (
                  <th key={r.detail.run.id} scope="col" className="compare-table__num">
                    {LETTERS[index]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map((info) => {
                const values = runs.map((r) => r.detail.run.metrics[info.key])
                const same = values.every((v) => v === values[0])
                return (
                  <tr key={info.key} className={same ? 'compare-table__row--same' : ''}>
                    <th scope="row">{info.label}</th>
                    {values.map((value, index) => {
                      const tone = info.kind === 'money' && value ? (value > 0 ? 'figure--profit' : 'figure--loss') : ''
                      return (
                        <td key={index} className={`compare-table__num ${!same && index > 0 ? 'compare-table__cell--differs' : ''}`}>
                          <span className={`num ${tone}`}>{formatMetric(info, value)}</span>
                          {index > 0 ? <Delta info={info} value={value} reference={reference.metrics[info.key]} /> : null}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Settings and inputs">
        <div className="compare__frame">
          <table className="compare-table">
            <caption className="visually-hidden">Settings and inputs of the compared runs</caption>
            <thead>
              <tr>
                <th scope="col">Setting</th>
                {runs.map((r, index) => (
                  <th key={r.detail.run.id} scope="col">
                    {LETTERS[index]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...settingsRows, ...parameterRows].map((row, rowIndex) => {
                const changed = differs(row.values)
                return (
                  <tr key={row.name} className={`${changed ? '' : 'compare-table__row--same'} ${rowIndex === settingsRows.length ? 'compare-table__row--break' : ''}`}>
                    <th scope="row" className={rowIndex >= settingsRows.length ? 'num' : ''}>
                      {row.name}
                    </th>
                    {row.values.map((value, index) => (
                      <td key={index} className={`num ${changed && index > 0 && value !== row.values[0] ? 'compare-table__cell--differs' : ''}`}>
                        {value}
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <p className="faint compare__note">Values that differ from run A are marked; rows that are the same in every run are dimmed.</p>
      </Section>
    </div>
  )
}
