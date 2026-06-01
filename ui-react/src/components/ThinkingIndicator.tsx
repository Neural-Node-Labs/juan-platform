export function ThinkingIndicator() {
  return (
    <div className="msg-row msg-row--assistant" aria-label="Juan is thinking" aria-live="polite">
      <div className="msg-avatar" aria-hidden>J</div>
      <div className="msg-bubble msg-bubble--assistant thinking-bubble">
        <span className="thinking-dot" style={{ animationDelay: '0ms' }} />
        <span className="thinking-dot" style={{ animationDelay: '160ms' }} />
        <span className="thinking-dot" style={{ animationDelay: '320ms' }} />
      </div>
    </div>
  )
}
