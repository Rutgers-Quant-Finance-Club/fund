import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { Pod } from '@/data/types'
import { TINT_LINE } from '@/data/colors'
import { formatCurrency } from '@/lib/formatters'

interface Props {
  pods: Pod[]
  // pod ids to render; if undefined, render all
  visible?: string[]
  height?: number
}

// Live account value of every pod on one graph, labelled by pod.
export function PortfolioChart({ pods, visible, height = 420 }: Props) {
  const shown = visible ? pods.filter((p) => visible.includes(p.id)) : pods

  // merge every pod's 1-min series onto one timeline keyed by timestamp
  const data = useMemo(() => {
    const stamps = new Set<string>()
    shown.forEach((p) => p.nav.forEach((n) => stamps.add(n.t)))
    const byPod = new Map(shown.map((p) => [p.id, new Map(p.nav.map((n) => [n.t, n.value]))]))
    return [...stamps].sort().map((t) => {
      const row: Record<string, number | string> = { t }
      shown.forEach((p) => {
        const v = byPod.get(p.id)?.get(t)
        if (v != null) row[p.id] = v
      })
      return row
    })
  }, [shown])

  // intraday (1-min bars) → show times; longer history → show dates
  const intraday = useMemo(() => {
    if (data.length < 2) return true
    const span = +new Date(data[data.length - 1].t as string) - +new Date(data[0].t as string)
    return span <= 48 * 60 * 60 * 1000
  }, [data])

  const fmtAxis = (v: string) =>
    intraday
      ? new Date(v).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      : new Date(v).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

  // Y-axis domain: enforce a minimum $5k spread so small moves don't expand
  // to fill the full height with micro-increments. Centers the line when data
  // hasn't moved much; adds 8% padding when the natural range is larger.
  const yDomain = useMemo((): [number, number] | ['auto', 'auto'] => {
    const vals = data.flatMap((row) =>
      shown.map((p) => row[p.id]).filter((v): v is number => typeof v === 'number'),
    )
    if (!vals.length) return ['auto', 'auto']
    const dataMin = Math.min(...vals)
    const dataMax = Math.max(...vals)
    const dataRange = dataMax - dataMin
    const MIN_RANGE = 5_000 // ~$1k per rung with 5 ticks
    if (dataRange >= MIN_RANGE) {
      const pad = dataRange * 0.08
      return [dataMin - pad, dataMax + pad]
    }
    const mid = (dataMin + dataMax) / 2
    return [mid - MIN_RANGE / 2, mid + MIN_RANGE / 2]
  }, [data, shown])

  // Tick label precision adapts to how wide the domain is.
  const yTickFmt = useMemo(() => {
    if (yDomain[0] === 'auto') return (v: number) => `$${(v / 1_000).toFixed(0)}k`
    const range = (yDomain[1] as number) - (yDomain[0] as number)
    if (range > 50_000) return (v: number) => `$${(v / 1_000).toFixed(0)}k`
    if (range > 5_000)  return (v: number) => `$${(v / 1_000).toFixed(1)}k`
    return                     (v: number) => `$${(v / 1_000).toFixed(2)}k`
  }, [yDomain])

  return (
    <div className="card-soft bg-panel">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-b border-line px-4 py-2.5">
        {shown.map((p) => (
          <span key={p.id} className="flex items-center gap-2 text-2xs uppercase tracking-[0.1em]">
            <span className="inline-block h-2.5 w-2.5 border border-rule" style={{ background: TINT_LINE[p.tint] }} />
            <span className="font-semibold">{p.code}: {p.name}</span>
            <span className={p.totalReturn >= 0 ? 'pos' : 'neg'}>
              {p.totalReturn >= 0 ? '+' : ''}{(p.totalReturn * 100).toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
      <div className="px-2 pb-2 pt-3" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 18, bottom: 6, left: 6 }}>
            <CartesianGrid stroke="#e2dccd" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={fmtAxis}
              tick={{ fontSize: 10, fill: '#6b6658', fontFamily: 'monospace' }}
              stroke="#1a1916"
              minTickGap={48}
            />
            <YAxis
              tickFormatter={yTickFmt}
              tick={{ fontSize: 10, fill: '#6b6658', fontFamily: 'monospace' }}
              stroke="#1a1916"
              width={56}
              tickCount={5}
              domain={yDomain}
            />
            <Tooltip
              contentStyle={{
                background: '#faf8f2',
                border: '1px solid #1a1916',
                borderRadius: 0,
                fontFamily: 'monospace',
                fontSize: 12,
              }}
              labelFormatter={(v) =>
                new Date(v).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
              }
              formatter={(value: number, name: string) => {
                const pod = pods.find((p) => p.id === name)
                return [formatCurrency(value), pod ? `${pod.code}: ${pod.name}` : name]
              }}
            />
            {shown.map((p) => (
              <Line
                key={p.id}
                type="monotone"
                dataKey={p.id}
                stroke={TINT_LINE[p.tint]}
                strokeWidth={1.6}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
