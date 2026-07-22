import { useEffect } from 'react'
import type { JobPhase, RenderJob } from '@/api/render-types'
import type { QualityPreset } from '@/api/types'
import { useProjectStore } from '@/store/project'
import { useRenderStore } from '@/store/render'
import { ErrorBox } from '@/components/ErrorBox'
import './ExportPage.css'

const PHASE_LABEL: Record<JobPhase, string> = {
  validate: 'Checking the project',
  'verify-sources': 'Verifying images and audio',
  'generate-tts': 'Generating narration',
  'probe-audio': 'Measuring audio',
  'compute-timeline': 'Computing the timeline',
  'build-subtitles': 'Building subtitles',
  'normalize-images': 'Preparing images',
  'render-text-cards': 'Rendering text',
  'render-scene-clips': 'Rendering scenes',
  assemble: 'Joining scenes',
  'mix-audio': 'Mixing audio',
  encode: 'Encoding the final video',
  'validate-output': 'Validating the export',
  'write-artifacts': 'Writing exports',
  cleanup: 'Cleaning up',
}

const QUALITIES: { id: QualityPreset; label: string; hint: string }[] = [
  { id: 'youtube-hq', label: 'YouTube high quality', hint: 'Best for upload. Slowest.' },
  { id: 'high', label: 'High', hint: 'High quality, a little faster.' },
  { id: 'standard', label: 'Standard', hint: 'Good quality, quicker.' },
  { id: 'preview', label: 'Preview', hint: 'Fast and rough. For checking timing only.' },
]

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`
}

function formatBytes(bytes: number): string {
  if (bytes > 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`
  if (bytes > 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

function StatusPill({ status }: { status: RenderJob['status'] }) {
  return <span className={`status-pill status-${status}`}>{status}</span>
}

export function ExportPage() {
  const { project, edit } = useProjectStore()
  const {
    preflight, job, event, history, exports, error, busy,
    loadPreflight, loadHistory, start, cancel, retry, detach, reattachIfRunning, clearError,
  } = useRenderStore()

  const slug = project?.slug ?? null

  useEffect(() => {
    if (!slug) return
    void loadPreflight(slug)
    void loadHistory(slug)
    void reattachIfRunning(slug)
    return () => detach()
  }, [slug, loadPreflight, loadHistory, reattachIfRunning, detach])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Export</h1>
        <p className="page-subtitle">Open a project first.</p>
      </div>
    )
  }

  const running = job !== null && (job.status === 'queued' || job.status === 'running')
  const live = event
  const percent = Math.round((live?.progress ?? job?.progress ?? 0) * 100)

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Export</h1>
          <p className="page-subtitle">
            Renders a 1920×1080, constant 60 FPS MP4 plus subtitles and side-car files.
          </p>
        </div>
        <div className="header-actions">
          {running ? (
            <button className="danger" onClick={() => void cancel()}>
              Cancel render
            </button>
          ) : (
            <button
              className="primary"
              onClick={() => void start(slug)}
              disabled={busy || !preflight?.ready}
              title={preflight?.ready ? 'Start rendering' : 'Resolve the blocking issues first'}
            >
              {busy ? 'Starting…' : 'Render video'}
            </button>
          )}
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {/* --- live progress --- */}
      {running && (
        <section className="card render-live">
          <div className="render-head">
            <h2>{PHASE_LABEL[live?.phase ?? job.phase] ?? 'Rendering'}</h2>
            <StatusPill status={job.status} />
          </div>
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <div className="render-meta">
            <span className="percent">{percent}%</span>
            <span>{live?.message ?? job.message}</span>
            <span className="spacer" />
            <span>Elapsed {formatDuration(live?.elapsedSeconds ?? 0)}</span>
            {live?.estimatedRemainingSeconds != null && (
              <span>~{formatDuration(live.estimatedRemainingSeconds)} left</span>
            )}
          </div>
        </section>
      )}

      {/* --- last finished render --- */}
      {job && !running && (
        <section className={`card render-result render-${job.status}`}>
          <div className="render-head">
            <h2>
              {job.status === 'completed'
                ? 'Render complete'
                : job.status === 'cancelled'
                  ? 'Render cancelled'
                  : job.status === 'interrupted'
                    ? 'Render interrupted'
                    : 'Render failed'}
            </h2>
            <StatusPill status={job.status} />
          </div>

          {job.status === 'completed' && (
            <>
              <p className="muted">
                {job.outputFile} · {formatDuration(job.totalDurationSeconds)} ·{' '}
                {job.scenesRendered} scene{job.scenesRendered === 1 ? '' : 's'} rendered,{' '}
                {job.scenesReused} reused from cache
              </p>
              <div className="artifact-list">
                {job.artifacts.map((artifact) => (
                  <a key={artifact.url} className="artifact" href={artifact.url} download>
                    <span className="artifact-kind">{artifact.kind}</span>
                    <span className="artifact-name">{artifact.filename}</span>
                    <span className="artifact-size">{formatBytes(artifact.sizeBytes)}</span>
                  </a>
                ))}
              </div>
            </>
          )}

          {job.errorMessage && (
            <ErrorBox
              error={{
                code: job.errorCode ?? 'render_failed',
                message: job.errorMessage,
                details: job.errorDetails,
                suggestion: job.errorSuggestion ?? 'Check the render log for details.',
                logPath: job.logFile,
                context: {},
              }}
              onRetry={() => void retry(job.id)}
            />
          )}

          {job.warnings.length > 0 && (
            <div className="warnings">
              {job.warnings.map((warning) => (
                <p key={warning}>⚠ {warning}</p>
              ))}
            </div>
          )}
        </section>
      )}

      {/* --- preflight --- */}
      {preflight && !running && (
        <section className="card">
          <h2>Before rendering</h2>

          {preflight.blockingIssues.length > 0 ? (
            <div className="blocking">
              <strong>These must be fixed first:</strong>
              {preflight.blockingIssues.map((issue) => (
                <p key={issue}>✕ {issue}</p>
              ))}
            </div>
          ) : (
            <p className="ready-note">✓ Everything needed for a render is in place.</p>
          )}

          {preflight.warnings.length > 0 && (
            <div className="warnings">
              {preflight.warnings.map((warning) => (
                <p key={warning}>⚠ {warning}</p>
              ))}
            </div>
          )}

          <div className="timing-grid">
            <div className="timing-stat big">
              <span className="value">{String(preflight.timing.totalFormatted ?? '—')}</span>
              <span className="label">Final runtime</span>
            </div>
            <div className="timing-stat">
              <span className="value">{Number(preflight.timing.sceneCount ?? 0)}</span>
              <span className="label">Scenes</span>
            </div>
            <div className="timing-stat">
              <span className="value">
                {Number(preflight.timing.transitionSeconds ?? 0).toFixed(1)}s
              </span>
              <span className="label">Transition overlap</span>
            </div>
            <div className="timing-stat">
              <span className="value">
                {Number(preflight.disk.totalMb ?? 0) > 1024
                  ? `${(Number(preflight.disk.totalMb) / 1024).toFixed(1)} GB`
                  : `${Number(preflight.disk.totalMb ?? 0).toFixed(0)} MB`}
              </span>
              <span className="label">Disk needed</span>
            </div>
            <div className="timing-stat">
              <span className="value">~{formatDuration(preflight.estimatedRenderSeconds)}</span>
              <span className="label">Est. render time</span>
            </div>
          </div>
        </section>
      )}

      {/* --- options --- */}
      <section className="card">
        <h2>Output</h2>
        <div className="quality-options">
          {QUALITIES.map((quality) => (
            <label
              key={quality.id}
              className={`quality ${project.export.quality === quality.id ? 'selected' : ''}`}
            >
              <input
                type="radio"
                name="quality"
                checked={project.export.quality === quality.id}
                onChange={() => edit((d) => void (d.export.quality = quality.id))}
                disabled={running}
              />
              <span className="quality-label">{quality.label}</span>
              <span className="hint">{quality.hint}</span>
            </label>
          ))}
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.export.useHardwareEncoder}
            onChange={(e) => edit((d) => void (d.export.useHardwareEncoder = e.target.checked))}
            disabled={running}
          />
          Use the hardware encoder
          <span className="hint">
            Much faster, but software encoding gives better quality per megabyte.
          </span>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.subtitles.burnIn}
            onChange={(e) => edit((d) => void (d.subtitles.burnIn = e.target.checked))}
            disabled={running}
          />
          Burn subtitles into the picture
          <span className="hint">
            On by default. A separate .srt is always exported too; turn this off for a clean
            image, which YouTube prefers.
          </span>
        </label>
      </section>

      {/* --- history --- */}
      {history.length > 0 && (
        <section className="card">
          <h2>Render history</h2>
          <table className="history-table">
            <tbody>
              {history.map((entry) => (
                <tr key={entry.id}>
                  <td><StatusPill status={entry.status} /></td>
                  <td className="history-file">{entry.outputFile ?? '—'}</td>
                  <td className="muted">{new Date(entry.createdAt).toLocaleString()}</td>
                  <td className="muted">{formatDuration(entry.totalDurationSeconds)}</td>
                  <td className="history-actions">
                    {entry.status !== 'completed' && (
                      <button onClick={() => void retry(entry.id)} disabled={running || busy}>
                        Retry
                      </button>
                    )}
                    {entry.logFile && (
                      <a className="button-link" href={`/api/jobs/${entry.id}/log`} download>
                        Log
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {exports.length > 0 && (
        <section className="card">
          <h2>Files on disk</h2>
          <div className="artifact-list">
            {exports.slice(0, 20).map((entry) => (
              <a key={entry.url} className="artifact" href={entry.url} download>
                <span className="artifact-name">{entry.filename}</span>
                <span className="artifact-size">{formatBytes(entry.sizeBytes)}</span>
              </a>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
