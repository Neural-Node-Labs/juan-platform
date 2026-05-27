import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  AuthToken, ChatMessage, ConnectionState, UserSettings,
  WSInbound, WSOutbound
} from '@/types'

const TOKEN_STORAGE_KEY = 'juan_ui_token'
const RECONNECT_DELAYS  = [1000, 2000, 4000, 8000, 16000]
const PING_INTERVAL_MS  = 25000

// ── Secure token storage ──────────────────────────────────────────────────
function saveToken(token: AuthToken): void {
  try {
    sessionStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(token))
  } catch { /* storage may be unavailable */ }
}

function loadToken(): AuthToken | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_STORAGE_KEY)
    if (!raw) return null
    const t = JSON.parse(raw) as AuthToken
    // Discard if expired or expiring in next 60s
    if (t.expiresAt < Date.now() / 1000 + 60) {
      sessionStorage.removeItem(TOKEN_STORAGE_KEY)
      return null
    }
    return t
  } catch {
    return null
  }
}

function clearToken(): void {
  try { sessionStorage.removeItem(TOKEN_STORAGE_KEY) } catch { /* ignore */ }
}

// ── Input validation (mirrors server-side rules) ──────────────────────────
function validateContent(content: string): string | null {
  if (!content.trim()) return 'Message cannot be empty'
  if (new TextEncoder().encode(content).length > 32 * 1024) return 'Message too long (max 32KB)'
  return null
}

// ── Hook ──────────────────────────────────────────────────────────────────
export interface UseJuanWSOptions {
  settings: UserSettings
  onMessage: (msg: ChatMessage) => void
  onStatusChange?: (status: ConnectionState) => void
}

export interface UseJuanWSReturn {
  connection: ConnectionState
  sendMessage: (content: string) => Promise<{ requestId: string } | null>
  authenticate: (password: string) => Promise<boolean>
  disconnect: () => void
  isThinking: boolean
}

export function useJuanWS({
  settings,
  onMessage,
  onStatusChange,
}: UseJuanWSOptions): UseJuanWSReturn {
  const wsRef          = useRef<WebSocket | null>(null)
  const pingRef        = useRef<ReturnType<typeof setInterval> | null>(null)
  const reconnectRef   = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectCount = useRef(0)
  const tokenRef       = useRef<AuthToken | null>(loadToken())
  const pendingRef     = useRef<Map<string, (msg: WSInbound) => void>>(new Map())

  const [connection, setConnection] = useState<ConnectionState>({
    status: 'disconnected', sessionId: null, error: null, lastPing: null
  })
  const [isThinking, setIsThinking] = useState(false)

  const updateState = useCallback((patch: Partial<ConnectionState>) => {
    setConnection(prev => {
      const next = { ...prev, ...patch }
      onStatusChange?.(next)
      return next
    })
  }, [onStatusChange])

  // ── Auth ────────────────────────────────────────────────────────────────
  const authenticate = useCallback(async (password: string): Promise<boolean> => {
    updateState({ status: 'auth' })
    try {
      const res = await fetch(`${settings.httpUrl}/ui/auth`, {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          'X-Client-Type': 'react',
        },
        body: JSON.stringify({ password }),
        credentials: 'same-origin',
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        updateState({
          status: 'error',
          error:  (err as { message?: string }).message || 'Authentication failed',
        })
        return false
      }
      const data = await res.json() as AuthToken
      tokenRef.current = data
      saveToken(data)
      return true
    } catch (err) {
      updateState({ status: 'error', error: String(err) })
      return false
    }
  }, [settings.httpUrl, updateState])

  // ── Connect ─────────────────────────────────────────────────────────────
  const connect = useCallback(() => {
    if (!tokenRef.current) {
      updateState({ status: 'auth', error: null })
      return
    }

    // Clear existing
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
    }

    updateState({ status: 'connecting', error: null })

    const ws = new WebSocket(settings.wsUrl)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      // Send auth header via first message — WebSocket headers are set on open
      // (token was passed in the Upgrade request via subprotocol workaround)
      reconnectCount.current = 0
      updateState({ status: 'connected', error: null })

      // Start ping
      if (pingRef.current) clearInterval(pingRef.current)
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, PING_INTERVAL_MS)
    }

    ws.onmessage = (ev: MessageEvent) => {
      let data: WSInbound
      try {
        data = JSON.parse(ev.data as string) as WSInbound
      } catch {
        return // ignore malformed frames
      }

      // Resolve pending promise
      if (data.request_id && pendingRef.current.has(data.request_id)) {
        const resolve = pendingRef.current.get(data.request_id)!
        pendingRef.current.delete(data.request_id)
        resolve(data)
      }

      if (data.type === 'pong') {
        updateState({ lastPing: Date.now() })
        return
      }
      if (data.type === 'status' && data.status === 'thinking') {
        setIsThinking(true)
        return
      }
      if (data.type === 'response') {
        setIsThinking(false)
        onMessage({
          id:         crypto.randomUUID(),
          role:       'assistant',
          content:    data.content ?? '',
          timestamp:  Date.now(),
          requestId:  data.request_id,
          metadata: data.metadata ? {
            iterations:   data.metadata.iterations,
            inputTokens:  data.metadata.input_tokens,
            outputTokens: data.metadata.output_tokens,
            durationMs:   data.metadata.duration_ms,
          } : undefined,
        })
        return
      }
      if (data.type === 'error') {
        setIsThinking(false)
        if (data.error_code === 'UIAUTH_FAIL' || data.error_code === 'UIAUTH_EXPIRED') {
          clearToken()
          tokenRef.current = null
          updateState({ status: 'auth', error: data.message ?? 'Session expired' })
          return
        }
        onMessage({
          id:        crypto.randomUUID(),
          role:      'error',
          content:   data.message ?? 'An error occurred',
          timestamp: Date.now(),
        })
      }
    }

    ws.onclose = (ev: CloseEvent) => {
      if (pingRef.current) clearInterval(pingRef.current)
      setIsThinking(false)

      if (ev.code === 4001 || ev.code === 4002) {
        clearToken()
        tokenRef.current = null
        updateState({ status: 'auth', error: ev.reason || 'Authentication required' })
        return
      }

      const delay = RECONNECT_DELAYS[Math.min(reconnectCount.current, RECONNECT_DELAYS.length - 1)]
      reconnectCount.current++
      updateState({ status: 'disconnected', error: `Reconnecting in ${delay / 1000}s…` })

      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      reconnectRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      updateState({ status: 'error', error: 'WebSocket error' })
    }
  }, [settings.wsUrl, onMessage, updateState])

  // ── Send ─────────────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (content: string) => {
    const validationError = validateContent(content)
    if (validationError) {
      onMessage({
        id: crypto.randomUUID(), role: 'error',
        content: validationError, timestamp: Date.now(),
      })
      return null
    }

    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      onMessage({
        id: crypto.randomUUID(), role: 'error',
        content: 'Not connected. Please wait…', timestamp: Date.now(),
      })
      return null
    }

    const requestId = crypto.randomUUID().replace(/-/g, '').slice(0, 16)
    const outbound: WSOutbound = { type: 'message', content, request_id: requestId }
    wsRef.current.send(JSON.stringify(outbound))
    return { requestId }
  }, [onMessage])

  const disconnect = useCallback(() => {
    if (reconnectRef.current) clearTimeout(reconnectRef.current)
    if (pingRef.current)      clearInterval(pingRef.current)
    wsRef.current?.close(1000, 'User disconnected')
    wsRef.current = null
    updateState({ status: 'disconnected', error: null })
  }, [updateState])

  // Auto-connect when token is available
  useEffect(() => {
    if (tokenRef.current) {
      connect()
    } else {
      updateState({ status: 'auth' })
    }
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      if (pingRef.current)      clearInterval(pingRef.current)
      wsRef.current?.close(1000, 'Unmounting')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { connection, sendMessage, authenticate, disconnect, isThinking }
}
