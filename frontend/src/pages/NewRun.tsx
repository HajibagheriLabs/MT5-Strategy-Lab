import { Play } from '@phosphor-icons/react'
import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, ApiError } from '../api/client'
import { useResource } from '../api/hooks'
import type { Engine, Parameter, Run, RunCreate, RunDetail, Strategy, Symbols, TickModel } from '../api/types'
import { describeSettings, fidelityBody, MODEL_HELP, MODEL_LABEL, PYTHON_MODEL_HELP } from '../runs/catalog'
import { Button } from '../ui/Button'
import { Checkbox } from '../ui/Checkbox'
import { Combobox } from '../ui/Combobox'
import { DateRange } from '../ui/DateRange'
import type { Range } from '../ui/DateRange'
import { EngineBadge } from '../ui/EngineBadge'
import { NumberInput, Select } from '../ui/Field'
import { TextInput } from '../ui/Field'
import { EmptyState, Notice } from '../ui/Notice'
import { PageHeader, Section } from '../ui/PageHeader'
import { RadioList } from '../ui/RadioList'
import { SkeletonLines } from '../ui/Skeleton'
import { useToast } from '../ui/toast-context'
import './NewRun.css'

const TESTER_TIMEFRAMES = ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M10', 'M12', 'M15', 'M20', 'M30', 'H1', 'H2', 'H3', 'H4', 'H6', 'H8', 'H12', 'D1', 'W1', 'MN1']
const PYTHON_TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD']
const LEVERAGES = [1, 10, 20, 30, 50, 100, 200, 300, 400, 500, 1000]
const TESTER_MODELS: TickModel[] = ['real_ticks', 'every_tick', 'ohlc_m1', 'open_prices']
const PYTHON_MODELS: TickModel[] = ['ohlc_m1', 'real_ticks']

type Form = {
  symbol: string
  timeframe: string
  range: Range
  model: TickModel
  deposit: string
  currency: string
  leverage: string
  commission: string
  parameters: Record<string, string>
}

type Errors = Partial<Record<'symbol' | 'range' | 'deposit' | 'commission' | 'general', string>> & {
  parameters?: Record<string, string>
}

function addDays(day: string, days: number): string {
  const date = new Date(`${day}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

const today = () => new Date().toISOString().slice(0, 10)

function checkParameter(p: Parameter, value: string): string | null {
  const text = value.trim()
  if (p.kind === 'integer' && !/^[-+]?\d+$/.test(text)) return 'A whole number.'
  if (p.kind === 'real' && (text === '' || Number.isNaN(Number(text)))) return 'A number.'
  return null
}

function ParameterField({ p, value, error, onChange }: { p: Parameter; value: string; error?: string; onChange: (v: string) => void }) {
  const label = p.label ?? p.name
  const changed = p.default !== value
  const help = (
    <>
      <span className="num">{p.name}</span>
      {changed ? `, default ${p.options.find((o) => o.value === p.default)?.label ?? p.default}` : ''}
    </>
  )
  if (p.kind === 'bool') {
    return (
      <div className="param param--bool">
        <Checkbox label={label} checked={value === 'true'} onChange={(e) => onChange(e.target.checked ? 'true' : 'false')} />
        <p className="field__help">{help}</p>
      </div>
    )
  }
  if (p.options.length) {
    return <Select label={label} help={help} value={value} onChange={(e) => onChange(e.target.value)} options={p.options} />
  }
  if (p.kind === 'integer' || p.kind === 'real') {
    return <NumberInput label={label} help={error ? undefined : help} error={error} value={value} onChange={(e) => onChange(e.target.value)} />
  }
  return <TextInput label={label} help={error ? undefined : help} error={error} value={value} onChange={(e) => onChange(e.target.value)} />
}

export function NewRun() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const toast = useToast()
  const fromRun = params.get('from')
  const strategies = useResource<Strategy[]>('/strategies')
  const symbols = useResource<Symbols>('/symbols')
  const fidelity = useResource<Record<Engine, Partial<Record<TickModel, string>>>>('/fidelity')
  const previous = useResource<RunDetail>(fromRun ? `/runs/${encodeURIComponent(fromRun)}` : null)

  const [chosenHash, setHash] = useState<string | null>(params.get('strategy'))
  const [edited, setForm] = useState<Form | null>(null)
  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false)
  const hash = chosenHash ?? previous.data?.run.strategy_hash ?? ''

  // The form starts from the run being repeated, or from sensible defaults on the first symbol
  // with history; after that it holds whatever was edited.
  const initial = useMemo<Form | null>(() => {
    if (fromRun) {
      if (!previous.data) return null
      const s = previous.data.run.settings
      return {
        symbol: s.symbol,
        timeframe: s.timeframe,
        range: { from: s.date_from, to: s.date_to },
        model: s.model,
        deposit: String(s.deposit),
        currency: s.currency,
        leverage: String(s.leverage),
        commission: String(s.commission_per_lot ?? 0),
        parameters: { ...s.parameters },
      }
    }
    if (!symbols.data) return null
    const list = symbols.data.symbols
    const first = list.find((s) => s.name.toUpperCase().startsWith('EURUSD')) ?? list[0]
    const end = first?.bars_to ? addDays(first.bars_to, 1) : today()
    const yearBefore = `${Number(end.slice(0, 4)) - 1}${end.slice(4)}`
    return {
      symbol: first?.name ?? '',
      timeframe: 'H1',
      range: { from: yearBefore, to: end },
      model: 'ohlc_m1',
      deposit: '10000',
      currency: 'USD',
      leverage: '100',
      commission: '0',
      parameters: {},
    }
  }, [fromRun, previous.data, symbols.data])
  const form = edited ?? initial

  const ready = (strategies.data ?? []).filter((s) => s.ok)
  const strategy = (strategies.data ?? []).find((s) => s.hash === hash)
  const python = strategy?.engine === 'python_sim'
  const symbolInfo = symbols.data?.symbols.find((s) => s.name === form?.symbol)

  const groups = useMemo(() => {
    const map = new Map<string, Parameter[]>()
    for (const p of strategy?.parameters ?? []) map.set(p.group ?? '', [...(map.get(p.group ?? '') ?? []), p])
    return [...map.entries()]
  }, [strategy])

  const update = (changes: Partial<Form>) => setForm((current) => {
    const base = current ?? initial
    return base ? { ...base, ...changes } : base
  })

  const chooseStrategy = (next: string) => {
    setHash(next)
    const chosen = (strategies.data ?? []).find((s) => s.hash === next)
    if (!chosen || !form) return
    const timeframes = chosen.engine === 'python_sim' ? PYTHON_TIMEFRAMES : TESTER_TIMEFRAMES
    const models = chosen.engine === 'python_sim' ? PYTHON_MODELS : TESTER_MODELS
    // Keep values for inputs the new strategy also has; drop the rest.
    const kept = Object.fromEntries(Object.entries(form.parameters).filter(([name]) => chosen.parameters.some((p) => p.name === name)))
    update({
      timeframe: timeframes.includes(form.timeframe) ? form.timeframe : 'H1',
      model: models.includes(form.model) ? form.model : 'ohlc_m1',
      parameters: kept,
    })
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!form || !strategy) return
    const found: Errors = {}
    if (!symbolInfo) found.symbol = 'Choose a symbol from the list.'
    if (!form.range.from || !form.range.to) found.range = 'Choose a start and an end date.'
    else if (form.range.to <= form.range.from) found.range = 'The end date must be after the start date (the end is not included).'
    if (!(Number(form.deposit) > 0)) found.deposit = 'A deposit above zero.'
    if (python && !(Number(form.commission) >= 0)) found.commission = 'Zero or more.'
    const paramErrors: Record<string, string> = {}
    for (const p of strategy.parameters) {
      const value = form.parameters[p.name]
      if (value === undefined) continue
      const problem = checkParameter(p, value)
      if (problem) paramErrors[p.name] = problem
    }
    if (Object.keys(paramErrors).length) found.parameters = paramErrors
    setErrors(found)
    if (Object.keys(found).length) return

    const body: RunCreate = {
      strategy_hash: strategy.hash,
      symbol: form.symbol,
      timeframe: form.timeframe,
      date_from: form.range.from,
      date_to: form.range.to,
      model: form.model,
      deposit: Number(form.deposit),
      currency: form.currency,
      leverage: Number(form.leverage),
      parameters: Object.fromEntries(
        Object.entries(form.parameters).filter(([name, value]) => strategy.parameters.find((p) => p.name === name)?.default !== value),
      ),
      commission_per_lot: python ? Number(form.commission) : 0,
    }
    setBusy(true)
    try {
      const sameStrategy = previous.data?.run.strategy_hash === strategy.hash
      // A rerun merges over the old run's inputs, so it is sent every input, not just changed ones.
      const every = Object.fromEntries(strategy.parameters.map((p) => [p.name, form.parameters[p.name] ?? p.default]))
      const run =
        fromRun && sameStrategy
          ? await api.post<Run>(`/runs/${encodeURIComponent(fromRun)}/rerun`, { ...body, parameters: every })
          : await api.post<Run>('/runs', body)
      toast.push({ title: 'Run queued', message: `${strategy.name}, ${describeSettings(run.settings)}.` })
      navigate(`/runs/${run.id}`)
    } catch (reason) {
      const message = reason instanceof ApiError ? reason.message : String(reason)
      const param = strategy.parameters.find((p) => message.includes(p.name))
      if (/History|end date|Recorded ticks|start/.test(message)) setErrors({ range: message })
      else if (param) setErrors({ parameters: { [param.name]: message } })
      else setErrors({ general: message })
    } finally {
      setBusy(false)
    }
  }

  const loading = strategies.loading || symbols.loading || (fromRun !== null && previous.loading)
  if (loading) {
    return (
      <>
        <PageHeader title="New run" />
        <SkeletonLines lines={8} />
      </>
    )
  }
  const loadError = strategies.error ?? symbols.error ?? previous.error
  if (loadError) {
    return (
      <>
        <PageHeader title="New run" />
        <Notice tone="danger" title="The form could not be prepared" action={<Button size="sm" onClick={() => { strategies.reload(); symbols.reload() }}>Try again</Button>}>
          {loadError.message}
        </Notice>
      </>
    )
  }
  if (!ready.length) {
    return (
      <>
        <PageHeader title="New run" />
        <EmptyState title="No strategy is ready to run" action={<Link to="/strategies">Upload a strategy</Link>}>
          Upload an MQL5 or Python strategy first. Strategies with compile errors cannot run.
        </EmptyState>
      </>
    )
  }
  if (!symbols.data?.symbols.length || !form) {
    return (
      <>
        <PageHeader title="New run" />
        <Notice tone="warning" title="The terminal has no history yet">
          {symbols.data?.message ?? 'Log the dedicated terminal in to the demo account and open a chart of each symbol you want to test, then come back.'}
        </Notice>
      </>
    )
  }

  const timeframes = python ? PYTHON_TIMEFRAMES : TESTER_TIMEFRAMES
  const models = python ? PYTHON_MODELS : TESTER_MODELS
  const lastDay = symbolInfo?.bars_to ? (symbolInfo.bars_to > today() ? symbolInfo.bars_to : today()) : today()
  const maxEnd = addDays(lastDay, 1)
  const presetEnd = symbolInfo?.bars_to ? addDays(symbolInfo.bars_to, 1) : maxEnd
  const fidelityText = strategy ? fidelity.data?.[strategy.engine]?.[form.model] : undefined

  return (
    <form className="new-run" onSubmit={submit} noValidate>
      <PageHeader
        title={fromRun ? 'Run again with changes' : 'New run'}
        context={fromRun && previous.data ? <>From run <Link to={`/runs/${fromRun}`} className="num">{fromRun}</Link></> : undefined}
        description="Everything here is checked against the strategy and the terminal's history before the run is queued."
      />
      <div className="new-run__grid">
        <div className="new-run__form">
          <Section title="Strategy">
            <div className="new-run__row">
              <Select
                label="Strategy"
                value={hash}
                placeholder="Choose a strategy"
                onChange={(event) => chooseStrategy(event.target.value)}
                options={ready.map((s) => ({ value: s.hash, label: s.name }))}
              />
              {strategy ? <EngineBadge engine={strategy.engine} /> : null}
            </div>
          </Section>

          <Section title="Market and period">
            <div className="new-run__columns">
              <Combobox
                label="Symbol"
                value={form.symbol}
                onChange={(symbol) => {
                  const info = symbols.data?.symbols.find((s) => s.name === symbol)
                  const from = info?.bars_from && form.range.from < info.bars_from ? info.bars_from : form.range.from
                  update({ symbol, range: { ...form.range, from } })
                }}
                options={symbols.data.symbols.map((s) => ({
                  value: s.name,
                  label: s.name,
                  keywords: s.description ?? '',
                  detail: s.bars_from ? `${s.bars_from} to ${s.bars_to}` : `history ${s.history_years[0]} to ${s.history_years[s.history_years.length - 1]}`,
                }))}
                empty="No symbol with history matches. Symbols appear here once the terminal has their history."
                error={errors.symbol}
                help={
                  symbolInfo
                    ? `${symbolInfo.description ?? ''}${symbolInfo.ticks_from ? `. Recorded ticks from ${symbolInfo.ticks_from.slice(0, 7)}` : '. No recorded ticks yet'}`
                    : undefined
                }
              />
              <Select
                label="Timeframe"
                value={form.timeframe}
                onChange={(event) => update({ timeframe: event.target.value })}
                options={timeframes.map((t) => ({ value: t, label: t }))}
                help={python ? 'Python strategies can use these timeframes.' : undefined}
              />
            </div>
            {symbols.data.message ? <p className="field__help new-run__note">{symbols.data.message}</p> : null}
            <DateRange
              label="Period"
              value={form.range}
              onChange={(range) => update({ range })}
              min={symbolInfo?.bars_from ?? undefined}
              max={maxEnd}
              presetEnd={presetEnd}
              error={errors.range}
              help={
                symbolInfo?.bars_from
                  ? `History for ${symbolInfo.name} covers ${symbolInfo.bars_from} to ${symbolInfo.bars_to}. The end date is not included.`
                  : 'The end date is not included.'
              }
            />
          </Section>

          <Section title="Tick model">
            <RadioList<TickModel>
              label={python ? 'Prices the simulator moves through' : 'How the tester generates prices'}
              value={form.model}
              onChange={(model) => update({ model })}
              options={models.map((m) => ({
                value: m,
                label: MODEL_LABEL[m],
                description: python ? PYTHON_MODEL_HELP[m] : MODEL_HELP[m],
              }))}
            />
          </Section>

          <Section title="Account">
            <div className="new-run__columns new-run__columns--3">
              <NumberInput label="Deposit" value={form.deposit} suffix={form.currency} error={errors.deposit} onChange={(e) => update({ deposit: e.target.value })} />
              <Select label="Currency" value={form.currency} onChange={(e) => update({ currency: e.target.value })} options={CURRENCIES.map((c) => ({ value: c, label: c }))} />
              <Select label="Leverage" value={form.leverage} onChange={(e) => update({ leverage: e.target.value })} options={LEVERAGES.map((l) => ({ value: String(l), label: `1:${l}` }))} />
              {python ? (
                <NumberInput
                  label="Commission per lot"
                  value={form.commission}
                  suffix={form.currency}
                  error={errors.commission}
                  help="Charged on each deal. The tester takes commission from the broker instead."
                  onChange={(e) => update({ commission: e.target.value })}
                />
              ) : null}
            </div>
          </Section>

          <Section title={strategy ? `Inputs (${strategy.parameters.length})` : 'Inputs'}>
            {!strategy ? (
              <p className="muted">Choose a strategy to see its inputs.</p>
            ) : strategy.parameters.length === 0 ? (
              <p className="muted">
                {strategy.parameter_source === 'none'
                  ? 'This compiled expert has no source or .set file, so its inputs cannot be read; it runs with its built-in defaults.'
                  : 'This strategy declares no inputs.'}
              </p>
            ) : (
              groups.map(([group, list]) => (
                <fieldset key={group} className="new-run__group">
                  {group ? <legend className="new-run__group-title">{group}</legend> : null}
                  <div className="new-run__columns new-run__columns--3">
                    {list.map((p) => (
                      <ParameterField
                        key={p.name}
                        p={p}
                        value={form.parameters[p.name] ?? p.default}
                        error={errors.parameters?.[p.name]}
                        onChange={(value) => update({ parameters: { ...form.parameters, [p.name]: value } })}
                      />
                    ))}
                  </div>
                </fieldset>
              ))
            )}
          </Section>
        </div>

        <aside className="new-run__summary" aria-label="Summary">
          <h2 className="new-run__summary-title">What will run</h2>
          <dl className="new-run__facts">
            <dt>Strategy</dt>
            <dd className="truncate" title={strategy?.name}>{strategy?.name ?? 'None chosen'}</dd>
            <dt>Engine</dt>
            <dd>{strategy ? <EngineBadge engine={strategy.engine} size="sm" /> : ''}</dd>
            <dt>Market</dt>
            <dd className="num">{form.symbol} {form.timeframe}</dd>
            <dt>Period</dt>
            <dd className="num">{form.range.from} to {form.range.to}</dd>
            <dt>Model</dt>
            <dd>{MODEL_LABEL[form.model]}</dd>
            <dt>Account</dt>
            <dd className="num">{form.deposit} {form.currency}, 1:{form.leverage}</dd>
          </dl>
          {strategy && python ? (
            <Notice tone="warning" title="Simulated, not run in the Strategy Tester">
              {fidelityText ? fidelityBody(fidelityText) : 'Python strategies run in StrategyLab’s simulator; results are an approximation.'}
            </Notice>
          ) : strategy && fidelityText ? (
            <p className="new-run__fidelity">{fidelityText}</p>
          ) : null}
          {errors.general ? (
            <Notice tone="danger" title="The run was not queued">
              {errors.general}
            </Notice>
          ) : null}
          <Button type="submit" variant="primary" icon={<Play size={16} />} busy={busy} disabled={!strategy}>
            {busy ? 'Queuing' : 'Start run'}
          </Button>
        </aside>
      </div>
    </form>
  )
}
