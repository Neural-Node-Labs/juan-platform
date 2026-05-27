import { Bot, Plus, Settings, Wifi, WifiOff, Loader } from 'lucide-react'
import type { ConnectionState } from '@/types'

interface SidebarProps {
  connection: ConnectionState
  onNewChat: () => void
  onSettings: () => void
}

const STATUS_ICON = {
  connected:    <Wifi size={14} className="status-icon status-icon--ok" />,
  connecting:   <Loader size={14} className="status-icon status-icon--spin" />,
  auth:         <Loader size={14} className="status-icon status-icon--spin" />,
  disconnected: <WifiOff size={14} className="status-icon status-icon--off" />,
  error:        <WifiOff size={14} className="status-icon status-icon--err" />,
}

const STATUS_LABEL = {
  connected:    'Connected',
  connecting:   'Connecting…',
  auth:         'Authenticating…',
  disconnected: 'Disconnected',
  error:        'Error',
}

export function Sidebar({ connection, onNewChat, onSettings }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Navigation">
      <div className="sidebar-header">
        <Bot size={28} strokeWidth={1.5} className="sidebar-logo" aria-hidden />
        <span className="sidebar-brand">Juan</span>
      </div>

      <nav className="sidebar-nav">
        <button
          className="sidebar-btn sidebar-btn--primary"
          onClick={onNewChat}
          aria-label="Start new chat"
        >
          <Plus size={16} />
          <span>New chat</span>
        </button>
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-status" aria-live="polite" aria-atomic>
          {STATUS_ICON[connection.status]}
          <span className="sidebar-status-label">
            {STATUS_LABEL[connection.status]}
          </span>
        </div>
        <button
          className="sidebar-icon-btn"
          onClick={onSettings}
          aria-label="Open settings"
          title="Settings"
        >
          <Settings size={18} />
        </button>
      </div>
    </aside>
  )
}
