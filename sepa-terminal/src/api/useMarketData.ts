import { useMarketStore } from '@/stores/market'

export function useMarketData() {
  const regime = useMarketStore((s) => s.regime)
  const sectors = useMarketStore((s) => s.sectors)
  return { regime, sectors, isLoading: false }
}
