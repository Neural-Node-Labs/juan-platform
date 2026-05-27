import { useEffect, useRef } from 'react'
import { AlertCircle, Copy, Check } from 'lucide-react'
import { useState } from 'react'
import { renderMarkdown } from '@/lib/markdown'
import type { ChatMessage } from '@/types'

interface MessageBubbleProps {
  message: ChatMessage
  showTokens: boolean
}

export function MessageBubble({ message, showTokens }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)

  // Render markdown into the div safely
  useEffect(() => {
    if (contentRef.current && message.role === 'assistant') {
      contentRef.current.innerHTML = renderMarkdown(message.content)
    }
  }, [message.content, message.role])

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard may not be available */ }
  }

  const ts = new Date(message.timestamp).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit'
  })

  if (message.role === 'error') {
    return (
      <div className="msg-error" role="alert">
        <AlertCircle size={14} />
        <span>{message.content}</span>
      </div>
    )
  }

  if (message.role === 'user') {
    return (
      <div className="msg-row msg-row--user">
        <div className="msg-bubble msg-bubble--user">
          <p className="msg-text">{message.content}</p>
          <span className="msg-time">{ts}</span>
        </div>
      </div>
    )
  }

  // assistant
  return (
    <div className="msg-row msg-row--assistant">
      <div className="msg-avatar" aria-hidden>J</div>
      <div className="msg-bubble msg-bubble--assistant">
        <div
          ref={contentRef}
          className="msg-text msg-text--md"
          aria-live="polite"
        >
          {/* innerHTML set by useEffect */}
          {message.role !== 'assistant' && message.content}
        </div>

        <div className="msg-footer">
          <span className="msg-time">{ts}</span>
          {showTokens && message.metadata && (
            <span className="msg-tokens">
              {(message.metadata.inputTokens ?? 0) + (message.metadata.outputTokens ?? 0)} tok
              {message.metadata.durationMs != null &&
                ` · ${(message.metadata.durationMs / 1000).toFixed(1)}s`}
            </span>
          )}
          <button
            className="msg-copy"
            onClick={handleCopy}
            aria-label="Copy message"
            title="Copy"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>
    </div>
  )
}
