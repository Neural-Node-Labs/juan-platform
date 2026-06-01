import { X } from 'lucide-react'
import type { UserSettings } from '@/types'

interface SettingsPanelProps {
  settings: UserSettings
  onUpdate: (patch: Partial<UserSettings>) => void
  onClose: () => void
  onReset: () => void
}

export function SettingsPanel({ settings, onUpdate, onClose, onReset }: SettingsPanelProps) {
  return (
    <div
      className="settings-overlay"
      role="dialog"
      aria-modal
      aria-label="Settings"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="settings-panel">
        <div className="settings-header">
          <h2 className="settings-title">Settings</h2>
          <button className="settings-close" onClick={onClose} aria-label="Close settings">
            <X size={18} />
          </button>
        </div>

        <div className="settings-body">

          <section className="settings-section">
            <h3 className="settings-section-title">Connection</h3>

            <label className="settings-label">
              WebSocket URL
              <input
                className="settings-input"
                type="url"
                value={settings.wsUrl}
                onChange={e => onUpdate({ wsUrl: e.target.value })}
                placeholder="ws://localhost:5001"
                aria-describedby="ws-hint"
              />
              <span id="ws-hint" className="settings-hint">
                Real-time connection for React UI
              </span>
            </label>

            <label className="settings-label">
              HTTP URL
              <input
                className="settings-input"
                type="url"
                value={settings.httpUrl}
                onChange={e => onUpdate({ httpUrl: e.target.value })}
                placeholder="http://localhost:5002"
              />
            </label>
          </section>

          <section className="settings-section">
            <h3 className="settings-section-title">Appearance</h3>

            <label className="settings-label">
              Theme
              <select
                className="settings-select"
                value={settings.theme}
                onChange={e => onUpdate({ theme: e.target.value as 'dark' | 'light' })}
              >
                <option value="dark">Dark</option>
                <option value="light">Light</option>
              </select>
            </label>

            <label className="settings-label">
              Font size
              <select
                className="settings-select"
                value={settings.fontSize}
                onChange={e =>
                  onUpdate({ fontSize: e.target.value as 'sm' | 'md' | 'lg' })
                }
              >
                <option value="sm">Small</option>
                <option value="md">Medium</option>
                <option value="lg">Large</option>
              </select>
            </label>
          </section>

          <section className="settings-section">
            <h3 className="settings-section-title">Behaviour</h3>

            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={settings.sendOnEnter}
                onChange={e => onUpdate({ sendOnEnter: e.target.checked })}
                className="sr-only"
              />
              <span className="toggle-track" aria-hidden />
              <span className="toggle-label">Send on Enter</span>
            </label>

            <label className="settings-toggle">
              <input
                type="checkbox"
                checked={settings.showTokenCounts}
                onChange={e => onUpdate({ showTokenCounts: e.target.checked })}
                className="sr-only"
              />
              <span className="toggle-track" aria-hidden />
              <span className="toggle-label">Show token counts</span>
            </label>
          </section>
        </div>

        <div className="settings-footer">
          <button className="settings-reset" onClick={onReset}>
            Reset to defaults
          </button>
          <button className="settings-done" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
