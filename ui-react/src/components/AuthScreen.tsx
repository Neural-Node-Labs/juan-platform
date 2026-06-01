import { type FormEvent, useState } from 'react'
import { Bot, Lock, AlertCircle } from 'lucide-react'

interface AuthScreenProps {
  onAuth: (password: string) => Promise<boolean>
  error?: string | null
}

export function AuthScreen({ onAuth, error }: AuthScreenProps) {
  const [password, setPassword]   = useState('')
  const [loading,  setLoading]    = useState(false)
  const [localErr, setLocalErr]   = useState<string | null>(null)

  const displayError = localErr ?? error ?? null

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!password.trim()) return
    setLoading(true)
    setLocalErr(null)
    const ok = await onAuth(password)
    if (!ok) setLocalErr('Authentication failed. Check your password.')
    setLoading(false)
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-logo">
          <Bot size={40} strokeWidth={1.5} />
        </div>
        <h1 className="auth-title">Juan</h1>
        <p className="auth-sub">Autonomous Agent Platform</p>

        <form onSubmit={handleSubmit} className="auth-form" noValidate>
          <div className="auth-field">
            <Lock size={16} className="auth-field-icon" />
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Enter password"
              className="auth-input"
              autoFocus
              autoComplete="current-password"
              disabled={loading}
              maxLength={256}
              aria-label="Password"
            />
          </div>

          {displayError && (
            <div className="auth-error" role="alert">
              <AlertCircle size={14} />
              <span>{displayError}</span>
            </div>
          )}

          <button
            type="submit"
            className="auth-btn"
            disabled={loading || !password.trim()}
          >
            {loading ? <span className="spinner-sm" /> : 'Connect'}
          </button>
        </form>

        <p className="auth-hint">
          {password === '' ? 'No password configured? Leave blank and connect.' : ''}
        </p>
      </div>
    </div>
  )
}
