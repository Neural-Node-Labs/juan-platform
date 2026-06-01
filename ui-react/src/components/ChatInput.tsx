import {
  type KeyboardEvent,
  type ChangeEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { Send, Square } from 'lucide-react'

interface ChatInputProps {
  onSend: (text: string) => void
  onHistoryNav: (dir: 'up' | 'down') => string
  disabled: boolean
  isThinking: boolean
  sendOnEnter: boolean
}

const MAX_CHARS = 8000

export function ChatInput({
  onSend,
  onHistoryNav,
  disabled,
  isThinking,
  sendOnEnter,
}: ChatInputProps) {
  const [text, setText]  = useState('')
  const textareaRef      = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`
  }, [text])

  // Focus on mount
  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const submit = useCallback(() => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    // Reset height
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }, [text, disabled, onSend])

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter') {
      if (sendOnEnter && !e.shiftKey) {
        e.preventDefault()
        submit()
        return
      }
    }
    if (e.key === 'ArrowUp' && text === '') {
      e.preventDefault()
      setText(onHistoryNav('up'))
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      const val = onHistoryNav('down')
      setText(val)
    }
  }

  function handleChange(e: ChangeEvent<HTMLTextAreaElement>) {
    const val = e.target.value
    if (val.length <= MAX_CHARS) setText(val)
  }

  const charPct = text.length / MAX_CHARS
  const nearLimit = charPct > 0.8

  return (
    <div className="chat-input-wrap">
      <div className={`chat-input-box ${disabled ? 'chat-input-box--disabled' : ''}`}>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Connecting…' : 'Message Juan  (↑ for history)'}
          className="chat-textarea"
          disabled={disabled}
          rows={1}
          aria-label="Message input"
          aria-describedby="char-count"
          spellCheck
        />
        <div className="chat-input-actions">
          {nearLimit && (
            <span
              id="char-count"
              className={`char-count ${charPct > 0.95 ? 'char-count--critical' : ''}`}
              aria-live="polite"
            >
              {MAX_CHARS - text.length}
            </span>
          )}
          <button
            className={`send-btn ${isThinking ? 'send-btn--stop' : ''}`}
            onClick={submit}
            disabled={disabled || (!isThinking && !text.trim())}
            aria-label={isThinking ? 'Stop' : 'Send message'}
            title={isThinking ? 'Stop' : sendOnEnter ? 'Send (Enter)' : 'Send (Ctrl+Enter)'}
          >
            {isThinking ? <Square size={16} /> : <Send size={16} />}
          </button>
        </div>
      </div>
      {!sendOnEnter && (
        <p className="input-hint">Shift+Enter for new line · Ctrl+Enter to send</p>
      )}
    </div>
  )
}
