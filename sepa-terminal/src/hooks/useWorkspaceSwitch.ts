import { useHotkeys } from 'react-hotkeys-hook'
import { useUIStore } from '@/stores/ui'

export function useWorkspaceSwitch() {
  const setWorkspace = useUIStore((s) => s.setWorkspace)
  useHotkeys('1', () => setWorkspace(1), { preventDefault: true })
  useHotkeys('2', () => setWorkspace(2), { preventDefault: true })
  useHotkeys('3', () => setWorkspace(3), { preventDefault: true })
  useHotkeys('4', () => setWorkspace(4), { preventDefault: true })
}
