import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useFund } from '@/data/useFund'
import { PortfolioChart } from '@/components/PortfolioChart'
import { TradesFeed } from '@/components/TradesFeed'
import { PositionCard, PositionsSummary } from '@/components/Positions'
import { Money, NoData, Pct, PodGlyph } from '@/components/ui'
import { cn } from '@/lib/cn'
import { formatCurrency } from '@/lib/formatters'

type Tab = 'trades' | 'positions'

const WINDOWS = [
  { label: 'Today', minutes: 390 },
  { label: '1W', minutes: 7 * 1440 },
  { label: 'All', minutes: null },
] as const
type WindowMinutes = typeof WINDOWS[number]['minutes']

export function Live() {
  const [chartMinutes, setChartMinutes] = useState<WindowMinutes>(390)
  const fund = useFund(chartMinutes)
  const { pods, trades } = fund
  const [view, setView] = useState<string>('all') // 'all' | podId
  const [tab, setTab] = useState<Tab>('trades')
  const [podFilter, setPodFilter] = useState<string>('all') // sidebar filter

  const visible = view === 'all' ? undefined : [view]

  const agg = useMemo(() => {
    const allocated = pods.reduce((a, p) => a + p.allocatedCapital, 0)
    const accountValue = pods.reduce((a, p) => a + p.accountValue, 0)
    const totalPnl = pods.reduce((a, p) => a + p.totalPnl, 0)
    const realized = pods.reduce((a, p) => a + p.realizedPnl, 0)
    const unrealized = pods.reduce((a, p) => a + p.unrealizedPnl, 0)
    return {
      allocated,
      accountValue,
      totalPnl,
      realized,
      unrealized,
      totalReturn: allocated ? totalPnl / allocated : 0,
    }
  }, [pods])

  if (pods.length === 0) return <NoData title="The fund is not live yet" />

  const headline =
    view === 'all'
      ? { name: 'Aggregate Index', value: agg.accountValue, ret: agg.totalReturn, pnl: agg.totalPnl, realized: agg.realized, unrealized: agg.unrealized }
      : (() => {
          const p = pods.find((x) => x.id === view)!
          return { name: `${p.code}: ${p.name}`, value: p.accountValue, ret: p.totalReturn, pnl: p.totalPnl, realized: p.realizedPnl, unrealized: p.unrealizedPnl }
        })()

  const sidebarTrades = podFilter === 'all' ? trades : trades.filter((t) => t.podId === podFilter)
  const filteredPods = podFilter === 'all' ? pods : pods.filter((p) => p.id === podFilter)

  return (
    <div className="space-y-4">
      {/* pod selector tabs */}
      <div className="flex flex-wrap items-center gap-2 border-b border-rule pb-3">
        <span className="label mr-1">View</span>
        <Tab2 active={view === 'all'} onClick={() => setView('all')}>
          Aggregate Index
        </Tab2>
        {pods.map((p) => (
          <Tab2 key={p.id} active={view === p.id} onClick={() => setView(p.id)}>
            {p.code}: {p.name}
          </Tab2>
        ))}
      </div>

      {/* headline strip */}
      <div className="grid grid-cols-2 gap-px border border-rule bg-rule md:grid-cols-5">
        <Cell label="Portfolio Value">
          <span className="text-lg num">{formatCurrency(headline.value)}</span>
        </Cell>
        <Cell label="Total Return">
          <Pct value={headline.ret} className="text-lg" />
        </Cell>
        <Cell label="Net P&L">
          <Money value={headline.pnl} signed className="text-lg" />
        </Cell>
        <Cell label="Realized">
          <Money value={headline.realized} signed />
        </Cell>
        <Cell label="Unrealized">
          <Money value={headline.unrealized} signed />
        </Cell>
      </div>

      {/* main: sidebar (left) + chart (center/right) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* ── left sidebar ── */}
        <aside className="card flex max-h-[640px] flex-col">
          <div className="flex border-b border-rule">
            <SideTab active={tab === 'trades'} onClick={() => setTab('trades')}>
              Completed Trades
            </SideTab>
            <SideTab active={tab === 'positions'} onClick={() => setTab('positions')}>
              Positions
            </SideTab>
          </div>

          {/* pod filter */}
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <span className="label">Filter</span>
            <select
              value={podFilter}
              onChange={(e) => setPodFilter(e.target.value)}
              className="flex-1 border border-rule bg-paper px-2 py-1 text-2xs uppercase tracking-[0.1em]"
            >
              <option value="all">All Pods</option>
              {pods.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code}: {p.name}
                </option>
              ))}
            </select>
          </div>

          <div className="no-scrollbar flex-1 overflow-y-auto p-2.5">
            {tab === 'trades' ? (
              <TradesFeed trades={sidebarTrades.slice(0, 40)} pods={pods} />
            ) : (
              <div className="space-y-3">
                {filteredPods.map((p) => (
                  <div key={p.id} className="space-y-2">
                    <div className="flex items-center gap-2">
                      <PodGlyph tint={p.tint} label={p.name} size={20} />
                      <span className="text-2xs font-semibold uppercase tracking-[0.1em]">
                        {p.code}: {p.name}
                      </span>
                    </div>
                    <PositionsSummary pod={p} />
                    {p.positions.map((pos) => (
                      <PositionCard key={pos.id} position={pos} />
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* ── center/right chart ── */}
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h1 className="font-serif text-2xl">{headline.name}</h1>
            <div className="flex items-center gap-3">
              {view !== 'all' && (() => {
                const sharpe = pods.find((p) => p.id === view)?.sharpe
                return sharpe != null
                  ? <span className="label">Sharpe&nbsp;<span className="num text-ink">{sharpe.toFixed(3)}</span></span>
                  : null
              })()}
              <div className="flex items-center gap-1">
              <span className="label mr-1">Window</span>
              {WINDOWS.map((w) => (
                <button
                  key={String(w.minutes)}
                  onClick={() => setChartMinutes(w.minutes)}
                  className={cn(
                    'px-2.5 py-1 text-2xs uppercase tracking-[0.1em] border border-rule',
                    chartMinutes === w.minutes ? 'bg-ink text-paper' : 'hover:bg-paper',
                  )}
                >
                  {w.label}
                </button>
              ))}
              </div>
            </div>
          </div>
          <PortfolioChart pods={pods} visible={visible} height={440} />

          {/* per-pod quick cards */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {pods.map((p) => (
              <Link key={p.id} to={`/pods/${p.code}`} className="card-soft px-3 py-3 hover:bg-paper">
                <div className="flex items-center gap-2">
                  <PodGlyph tint={p.tint} label={p.name} size={24} />
                  <div className="min-w-0">
                    <div className="truncate text-xs font-semibold">{p.code}: {p.name}</div>
                    <div className="text-2xs text-faint">{p.assetClass}</div>
                  </div>
                </div>
                <div className="mt-2 flex items-end justify-between">
                  <span className="num text-sm">{formatCurrency(p.accountValue)}</span>
                  <Pct value={p.totalReturn} />
                </div>
              </Link>
            ))}
          </div>
        </section>
      </div>

      {/* mission blurb */}
      <div className="card-soft px-5 py-4 text-xs leading-relaxed text-ink/90">
        <span className="font-semibold">RQFC</span> is a fully transparent, multi-pod paper-trading fund.
        Every order placed through the <code className="bg-paper px-1">rqfc</code> client is executed by our backend,
        recorded, and marked against live market data — so the moment a pod buys NVDA and NVDA ticks up, that pod's
        value moves with it. No static numbers, no hand-waving: realized and unrealized P&amp;L update continuously.{' '}
        <Link to="/about" className="underline underline-offset-4">Read the mission →</Link>
      </div>
    </div>
  )
}

function Tab2({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={cn('chip cursor-pointer', active && 'chip-active')}>
      {children}
    </button>
  )
}

function SideTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex-1 px-3 py-2 text-2xs uppercase tracking-[0.1em] border-r border-rule last:border-r-0',
        active ? 'bg-ink text-paper' : 'hover:bg-paper',
      )}
    >
      {children}
    </button>
  )
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-panel px-3 py-2.5">
      <div className="label">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  )
}
