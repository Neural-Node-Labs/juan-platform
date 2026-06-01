// ── Message types ──────────────────────────────────────────────────────────
export type MessageRole = 'user' | 'assistant' | 'system' | 'error'

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  timestamp: number
  requestId?: string
  metadata?: {
    iterations?: number
    inputTokens?: number
    outputTokens?: number
    durationMs?: number
  }
}

// ── WebSocket protocol ──────────────────────────────────────────────────────
export type WSMessageType = 'message' | 'command' | 'ping'
export type WSResponseType = 'response' | 'error' | 'status' | 'pong'

export interface WSOutbound {
  type: WSMessageType
  content?: string
  session_id?: string
  request_id?: string
}

export interface WSInbound {
  type: WSResponseType
  request_id?: string
  content?: string
  session_id?: string
  done?: boolean
  status?: string
  error_code?: string
  message?: string
  metadata?: {
    iterations?: number
    input_tokens?: number
    output_tokens?: number
    duration_ms?: number
  }
}

// ── Connection state ────────────────────────────────────────────────────────
export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error' | 'auth'

export interface ConnectionState {
  status: ConnectionStatus
  sessionId: string | null
  error: string | null
  lastPing: number | null
}

// ── Auth ────────────────────────────────────────────────────────────────────
export interface AuthToken {
  token: string
  expiresAt: number
}

// ── Settings ────────────────────────────────────────────────────────────────
export interface UserSettings {
  wsUrl: string
  httpUrl: string
  password: string
  theme: 'dark' | 'light'
  fontSize: 'sm' | 'md' | 'lg'
  sendOnEnter: boolean
  showTokenCounts: boolean
}

export const DEFAULT_SETTINGS: UserSettings = {
  wsUrl: typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:5001`
    : 'ws://localhost:5001',
  httpUrl: typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:5002`
    : 'http://localhost:5002',
  password: '',
  theme: 'dark',
  fontSize: 'md',
  sendOnEnter: true,
  showTokenCounts: false,
}
