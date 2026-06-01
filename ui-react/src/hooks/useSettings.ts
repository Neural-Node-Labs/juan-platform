import { useCallback, useState } from 'react'
import { DEFAULT_SETTINGS, type UserSettings } from '@/types'

const SETTINGS_KEY = 'juan_ui_settings'

function loadSettings(): UserSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return { ...DEFAULT_SETTINGS }
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) as Partial<UserSettings> }
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

function persistSettings(s: UserSettings): void {
  try {
    // Never persist the password
    const safe = { ...s, password: '' }
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(safe))
  } catch { /* quota may be exceeded */ }
}

export function useSettings() {
  const [settings, setSettings] = useState<UserSettings>(loadSettings)

  const update = useCallback((patch: Partial<UserSettings>) => {
    setSettings(prev => {
      const next = { ...prev, ...patch }
      persistSettings(next)
      return next
    })
  }, [])

  const reset = useCallback(() => {
    setSettings({ ...DEFAULT_SETTINGS })
    try { localStorage.removeItem(SETTINGS_KEY) } catch { /* ignore */ }
  }, [])

  return { settings, update, reset }
}
