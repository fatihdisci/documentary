/**
 * The single component used to display any failure.
 *
 * Enforces the rule that an error always shows: what happened, what to do,
 * where the log is, and (collapsed) the technical detail.
 */

import { useState } from 'react'
import type { ApiErrorPayload } from '@/api/types'
import './ErrorBox.css'

interface Props {
  error: ApiErrorPayload
  onRetry?: () => void
  onDismiss?: () => void
}

export function ErrorBox({ error, onRetry, onDismiss }: Props) {
  const [showDetails, setShowDetails] = useState(false)

  return (
    <div className="error-box" role="alert">
      <div className="error-head">
        <span className="error-code">{error.code}</span>
        <p className="error-message">{error.message}</p>
      </div>

      {error.suggestion && (
        <p className="error-suggestion">
          <strong>Ne yapmalı:</strong> {error.suggestion}
        </p>
      )}

      {error.logPath && (
        <p className="error-log">
          <strong>Kayıt dosyası:</strong> <code>{error.logPath}</code>
        </p>
      )}

      <div className="error-actions">
        {onRetry && (
          <button onClick={onRetry} className="primary">
            Tekrar dene
          </button>
        )}
        {error.details && (
          <button onClick={() => setShowDetails((v) => !v)}>
            {showDetails ? 'Teknik ayrıntıları gizle' : 'Teknik ayrıntıları göster'}
          </button>
        )}
        {onDismiss && <button onClick={onDismiss}>Kapat</button>}
      </div>

      {showDetails && error.details && <pre className="error-details">{error.details}</pre>}
    </div>
  )
}
