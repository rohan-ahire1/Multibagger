import { useHotkeys } from 'react-hotkeys-hook'
import { useScannerStore } from '@/stores/scanner'
import { useReviewsStore } from '@/stores/reviews'
import { useUIStore } from '@/stores/ui'
import { useCallback } from 'react'

export function useKeyboardTriage(setupIds: string[]) {
  const selectedSetupId = useScannerStore((s) => s.selectedSetupId)
  const selectNext = useScannerStore((s) => s.selectNext)
  const selectPrev = useScannerStore((s) => s.selectPrev)
  const shortlist = useReviewsStore((s) => s.shortlist)
  const reject = useReviewsStore((s) => s.reject)
  const watch = useReviewsStore((s) => s.watch)
  const undoLast = useReviewsStore((s) => s.undoLast)
  const scanDate = useUIStore((s) => s.scanDate)
  const setWorkspace = useUIStore((s) => s.setWorkspace)

  const isInputFocused = useCallback(() => {
    const el = document.activeElement
    return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
  }, [])

  useHotkeys(
    'j, ArrowDown',
    () => {
      if (!isInputFocused()) selectNext(setupIds)
    },
    { preventDefault: true },
    [setupIds]
  )

  useHotkeys(
    'k, ArrowUp',
    () => {
      if (!isInputFocused()) selectPrev(setupIds)
    },
    { preventDefault: true },
    [setupIds]
  )

  useHotkeys(
    's',
    () => {
      if (isInputFocused()) return
      const sym = selectedSetupId?.split(':')[0]
      if (sym) {
        shortlist(sym, scanDate)
        selectNext(setupIds)
      }
    },
    [setupIds, scanDate, selectedSetupId]
  )

  useHotkeys(
    'x',
    () => {
      if (isInputFocused()) return
      const sym = selectedSetupId?.split(':')[0]
      if (sym) {
        reject(sym, scanDate)
        selectNext(setupIds)
      }
    },
    [setupIds, scanDate, selectedSetupId]
  )

  useHotkeys(
    'w',
    () => {
      if (isInputFocused()) return
      const sym = selectedSetupId?.split(':')[0]
      if (sym) watch(sym, scanDate)
    },
    [scanDate, selectedSetupId]
  )

  useHotkeys(
    'Enter, ArrowRight',
    () => {
      if (!isInputFocused() && selectedSetupId) setWorkspace(2)
    },
    [selectedSetupId]
  )

  useHotkeys('mod+z', () => {
    if (!isInputFocused()) undoLast()
  }, { preventDefault: true })

  useHotkeys('n', (e) => {
    if (isInputFocused()) return
    e.preventDefault()
    const noteInput = document.querySelector<HTMLTextAreaElement>(
      '[data-note-input]'
    )
    noteInput?.focus()
  })
}
