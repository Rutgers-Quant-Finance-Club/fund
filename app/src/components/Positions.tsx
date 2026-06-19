import type { Pod, Position } from '@/data/types'
import { Money, SideBadge } from './ui'
import { formatCurrency, formatDateTime } from '@/lib/formatters'
import { cn } from '@/lib/cn'

// Per-position card for the live sidebar "Positions" tab.
export function PositionCard({ position }: { position: Position }) {
  const p = position
  return (
    <div className="card-soft px-3 py-2.5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-xs">
          <SideBadge side={p.side} />
          <span className="font-semibold">${p.symbol}</span>
          {p.instrumentType === 'option' && <span className="chip py-0">OPT</span>}
        </span>
        <span className={cn('num text-2xs', p.unrealizedPnl >= 0 ? 'pos' : 'neg')}>
          {p.unrealizedPnl >= 0 ? '+' : ''}
          {formatCurrency(p.unrealizedPnl)}
        </span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
        <Row k="Qty" v={p.quantity.toLocaleString()} />
        <Row k="Entry" v={formatCurrency(p.avgEntry)} />
        <Row k="Mark" v={formatCurrency(p.currentPrice)} />
        <Row k="Mkt Value" v={formatCurrency(Math.abs(p.marketValue))} />
        {p.openedAt && (
          <div className="col-span-2 flex items-center justify-between gap-2">
            <dt className="text-faint uppercase tracking-[0.08em]">Opened</dt>
            <dd className="num">{formatDateTime(p.openedAt)}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-faint uppercase tracking-[0.08em]">{k}</dt>
      <dd className="num">{v}</dd>
    </div>
  )
}

// Pod summary strip: Sharpe, unrealized, net P&L (live sidebar header).
export function PositionsSummary({ pod }: { pod: Pod }) {
  const cells: [string, React.ReactNode][] = [
    ['Sharpe', <span className="num">{pod.sharpe.toFixed(2)}</span>],
    ['Unrealized', <Money value={pod.unrealizedPnl} signed />],
    ['Net P&L', <Money value={pod.totalPnl} signed />],
    ['Win Rate', <span className="num">{(pod.winRate * 100).toFixed(0)}%</span>],
  ]
  return (
    <div className="grid grid-cols-2 gap-px border border-rule bg-rule sm:grid-cols-4">
      {cells.map(([k, v]) => (
        <div key={k} className="bg-panel px-3 py-2">
          <div className="label">{k}</div>
          <div className="text-sm">{v}</div>
        </div>
      ))}
    </div>
  )
}

// Full positions table (pod detail).
export function PositionsTable({ positions }: { positions: Position[] }) {
  if (!positions.length) {
    return <p className="px-3 py-6 text-center text-2xs uppercase tracking-[0.12em] text-faint">No open positions</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="dtable">
        <thead>
          <tr>
            <th>Side</th>
            <th>Symbol</th>
            <th>Trader</th>
            <th className="text-right">Qty</th>
            <th className="text-right">Avg Entry</th>
            <th className="text-right">Mark</th>
            <th className="text-right">Mkt Value</th>
            <th className="text-right">Unreal P&L</th>
            <th className="text-right">Total P&L</th>
            <th className="text-right">Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.id}>
              <td><SideBadge side={p.side} /></td>
              <td className="font-semibold">${p.symbol}</td>
              <td className="text-faint">{p.trader}</td>
              <td className="text-right">{p.quantity.toLocaleString()}</td>
              <td className="text-right">{formatCurrency(p.avgEntry)}</td>
              <td className="text-right">{formatCurrency(p.currentPrice)}</td>
              <td className="text-right">{formatCurrency(Math.abs(p.marketValue))}</td>
              <td className="text-right"><Money value={p.unrealizedPnl} signed /></td>
              <td className="text-right"><Money value={p.totalPnl} signed /></td>
              <td className="text-right text-faint">{p.openedAt ? formatDateTime(p.openedAt) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
