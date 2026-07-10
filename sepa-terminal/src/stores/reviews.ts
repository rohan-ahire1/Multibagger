import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ReviewStatus } from '@/lib/types'

export interface ReviewAction {
  symbol: string
  scanDate: string
  status: ReviewStatus
  note: string
  reviewedAt: string
}

interface UndoEntry {
  key: string
  prev: ReviewAction | undefined
}

interface ReviewsStore {
  reviews: Record<string, ReviewAction>
  undoStack: UndoEntry[]
  setStatus: (symbol: string, scanDate: string, status: ReviewStatus) => void
  setNote: (symbol: string, scanDate: string, note: string) => void
  undoLast: () => void
  getReview: (symbol: string, scanDate: string) => ReviewAction | undefined
}

const makeKey = (symbol: string, scanDate: string) => `${symbol}:${scanDate}`

export const useReviewsStore = create<ReviewsStore>()(
  persist(
    (set, get) => ({
      reviews: {},
      undoStack: [],

      setStatus: (symbol, scanDate, status) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        const next: ReviewAction = {
          symbol,
          scanDate,
          status,
          note: prev?.note ?? '',
          reviewedAt: new Date().toISOString(),
        }
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: status === 'new' && !next.note
            ? Object.fromEntries(Object.entries(s.reviews).filter(([k]) => k !== key))
            : { ...s.reviews, [key]: next },
        }))
      },

      setNote: (symbol, scanDate, note) => {
        const key = makeKey(symbol, scanDate)
        set((s) => {
          const existing = s.reviews[key]
          return {
            reviews: {
              ...s.reviews,
              [key]: existing
                ? { ...existing, note }
                : { symbol, scanDate, status: 'new', note, reviewedAt: new Date().toISOString() },
            },
          }
        })
      },

      undoLast: () => {
        const { undoStack } = get()
        if (!undoStack.length) return
        const entry = undoStack[undoStack.length - 1]
        set((s) => {
          const reviews = { ...s.reviews }
          if (entry.prev) reviews[entry.key] = entry.prev
          else delete reviews[entry.key]
          return { reviews, undoStack: s.undoStack.slice(0, -1) }
        })
      },

      getReview: (symbol, scanDate) => get().reviews[makeKey(symbol, scanDate)],
    }),
    { name: 'sepa-reviews-v2' }
  )
)
