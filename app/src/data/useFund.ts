import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { backendUrl } from '@/lib/backend'
import { maxDrawdownOf, round, sharpeOf } from './compute'
import type { FundData, NavPoint, Pod, Position, Tint, TickerItem, Trade, Trader } from './types'

const TINTS: Tint[] = ['p3', 'p1', 'p5', 'p4', 'p2', 'p6']

// ── raw shapes from the backend public feeds ──────────────────────────
interface LivePosition {
  symbol: string
  quantity: number
  avg_entry_price: number
  current_price: number | null
  market_value: number | null
  cost_basis?: number | null
  unrealized_pnl: number | null
  realized_pnl?: number | null
  total_pnl?: number | null
  instrument_type?: string
}
interface LiveMember {
  id: string
  name: string
  role: string
  is_admin: boolean
}
interface LiveSnapshot {
  id: string
  name: string
  asset_class: string
  description?: string | null
  benchmark_symbol?: string | null
  inception_date?: string | null
  allocated_capital: number
  live: boolean
  account?: { portfolio_value?: number; cash?: number } | null
  nav: number
  cash?: number | null
  gross_notional?: number
  net_notional?: number
  realized_pnl?: number
  unrealized_pnl?: number
  total_pnl?: number
  fees?: number
  live_gain?: number
  session_return?: number | null
  total_return?: number | null
  members?: LiveMember[]
  nav_series?: { t: string; value: number }[]
  positions?: LivePosition[]
}
interface LiveTrade {
  id: string
  pod_id: string
  pod_name: string | null
  trader: string | null
  trader_id: string | null
  symbol: string
  side: string
  instrument_type: string
  quantity: number | null
  price: number | null
  notional: number | null
  type: string
  status: string
  realized_pnl: number | null
  executed_at: string
}

async function getJSON<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(backendUrl(path))
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

interface NavSeriesPod {
  pod_id: string
  name: string
  series: { t: string; value: number }[]
}

const fetchLive = () => getJSON<{ pods: LiveSnapshot[] }>('/public/live').then((d) => d?.pods ?? null)
const fetchNavSeries = (minutes: number) =>
  getJSON<{ pods: NavSeriesPod[] }>(`/public/nav-series?minutes=${minutes}`).then((d) => d?.pods ?? null)
const fetchTrades = () => getJSON<{ trades: LiveTrade[] }>('/public/trades?limit=200').then((d) => d?.trades ?? null)
const fetchTicker = () =>
  getJSON<{ items: { symbol: string; price: number; change_pct: number }[] }>('/public/ticker').then(
    (d) => d?.items?.map((i) => ({ symbol: i.symbol, price: i.price, changePct: i.change_pct })) ?? null,
  )

// ── assembly ──────────────────────────────────────────────────────────
function mapPosition(podId: string, p: LivePosition, idx: number): Position {
  const side: 'long' | 'short' = p.quantity >= 0 ? 'long' : 'short'
  const mv = p.market_value ?? 0
  return {
    id: `${podId}-pos-${idx}`,
    podId,
    symbol: p.symbol,
    side,
    instrumentType: p.instrument_type === 'option' ? 'option' : 'equity',
    quantity: Math.abs(p.quantity),
    avgEntry: p.avg_entry_price,
    currentPrice: p.current_price ?? p.avg_entry_price,
    marketValue: mv,
    costBasis: p.cost_basis ?? p.avg_entry_price * p.quantity,
    unrealizedPnl: p.unrealized_pnl ?? 0,
    realizedPnl: p.realized_pnl ?? 0,
    totalPnl: p.total_pnl ?? p.unrealized_pnl ?? 0,
    trader: '',
    openedAt: '',  // enriched below from trade history
  }
}

function mapTrade(t: LiveTrade, podByName: Map<string, Pod>, podById: Map<string, Pod>): Trade {
  const pod = podById.get(t.pod_id) ?? (t.pod_name ? podByName.get(t.pod_name.toLowerCase()) : undefined)
  const qty = t.quantity == null ? null : Math.abs(t.quantity)
  const price = t.price
  const notional = t.notional ?? (qty != null && price != null ? Math.abs(qty * price) : null)
  return {
    id: t.id,
    podId: t.pod_id,
    podCode: pod?.code ?? 0,
    podName: pod?.name ?? t.pod_name ?? '',
    trader: t.trader ?? '—',
    traderId: t.trader_id ?? '',
    symbol: t.symbol,
    side: t.side === 'sell' ? 'sell' : 'buy',
    instrumentType: t.instrument_type === 'option' ? 'option' : 'equity',
    quantity: qty,
    price,
    notional,
    type: (t.type || 'MARKET').toUpperCase(),
    status: t.status ?? 'filled',
    realizedPnl: t.realized_pnl ?? null,
    executedAt: t.executed_at,
  }
}

function assemble(
  snaps: LiveSnapshot[] | null,
  liveTrades: LiveTrade[] | null,
  ticker: TickerItem[] | null,
  navSeries: NavSeriesPod[] | null,
): FundData {
  const empty: FundData = {
    pods: [],
    traders: [],
    trades: [],
    positions: [],
    ticker: ticker ?? [],
    isLive: false,
    asOf: new Date().toISOString(),
  }
  if (!snaps || snaps.length === 0) return empty

  const minuteNavByPod = new Map((navSeries ?? []).map((n) => [n.pod_id, n.series]))
  const nowIso = new Date().toISOString()

  // first pass: pods with positions + nav + pod-level metrics
  const pods: Pod[] = snaps.map((s, i) => {
    const podId = s.id
    const allocated = s.allocated_capital || 0
    const accountValue = s.account?.portfolio_value ?? s.nav ?? allocated
    const positions = (s.positions ?? []).map((p, idx) => mapPosition(podId, p, idx))

    // 1-minute live market-data series; recorded history as fallback. The
    // latest point is always anchored to the live account value so the line
    // keeps ticking between minute bars.
    const minute = minuteNavByPod.get(podId)
    const base = minute?.length ? minute : (s.nav_series ?? [])
    const nav: NavPoint[] = base.map((n) => ({ t: n.t, value: n.value }))
    if (nav.length === 0 || Math.abs(nav[nav.length - 1].value - accountValue) > 0.01) {
      nav.push({ t: nowIso, value: accountValue })
    }

    const unrealizedPnl = s.unrealized_pnl ?? round(positions.reduce((a, p) => a + p.unrealizedPnl, 0))
    const realizedPnl = s.realized_pnl ?? 0
    const totalPnl = s.total_pnl ?? accountValue - allocated
    const grossLong = positions.filter((p) => p.side === 'long').reduce((a, p) => a + Math.abs(p.marketValue), 0)
    const grossShort = positions.filter((p) => p.side === 'short').reduce((a, p) => a + Math.abs(p.marketValue), 0)

    return {
      id: podId,
      code: i + 1,
      name: s.name,
      strategy: s.asset_class,
      assetClass: s.asset_class,
      description: s.description ?? '',
      tint: TINTS[i % TINTS.length],
      inceptionDate: (s.inception_date ?? nav[0]?.t ?? new Date().toISOString()).slice(0, 10),
      allocatedCapital: allocated,
      accountValue,
      cash: s.cash ?? s.account?.cash ?? 0,
      realizedPnl,
      unrealizedPnl,
      totalPnl,
      fees: s.fees ?? 0,
      totalReturn: s.total_return ?? (allocated ? totalPnl / allocated : 0),
      dayReturn: s.session_return ?? 0,
      liveGain: s.live_gain ?? accountValue - allocated,
      sharpe: sharpeOf(nav),
      maxDrawdown: maxDrawdownOf(nav),
      winRate: 0,
      biggestWin: 0,
      biggestLoss: 0,
      avgLeverage: accountValue ? round((grossLong + grossShort) / accountValue, 2) : 0,
      longExposure: accountValue ? round(grossLong / accountValue, 3) : 0,
      shortExposure: accountValue ? round(grossShort / accountValue, 3) : 0,
      traders: [],
      positions,
      nav,
    }
  })

  const podById = new Map(pods.map((p) => [p.id, p]))
  const podByName = new Map(pods.map((p) => [p.name.toLowerCase(), p]))

  // trades
  const trades: Trade[] = (liveTrades ?? [])
    .map((t) => mapTrade(t, podByName, podById))
    .sort((a, b) => +new Date(b.executedAt) - +new Date(a.executedAt))

  // attribute trade-based metrics back to pods + build traders from members
  const traders: Trader[] = []
  pods.forEach((pod, pi) => {
    const podTrades = trades.filter((t) => t.podId === pod.id)
    const realizedAll = podTrades.map((t) => t.realizedPnl).filter((v): v is number => v != null)
    const wins = realizedAll.filter((v) => v > 0).length
    pod.winRate = realizedAll.length ? round(wins / realizedAll.length, 3) : 0
    pod.biggestWin = realizedAll.length ? round(Math.max(0, ...realizedAll)) : 0
    pod.biggestLoss = realizedAll.length ? round(Math.min(0, ...realizedAll)) : 0

    // attach trader names to positions (round-robin over the pod roster)
    const roster = snaps[pi].members ?? []
    pod.positions.forEach((pos, idx) => {
      pos.trader = roster[idx % Math.max(1, roster.length)]?.name ?? ''
    })

    // enrich openedAt: earliest buy trade for this symbol in this pod
    const firstBuyBySymbol = new Map<string, string>()
    podTrades
      .filter((t) => t.side === 'buy')
      .sort((a, b) => +new Date(a.executedAt) - +new Date(b.executedAt))
      .forEach((t) => { if (!firstBuyBySymbol.has(t.symbol)) firstBuyBySymbol.set(t.symbol, t.executedAt) })
    pod.positions.forEach((pos) => {
      pos.openedAt = firstBuyBySymbol.get(pos.symbol) ?? ''
    })

    roster.forEach((m, mi) => {
      const mineTrades = podTrades.filter((t) => t.traderId === m.id)
      const realizedList = mineTrades.map((t) => t.realizedPnl).filter((v): v is number => v != null)
      const realizedSum = round(realizedList.reduce((a, b) => a + b, 0))
      const w = realizedList.filter((v) => v > 0).length
      pod.traders.push({
        id: m.id,
        name: m.name,
        handle: m.name.toLowerCase().replace(/\s+/g, ''),
        podId: pod.id,
        podCode: pod.code,
        role: m.role === 'pm' ? 'pm' : 'trader',
        tint: TINTS[mi % TINTS.length],
        livePnl: pod.totalPnl, // a trader's live P&L = their pod's live P&L
        realizedPnl: realizedSum,
        unrealizedPnl: 0,
        biggestWin: realizedList.length ? round(Math.max(0, ...realizedList)) : 0,
        biggestLoss: realizedList.length ? round(Math.min(0, ...realizedList)) : 0,
        maxDrawdown: pod.maxDrawdown,
        winRate: realizedList.length ? round(w / realizedList.length, 3) : 0,
        trades: mineTrades.length,
      })
    })
    traders.push(...pod.traders)
  })

  return {
    pods,
    traders,
    trades,
    positions: pods.flatMap((p) => p.positions),
    ticker: ticker ?? [],
    isLive: true,
    asOf: new Date().toISOString(),
  }
}

// minutes=null means "use daily history from snapshot, skip the 1-min bars API"
export function useFund(minutes: number | null = 390): FundData {
  const { data: live } = useQuery({ queryKey: ['fund-live'], queryFn: fetchLive, refetchInterval: 5_000, staleTime: 2_000 })
  const { data: liveTrades } = useQuery({ queryKey: ['fund-trades'], queryFn: fetchTrades, refetchInterval: 8_000, staleTime: 4_000 })
  const { data: ticker } = useQuery({ queryKey: ['fund-ticker'], queryFn: fetchTicker, refetchInterval: 15_000, staleTime: 10_000 })
  // 1Min market bars only change once a minute; null minutes = skip fetch, use daily history
  const { data: navSeries } = useQuery({
    queryKey: ['fund-nav', minutes],
    queryFn: () => fetchNavSeries(minutes!),
    enabled: minutes !== null,
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  return useMemo(
    () => assemble(live ?? null, liveTrades ?? null, ticker ?? null, minutes !== null ? (navSeries ?? null) : null),
    [live, liveTrades, ticker, navSeries, minutes],
  )
}

export function usePod(podId: string | undefined): Pod | undefined {
  const fund = useFund()
  return useMemo(
    () => fund.pods.find((p) => p.id === podId || String(p.code) === podId),
    [fund.pods, podId],
  )
}
