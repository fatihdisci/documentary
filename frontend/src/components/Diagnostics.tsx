/**
 * Diagnostics page: shows measured facts about the environment.
 *
 * This is the first place a user looks when a render fails, so every check
 * shows its actual value and, when it is not OK, what to do about it.
 */

import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload, CheckStatus, DiagnosticsReport } from '@/api/types'
import { ErrorBox } from './ErrorBox'
import './Diagnostics.css'

const STATUS_LABEL: Record<CheckStatus, string> = {
  ok: 'Tamam',
  warn: 'Uyarı',
  fail: 'Sorun var',
}

export function Diagnostics() {
  const [report, setReport] = useState<DiagnosticsReport | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await api.diagnostics())
    } catch (err) {
      setError(describeError(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Sistem kontrolü</h1>
          <p className="page-subtitle">
            Video oluşturmak için gereken her şey bilgisayarınızda var mı, burada görürsünüz.
          </p>
        </div>
        <button onClick={() => void load()} disabled={loading}>
          {loading ? 'Kontrol ediliyor…' : 'Yeniden kontrol et'}
        </button>
      </header>

      {error && <ErrorBox error={error} onRetry={() => void load()} />}

      {loading && !report && <p className="muted">Kontrol ediliyor…</p>}

      {report && (
        <>
          <div className={`summary summary-${report.healthy ? 'ok' : 'fail'}`}>
            <strong>
              {report.healthy
                ? 'Her şey hazır, video oluşturabilirsiniz.'
                : 'Henüz hazır değil — aşağıdaki sorunları giderin.'}
            </strong>
            <span className="muted">
              Son kontrol: {new Date(report.generatedAt * 1000).toLocaleTimeString()}
            </span>
          </div>

          <table className="checks">
            <tbody>
              {report.checks.map((check) => (
                <tr key={check.id}>
                  <td className="check-status">
                    <span className={`badge badge-${check.status}`}>
                      {STATUS_LABEL[check.status]}
                    </span>
                  </td>
                  <td className="check-label">{check.label}</td>
                  <td className="check-value">
                    <div>{check.value}</div>
                    {check.detail && <div className="check-detail">{check.detail}</div>}
                    {check.suggestion && (
                      <div className="check-suggestion">→ {check.suggestion}</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {report.notes.length > 0 && (
            <section className="notes">
              <h2>Video motoru hakkında notlar</h2>
              {report.notes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
