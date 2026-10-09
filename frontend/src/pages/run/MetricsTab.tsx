import { Info } from '@phosphor-icons/react'
import { useResource } from '../../api/hooks'
import type { Metrics } from '../../api/types'
import { formatMetric, METRIC_GROUPS } from '../../runs/catalog'

type MetricsInfo = { definitions: Record<string, string>; metatrader: Record<string, [string, number]> }

/** One part of a report figure such as "684.68 (5.87%)": 0 is the first number, 1 the one in
 * brackets. */
function reportedPart(text: string | undefined, part: number): string | undefined {
  if (text === undefined) return undefined
  const match = /^(.*?)\s*\((.*)\)\s*$/.exec(text)
  if (!match) return part === 0 ? text : undefined
  return (part === 0 ? match[1] : match[2]).trim()
}

export function MetricsTab({ metrics, reported, tester }: { metrics: Metrics; reported: Record<string, string>; tester: boolean }) {
  const info = useResource<MetricsInfo>('/metrics')
  return (
    <div className="metrics-tab">
      <p className="faint metrics-tab__intro">
        Computed by StrategyLab from the deals, the same way for both engines.
        {tester ? ' MetaTrader’s own figure from its report is shown beside each one it also prints; where the two differ, the definitions differ (hover a name for ours).' : ''}
      </p>
      <div className="metrics-tab__groups">
        {METRIC_GROUPS.map((group) => (
          <table key={group.title} className="metrics-table">
            <caption className="metrics-table__caption">{group.title}</caption>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col" className="metrics-table__num">StrategyLab</th>
                {tester ? <th scope="col" className="metrics-table__num">MetaTrader</th> : null}
              </tr>
            </thead>
            <tbody>
              {group.metrics.map((m) => {
                const equivalent = info.data?.metatrader[m.key]
                const theirs = equivalent ? reportedPart(reported[equivalent[0]], equivalent[1]) : undefined
                const definition = info.data?.definitions[m.key]
                const value = metrics[m.key]
                const tone = m.kind === 'money' && value ? (value > 0 ? 'figure--profit' : 'figure--loss') : ''
                return (
                  <tr key={m.key}>
                    <th scope="row" title={definition}>
                      <span className="metrics-table__name">
                        {m.label}
                        {definition ? <Info size={12} aria-hidden className="faint" /> : null}
                      </span>
                    </th>
                    <td className={`metrics-table__num num ${tone}`}>{formatMetric(m, value)}</td>
                    {tester ? <td className="metrics-table__num num faint">{theirs ?? ''}</td> : null}
                  </tr>
                )
              })}
            </tbody>
          </table>
        ))}
      </div>
    </div>
  )
}
