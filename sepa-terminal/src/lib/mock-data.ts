import type {
  MarketRegime,
  SectorPerformance,
  TrendTemplateResult,
  VCPMetrics,
  Position,
  PortfolioStats,
} from './types'

export function generateMockRegime(): MarketRegime {
  return {
    regime: 'bull',
    regimeLabel: 'Bull Market',
    nifty50Price: 22450,
    nifty50Change: 178.5,
    nifty50ChangePct: 0.8,
    niftyVs200dma: 5.2,
    breadthAbove50d: 68,
    breadthAbove200d: 72,
    indiaVix: 14.2,
    fiiToday: -1200,
    diiToday: 1800,
    fii5d: -3400,
    dii5d: 5200,
    newHighsMinusLows: 42,
    signalCountBreakout: 7,
    signalCountWatch: 34,
  }
}

export function generateMockSectors(): SectorPerformance[] {
  return [
    {
      sector: 'Bank',
      change1m: 3.2,
      change3m: 8.1,
      change6m: 14.5,
      rrqQuadrant: 'leading',
    },
    {
      sector: 'Fin Svc',
      change1m: 2.8,
      change3m: 7.4,
      change6m: 12.8,
      rrqQuadrant: 'leading',
    },
    {
      sector: 'Auto',
      change1m: 1.5,
      change3m: 5.2,
      change6m: 9.8,
      rrqQuadrant: 'improving',
    },
    {
      sector: 'IT',
      change1m: -0.3,
      change3m: -2.1,
      change6m: 1.2,
      rrqQuadrant: 'lagging',
    },
    {
      sector: 'Pharma',
      change1m: 0.9,
      change3m: 3.8,
      change6m: 7.1,
      rrqQuadrant: 'improving',
    },
    {
      sector: 'FMCG',
      change1m: -1.2,
      change3m: -3.4,
      change6m: -1.8,
      rrqQuadrant: 'lagging',
    },
    {
      sector: 'Infra',
      change1m: 2.1,
      change3m: 6.3,
      change6m: 11.2,
      rrqQuadrant: 'leading',
    },
    {
      sector: 'Metal',
      change1m: 4.5,
      change3m: 9.2,
      change6m: 18.6,
      rrqQuadrant: 'leading',
    },
    {
      sector: 'Realty',
      change1m: 5.2,
      change3m: 11.8,
      change6m: 22.4,
      rrqQuadrant: 'leading',
    },
    {
      sector: 'Media',
      change1m: -2.1,
      change3m: -5.6,
      change6m: -8.3,
      rrqQuadrant: 'weakening',
    },
    {
      sector: 'Energy',
      change1m: 1.8,
      change3m: 4.6,
      change6m: 8.9,
      rrqQuadrant: 'improving',
    },
    {
      sector: 'PSU Bank',
      change1m: 6.1,
      change3m: 14.2,
      change6m: 28.5,
      rrqQuadrant: 'leading',
    },
  ]
}

export function generateMockTrendTemplate(
  pass: boolean = true,
): TrendTemplateResult {
  const conditions: TrendTemplateResult['conditions'] = [
    {
      label: 'Price > 150-day MA',
      pass: true,
      value: '₹1,842 vs ₹1,685',
    },
    {
      label: 'Price > 200-day MA',
      pass: true,
      value: '₹1,842 vs ₹1,612',
    },
    {
      label: '150-day MA > 200-day MA',
      pass: true,
      value: '₹1,685 vs ₹1,612',
    },
    {
      label: '200-day MA trending up 1 month',
      pass: pass,
      value: pass ? '₹1,612 vs ₹1,580 (1mo ago)' : '₹1,612 vs ₹1,625 (1mo ago)',
    },
    {
      label: '50-day MA > 150-day MA',
      pass: true,
      value: '₹1,768 vs ₹1,685',
    },
    {
      label: '50-day MA > 200-day MA',
      pass: true,
      value: '₹1,768 vs ₹1,612',
    },
    {
      label: 'Price > 50-day MA',
      pass: true,
      value: '₹1,842 vs ₹1,768',
    },
    {
      label: 'Price within 25% of 52-week high',
      pass: pass,
      value: pass ? '₹1,842 / ₹1,920 (4.1%)' : '₹1,842 / ₹2,480 (25.7%)',
    },
    {
      label: 'Price at least 30% above 52-week low',
      pass: true,
      value: '₹1,842 / ₹1,285 (+43.3%)',
    },
  ]

  const passCount = pass ? 9 : 7

  return { conditions, passCount }
}

export function generateMockVCPMetrics(): VCPMetrics {
  return {
    contractionCount: 3,
    contractionDepths: [22, 11, 5],
    qualityRatio: 0.48,
    baseDepth: 22.4,
    baseDuration: 48,
    volDryUp: 0.52,
    atrContraction: 0.68,
    range10d: 4.8,
    breakoutVolMult: 2.3,
  }
}

export function generateMockFundamentals(): Record<string, string | number> {
  return {
    'P/E': 28.5,
    'Revenue Growth': '+18.2%',
    'PAT Growth': '+22.4%',
    ROE: '16.8%',
    'D/E': 0.3,
    'Market Cap': '\u20B942,500Cr',
    'Promoter %': '62.3%',
    'FII QoQ': '+1.2%',
  }
}

export function generateMockSectorContext(
  sector: string = 'Bank',
): {
  sector: string
  performance1m: number
  performance3m: number
  rrqQuadrant: string
  fiiFlow: string
  diiFlow: string
  sectorBreadth: number
} {
  const sectorData: Record<
    string,
    {
      performance1m: number
      performance3m: number
      rrqQuadrant: string
      fiiFlow: string
      diiFlow: string
      sectorBreadth: number
    }
  > = {
    Bank: {
      performance1m: 3.2,
      performance3m: 8.1,
      rrqQuadrant: 'Leading',
      fiiFlow: '+₹2,400Cr',
      diiFlow: '+₹1,100Cr',
      sectorBreadth: 74,
    },
    'Fin Svc': {
      performance1m: 2.8,
      performance3m: 7.4,
      rrqQuadrant: 'Leading',
      fiiFlow: '+₹1,800Cr',
      diiFlow: '+₹900Cr',
      sectorBreadth: 68,
    },
    IT: {
      performance1m: -0.3,
      performance3m: -2.1,
      rrqQuadrant: 'Lagging',
      fiiFlow: '-₹1,200Cr',
      diiFlow: '+₹400Cr',
      sectorBreadth: 38,
    },
    Pharma: {
      performance1m: 0.9,
      performance3m: 3.8,
      rrqQuadrant: 'Improving',
      fiiFlow: '+₹600Cr',
      diiFlow: '+₹350Cr',
      sectorBreadth: 55,
    },
    Auto: {
      performance1m: 1.5,
      performance3m: 5.2,
      rrqQuadrant: 'Improving',
      fiiFlow: '+₹800Cr',
      diiFlow: '+₹500Cr',
      sectorBreadth: 61,
    },
    Metal: {
      performance1m: 4.5,
      performance3m: 9.2,
      rrqQuadrant: 'Leading',
      fiiFlow: '+₹1,500Cr',
      diiFlow: '+₹700Cr',
      sectorBreadth: 78,
    },
    Realty: {
      performance1m: 5.2,
      performance3m: 11.8,
      rrqQuadrant: 'Leading',
      fiiFlow: '+₹900Cr',
      diiFlow: '+₹450Cr',
      sectorBreadth: 82,
    },
    Infra: {
      performance1m: 2.1,
      performance3m: 6.3,
      rrqQuadrant: 'Leading',
      fiiFlow: '+₹1,100Cr',
      diiFlow: '+₹600Cr',
      sectorBreadth: 65,
    },
  }

  const data = sectorData[sector] ?? {
    performance1m: 1.5,
    performance3m: 4.2,
    rrqQuadrant: 'Improving',
    fiiFlow: '+₹500Cr',
    diiFlow: '+₹300Cr',
    sectorBreadth: 58,
  }

  return { sector, ...data }
}

export function generateMockPortfolio(): Position[] {
  return [
    // 2 shortlisted
    {
      id: 'pos-001',
      symbol: 'PERSISTENT',
      sector: 'IT',
      entryDate: '',
      entryPrice: 0,
      shares: 0,
      initialStop: 0,
      currentStop: 0,
      stopMethod: 'initial',
      stage: 'shortlisted',
      currentPrice: 4820,
      unrealizedPnl: 0,
      rMultiple: 0,
      daysHeld: 0,
    },
    {
      id: 'pos-002',
      symbol: 'MAZDOCK',
      sector: 'Infra',
      entryDate: '',
      entryPrice: 0,
      shares: 0,
      initialStop: 0,
      currentStop: 0,
      stopMethod: 'initial',
      stage: 'shortlisted',
      currentPrice: 3285,
      unrealizedPnl: 0,
      rMultiple: 0,
      daysHeld: 0,
    },
    // 1 watching
    {
      id: 'pos-003',
      symbol: 'POLYCAB',
      sector: 'Infra',
      entryDate: '',
      entryPrice: 0,
      shares: 0,
      initialStop: 0,
      currentStop: 0,
      stopMethod: 'initial',
      stage: 'watching',
      currentPrice: 5640,
      unrealizedPnl: 0,
      rMultiple: 0,
      daysHeld: 0,
    },
    // 3 open
    {
      id: 'pos-004',
      symbol: 'HDFCBANK',
      sector: 'Bank',
      entryDate: '2026-02-10',
      entryPrice: 1680,
      shares: 60,
      initialStop: 1590,
      currentStop: 1680,
      stopMethod: 'breakeven',
      stage: 'open',
      currentPrice: 1755,
      unrealizedPnl: 4500,
      rMultiple: 1.39,
      daysHeld: 32,
    },
    {
      id: 'pos-005',
      symbol: 'RELIANCE',
      sector: 'Energy',
      entryDate: '2026-02-18',
      entryPrice: 2480,
      shares: 40,
      initialStop: 2350,
      currentStop: 2480,
      stopMethod: 'breakeven',
      stage: 'open',
      currentPrice: 2615,
      unrealizedPnl: 5400,
      rMultiple: 1.04,
      daysHeld: 24,
    },
    {
      id: 'pos-006',
      symbol: 'TATAELXSI',
      sector: 'IT',
      entryDate: '2026-03-03',
      entryPrice: 6250,
      shares: 15,
      initialStop: 5900,
      currentStop: 5900,
      stopMethod: 'initial',
      stage: 'open',
      currentPrice: 6480,
      unrealizedPnl: 3450,
      rMultiple: 0.66,
      daysHeld: 11,
    },
    // 1 managing
    {
      id: 'pos-007',
      symbol: 'INFY',
      sector: 'IT',
      entryDate: '2026-01-08',
      entryPrice: 1520,
      shares: 80,
      initialStop: 1440,
      currentStop: 1620,
      stopMethod: '21ema',
      stage: 'managing',
      currentPrice: 1710,
      unrealizedPnl: 15200,
      rMultiple: 2.38,
      daysHeld: 65,
    },
    // 1 closed
    {
      id: 'pos-008',
      symbol: 'BAJFINANCE',
      sector: 'Fin Svc',
      entryDate: '2025-12-15',
      entryPrice: 7200,
      shares: 12,
      initialStop: 6850,
      currentStop: 7500,
      stopMethod: '10dlow',
      stage: 'closed',
      currentPrice: 7500,
      unrealizedPnl: 3600,
      rMultiple: 0.86,
      daysHeld: 42,
      exitPrice: 7500,
      exitDate: '2026-01-26',
      exitReason: 'Stopped out at 10d low',
    },
  ]
}

export function generateMockPortfolioStats(): PortfolioStats {
  return {
    portfolioValue: 1200000,
    capitalBase: 1000000,
    unrealizedPnl: 187000,
    unrealizedPnlPct: 15.6,
    openPositions: 4,
    openSectors: 3,
    capitalAtRiskPct: 4.8,
    capitalAtRiskAmt: 57600,
    avgR: 1.8,
    closedTrades: 12,
    winRate: 58,
    expectancy: 0.42,
  }
}
