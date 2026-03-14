import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ReviewStatus } from '@/lib/types'

interface ReviewAction {
  symbol: string
  scanDate: string
  status: ReviewStatus
  priority: number
  note: string
  thesis: string
  reviewedAt: string
}

interface UndoEntry {
  key: string
  prev: ReviewAction | undefined
}

interface ReviewsStore {
  reviews: Record<string, ReviewAction>
  undoStack: UndoEntry[]
  shortlist: (symbol: string, scanDate: string) => void
  reject: (symbol: string, scanDate: string) => void
  watch: (symbol: string, scanDate: string) => void
  reset: (symbol: string, scanDate: string) => void
  setNote: (symbol: string, scanDate: string, note: string) => void
  setThesis: (symbol: string, scanDate: string, thesis: string) => void
  undoLast: () => void
  getReview: (symbol: string, scanDate: string) => ReviewAction | undefined
}

const makeKey = (symbol: string, scanDate: string) => `${symbol}:${scanDate}`

const makeReview = (
  symbol: string,
  scanDate: string,
  status: ReviewStatus,
  prev: ReviewAction | undefined
): ReviewAction => ({
  symbol,
  scanDate,
  status,
  priority: status === 'shortlisted' ? 1 : 0,
  note: prev?.note ?? '',
  thesis: prev?.thesis ?? '',
  reviewedAt: prev?.reviewedAt ?? new Date().toISOString(),
})

export const useReviewsStore = create<ReviewsStore>()(
  persist(
    (set, get) => ({
      reviews: {},
      undoStack: [],

      shortlist: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: makeReview(symbol, scanDate, 'shortlisted', prev),
          },
        }))
      },

      reject: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: makeReview(symbol, scanDate, 'rejected', prev),
          },
        }))
      },

      watch: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        const prev = get().reviews[key]
        set((s) => ({
          undoStack: [...s.undoStack.slice(-19), { key, prev }],
          reviews: {
            ...s.reviews,
            [key]: makeReview(symbol, scanDate, 'watching', prev),
          },
        }))
      },

      reset: (symbol, scanDate) => {
        const key = makeKey(symbol, scanDate)
        set((s) => {
          const { [key]: _, ...rest } = s.reviews
          return { reviews: rest }
        })
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
                : {
                    symbol,
                    scanDate,
                    status: 'new' as const,
                    priority: 0,
                    note,
                    thesis: '',
                    reviewedAt: new Date().toISOString(),
                  },
            },
          }
        })
      },

      setThesis: (symbol, scanDate, thesis) => {
        const key = makeKey(symbol, scanDate)
        set((s) => {
          const existing = s.reviews[key]
          return {
            reviews: {
              ...s.reviews,
              [key]: existing
                ? { ...existing, thesis }
                : {
                    symbol,
                    scanDate,
                    status: 'new' as const,
                    priority: 0,
                    note: '',
                    thesis,
                    reviewedAt: new Date().toISOString(),
                  },
            },
          }
        })
      },

      undoLast: () => {
        const { undoStack } = get()
        if (!undoStack.length) return
        const entry = undoStack[undoStack.length - 1]
        set((s) => {
          const newReviews = { ...s.reviews }
          if (entry.prev) {
            newReviews[entry.key] = entry.prev
          } else {
            delete newReviews[entry.key]
          }
          return { reviews: newReviews, undoStack: s.undoStack.slice(0, -1) }
        })
      },

      getReview: (symbol, scanDate) =>
        get().reviews[makeKey(symbol, scanDate)],
    }),
    { name: 'sepa-reviews' }
  )
)
