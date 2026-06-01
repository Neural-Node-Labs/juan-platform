import { useCallback, useReducer, useRef } from 'react'
import type { ChatMessage } from '@/types'

// ── State ─────────────────────────────────────────────────────────────────────
interface ChatState {
  messages: ChatMessage[]
  sessionId: string
  inputHistory: string[]   // up-arrow recall
  historyIndex: number
}

type ChatAction =
  | { type: 'ADD_MESSAGE';    message: ChatMessage }
  | { type: 'CLEAR' }
  | { type: 'NEW_SESSION' }
  | { type: 'PUSH_INPUT';     text: string }
  | { type: 'SET_HISTORY_IDX'; idx: number }

function newSessionId(): string {
  return crypto.randomUUID()
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'ADD_MESSAGE':
      return {
        ...state,
        messages: [...state.messages, action.message],
      }
    case 'CLEAR':
      return { ...state, messages: [] }
    case 'NEW_SESSION':
      return {
        ...state,
        messages: [],
        sessionId: newSessionId(),
        inputHistory: [],
        historyIndex: -1,
      }
    case 'PUSH_INPUT': {
      // Avoid consecutive duplicates
      const last = state.inputHistory[0]
      if (last === action.text) return state
      return {
        ...state,
        inputHistory: [action.text, ...state.inputHistory].slice(0, 100),
        historyIndex: -1,
      }
    }
    case 'SET_HISTORY_IDX':
      return { ...state, historyIndex: action.idx }
    default:
      return state
  }
}

// ── Hook ─────────────────────────────────────────────────────────────────────
export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, {
    messages: [],
    sessionId: newSessionId(),
    inputHistory: [],
    historyIndex: -1,
  })

  const addMessage = useCallback((msg: ChatMessage) => {
    dispatch({ type: 'ADD_MESSAGE', message: msg })
  }, [])

  const clearMessages = useCallback(() => {
    dispatch({ type: 'CLEAR' })
  }, [])

  const newSession = useCallback(() => {
    dispatch({ type: 'NEW_SESSION' })
  }, [])

  const pushInputHistory = useCallback((text: string) => {
    if (text.trim()) dispatch({ type: 'PUSH_INPUT', text })
  }, [])

  const navigateHistory = useCallback(
    (direction: 'up' | 'down'): string => {
      const { inputHistory, historyIndex } = state
      if (inputHistory.length === 0) return ''
      const next =
        direction === 'up'
          ? Math.min(historyIndex + 1, inputHistory.length - 1)
          : Math.max(historyIndex - 1, -1)
      dispatch({ type: 'SET_HISTORY_IDX', idx: next })
      return next === -1 ? '' : (inputHistory[next] ?? '')
    },
    [state]
  )

  return {
    messages:        state.messages,
    sessionId:       state.sessionId,
    addMessage,
    clearMessages,
    newSession,
    pushInputHistory,
    navigateHistory,
  }
}
