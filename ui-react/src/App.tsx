import { useCallback, useEffect, useRef, useState } from 'react'
import { AuthScreen }      from '@/components/AuthScreen'
import { ChatInput }       from '@/components/ChatInput'
import { MessageBubble }   from '@/components/MessageBubble'
import { SettingsPanel }   from '@/components/SettingsPanel'
import { Sidebar }         from '@/components/Sidebar'
import { ThinkingIndicator } from '@/components/ThinkingIndicator'
import { useChat }         from '@/hooks/useChat'
import { useJuanWS }       from '@/hooks/useJuanWS'
import { useSettings }     from '@/hooks/useSettings'
import type { ChatMessage } from '@/types'

export default function App() {
  const { settings, update: updateSettings, reset: resetSettings } = useSettings()
  const [showSettings, setShowSettings] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const {
    messages, sessionId,
    addMessage, clearMessages, newSession,
    pushInputHistory, navigateHistory,
  } = useChat()

  const { connection, sendMessage, authenticate, disconnect, isThinking } = useJuanWS({
    settings,
    onMessage: addMessage,
  })

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isThinking])

  // Apply theme to <html>
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', settings.theme)
    document.documentElement.setAttribute('data-font-size', settings.fontSize)
  }, [settings.theme, settings.fontSize])

  const handleSend = useCallback(
    async (text: string) => {
      // Optimistic user message
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(), role: 'user',
        content: text, timestamp: Date.now(),
      }
      addMessage(userMsg)
      pushInputHistory(text)
      await sendMessage(text)
    },
    [addMessage, pushInputHistory, sendMessage]
  )

  const handleNewChat = useCallback(() => {
    newSession()
    clearMessages()
  }, [newSession, clearMessages])

  // Auth screen
  if (connection.status === 'auth') {
    return (
      <AuthScreen
        onAuth={authenticate}
        error={connection.error}
      />
    )
  }

  return (
    <div className="app-shell">
      <Sidebar
        connection={connection}
        onNewChat={handleNewChat}
        onSettings={() => setShowSettings(s => !s)}
      />

      <main className="chat-main" aria-label="Chat">
        {/* Empty state */}
        {messages.length === 0 && !isThinking && (
          <div className="empty-state" aria-hidden>
            <p className="empty-headline">What can I help with?</p>
            <p className="empty-sub">Session&nbsp;<code>{sessionId.slice(0, 8)}</code></p>
          </div>
        )}

        {/* Message list */}
        <div className="msg-list" role="log" aria-label="Conversation" aria-live="polite">
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              message={msg}
              showTokens={settings.showTokenCounts}
            />
          ))}
          {isThinking && <ThinkingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <ChatInput
          onSend={handleSend}
          onHistoryNav={navigateHistory}
          disabled={connection.status !== 'connected'}
          isThinking={isThinking}
          sendOnEnter={settings.sendOnEnter}
        />
      </main>

      {/* Settings panel */}
      {showSettings && (
        <SettingsPanel
          settings={settings}
          onUpdate={updateSettings}
          onClose={() => setShowSettings(false)}
          onReset={() => { resetSettings(); setShowSettings(false) }}
        />
      )}
    </div>
  )
}
