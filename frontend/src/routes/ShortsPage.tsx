/**
 * Shorts: cut a vertical 9:16 clip out of a finished long render.
 *
 * Nothing on this page re-renders a scene. It picks spans out of an MP4 that is
 * already finished, so the narration, music, burned-in subtitles and in-scene
 * transitions come through exactly as they were mixed.
 */

import { useEffect, useMemo, useState } from 'react'
import type { ShortJob, ShortPhase, ShortTimelineSection } from '@/api/shorts-types'
import { useProjectStore } from '@/store/project'
import { useShortsStore } from '@/store/shorts'
import { formatTimecode, frameAt, resolvePlan } from '@/lib/shortsPlan'
import { ErrorBox } from '@/components/ErrorBox'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import './ShortsPage.css'

const PHASE_LABEL: Record<ShortPhase, string> = {
  'validate-source': 'Checking the source render',
  plan: 'Preparing the cut list',
  'cut-segments': 'Cutting the selected sections',
  concat: 'Joining the cuts',
  compose: 'Building the vertical frame',
  'validate-output': 'Validating the Short',
  publish: 'Publishing',
  cleanup: 'Cleaning up',
}

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

function StatusPill({ status }: { status: ShortJob['status'] }) {
  return <span className={`status-pill status-${status}`}>{status}</span>
}

/** A number input that only commits a value the backend would also accept. */
function TrimInput({
  label,
  value,
  low,
  high,
  fps,
  describedBy,
  onCommit,
}: {
  label: string
  value: number
  low: number
  high: number
  fps: number
  describedBy: string
  onCommit: (next: number) => void
}) {
  const [draft, setDraft] = useState(value.toFixed(2))
  useEffect(() => setDraft(value.toFixed(2)), [value])

  return (
    <label className="trim-field">
      <span className="trim-label">{label}</span>
      <input
        type="number"
        step={1 / fps}
        min={low}
        max={high}
        value={draft}
        aria-label={label}
        aria-describedby={describedBy}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const parsed = Number(draft)
          if (!Number.isFinite(parsed)) {
            setDraft(value.toFixed(2))
            return
          }
          onCommit(Math.min(Math.max(parsed, low), high))
        }}
      />
      <span className="trim-hint">frame {frameAt(value, fps)}</span>
    </label>
  )
}

export function ShortsPage() {
  const { project } = useProjectStore()
  const {
    sources, selectedRenderId, timeline, selection, preflight, job, event, history,
    error, loading, busy,
    loadSources, selectSource, toggleSection, moveSelection, removeSelection,
    setTrim, refreshPreflight, start, cancel, retry, detach, reattachIfRunning,
    loadHistory, remove, clearError,
  } = useShortsStore()

  const slug = project?.slug ?? null
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)

  useEffect(() => {
    if (!slug) return
    void loadSources(slug)
    void loadHistory(slug)
    void reattachIfRunning(slug)
    return () => detach()
  }, [slug, loadSources, loadHistory, reattachIfRunning, detach])

  const plan = useMemo(
    () =>
      timeline
        ? resolvePlan(timeline.sections, selection, timeline.minClipSeconds)
        : { segments: [], groups: [], totalSeconds: 0 },
    [timeline, selection],
  )
  const orderOf = useMemo(() => {
    const map = new Map<string, number>()
    selection.forEach((item, index) => map.set(item.unitId, index + 1))
    return map
  }, [selection])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Shorts</h1>
        <p className="page-subtitle">Open a project first.</p>
      </div>
    )
  }

  const source = sources.find((entry) => entry.renderId === selectedRenderId) ?? null
  const fps = timeline?.fps ?? 30
  const total = preflight?.totalDurationSeconds ?? plan.totalSeconds
  const maxSeconds = timeline?.maxSeconds ?? 180
  const warnSeconds = timeline?.warnSeconds ?? 60
  const bandLow = timeline?.recommendedMinSeconds ?? 25
  const bandHigh = timeline?.recommendedMaxSeconds ?? 50

  const running = job !== null && (job.status === 'queued' || job.status === 'running')
  const live = event
  const percent = Math.round((live?.progress ?? job?.progress ?? 0) * 100)

  const overMaximum = total > maxSeconds
  const overWarn = total > warnSeconds
  const withinBand = total >= bandLow && total <= bandHigh
  const shortClips = plan.segments.filter((segment) => segment.tooShort)
  const canRender =
    !!source?.usable &&
    selection.length > 0 &&
    !overMaximum &&
    shortClips.length === 0 &&
    !running &&
    !busy

  return (
    <div className="page shorts-page">
      <header className="page-header">
        <div>
          <h1>Shorts</h1>
          <p className="page-subtitle">
            Cuts a 1080×1920 vertical MP4 from a finished render — the picture centred on black
            at its original 16:9, with the mixed audio and burned-in subtitles carried through
            untouched.
          </p>
        </div>
        <div className="header-actions">
          {running ? (
            <button className="danger" onClick={() => void cancel()}>
              Cancel Short
            </button>
          ) : (
            <button
              className="primary"
              onClick={() => void start(slug)}
              disabled={!canRender}
              title={
                selection.length === 0
                  ? 'Select at least one section first'
                  : overMaximum
                    ? `Over the ${maxSeconds}-second Shorts limit`
                    : 'Build the Short'
              }
            >
              {busy ? 'Starting…' : 'Render Short'}
            </button>
          )}
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {/* --- 1. source render --------------------------------------------- */}
      <section className="card">
        <div className="section-head">
          <h2>Source render</h2>
          <button onClick={() => void loadSources(slug)} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>

        {sources.length === 0 ? (
          <p className="muted empty-note">
            No completed render yet. Render the long video on the Export tab first — a Short is
            always cut from a finished export.
          </p>
        ) : (
          <div className="source-grid" role="radiogroup" aria-label="Source render">
            {sources.map((entry) => (
              <button
                key={entry.renderId}
                type="button"
                role="radio"
                aria-checked={entry.renderId === selectedRenderId}
                aria-label={`${entry.filename}, ${formatDuration(entry.durationSeconds)}, ${entry.quality}`}
                className={`source-card ${entry.renderId === selectedRenderId ? 'selected' : ''} ${
                  entry.usable ? '' : 'unusable'
                }`}
                disabled={!entry.usable}
                onClick={() => void selectSource(slug, entry.renderId)}
              >
                <span className="source-thumb">
                  {entry.thumbnailUrl ? (
                    <img src={entry.thumbnailUrl} alt="" loading="lazy" />
                  ) : (
                    <span className="source-thumb-placeholder" aria-hidden="true">
                      ▦
                    </span>
                  )}
                </span>
                <span className="source-body">
                  <span className="source-name">{entry.filename}</span>
                  <span className="source-meta">
                    {new Date(entry.createdAt).toLocaleString()} ·{' '}
                    {formatDuration(entry.durationSeconds)} · {entry.quality} ·{' '}
                    {formatBytes(entry.sizeBytes)}
                  </span>
                  <span className="source-meta">
                    {entry.width}×{entry.height} @ {entry.fps} fps · {entry.sectionCount} sections
                  </span>
                  {entry.issue && <span className="source-issue">⚠ {entry.issue}</span>}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* --- 2. sections --------------------------------------------------- */}
      <section className="card">
        <div className="section-head">
          <h2>Sections</h2>
          {selection.length > 0 && (
            <span className="muted">
              {selection.length} selected · click order decides playback order
            </span>
          )}
        </div>

        {!timeline ? (
          <p className="muted empty-note">
            Select a completed render above to choose sections from it.
          </p>
        ) : (
          <div className="section-list">
            {timeline.sections.map((section) => (
              <SectionCard
                key={section.unitId}
                section={section}
                fps={fps}
                order={orderOf.get(section.unitId) ?? null}
                selection={selection.find((item) => item.unitId === section.unitId) ?? null}
                total={timeline.totalDurationSeconds}
                minClipSeconds={timeline.minClipSeconds}
                onToggle={() => toggleSection(slug, section.unitId)}
                onMove={(direction) => moveSelection(slug, section.unitId, direction)}
                onRemove={() => removeSelection(slug, section.unitId)}
                onTrim={(edge, value) => setTrim(slug, section.unitId, edge, value)}
              />
            ))}
          </div>
        )}
      </section>

      {/* --- 3. duration and preview -------------------------------------- */}
      {selection.length > 0 && timeline && (
        <section className="card">
          <h2>Preview</h2>

          <div className="duration-row">
            <div className="duration-readout">
              <span className={`duration-value ${overMaximum ? 'bad' : overWarn ? 'warn' : ''}`}>
                {total.toFixed(1)}s
              </span>
              <span className="label">
                total · {plan.groups.length} cut{plan.groups.length === 1 ? '' : 's'}
              </span>
            </div>
            <div
              className="duration-meter"
              role="meter"
              aria-valuenow={Math.round(total)}
              aria-valuemin={0}
              aria-valuemax={Math.round(maxSeconds)}
              aria-label="Total Short duration"
            >
              <span
                className="band"
                style={{
                  left: `${(bandLow / maxSeconds) * 100}%`,
                  width: `${((bandHigh - bandLow) / maxSeconds) * 100}%`,
                }}
                title={`Recommended ${bandLow}–${bandHigh}s`}
              />
              <span className="tick warn" style={{ left: `${(warnSeconds / maxSeconds) * 100}%` }} />
              <span
                className={`fill ${overMaximum ? 'bad' : overWarn ? 'warn' : withinBand ? 'good' : ''}`}
                style={{ width: `${Math.min(100, (total / maxSeconds) * 100)}%` }}
              />
            </div>
            <span className="muted duration-scale">
              recommended {bandLow}–{bandHigh}s · {maxSeconds}s max
            </span>
          </div>

          {overMaximum && (
            <div className="blocking">
              <strong>Too long to be a Short.</strong>
              <p>
                YouTube only treats vertical or square video up to {maxSeconds / 60} minutes as a
                Short. Deselect a section or trim the selection down.
              </p>
            </div>
          )}
          {!overMaximum && overWarn && (
            <div className="warnings">
              <p>
                ⚠ Over {warnSeconds} seconds. A Short longer than a minute can be blocked worldwide
                if any music in it has an active Content ID claim. Fine when the music is yours or
                licensed — otherwise keep it under a minute.
              </p>
            </div>
          )}
          {shortClips.length > 0 && (
            <div className="blocking">
              <strong>A clip is too short.</strong>
              {shortClips.map((segment) => (
                <p key={segment.unitId}>
                  Section {segment.number} — {segment.title} is{' '}
                  {segment.durationSeconds.toFixed(2)}s, under the{' '}
                  {timeline.minClipSeconds.toFixed(1)}s minimum.
                </p>
              ))}
            </div>
          )}

          <div className="preview-row">
            <div className="canvas-preview" aria-label="1080×1920 output preview">
              <div className="canvas-frame">
                <div className="canvas-fit">
                  {preflight?.previewFrames?.[0] ? (
                    <img src={preflight.previewFrames[0].url} alt="First frame of the Short" />
                  ) : (
                    <span className="canvas-placeholder">16:9</span>
                  )}
                </div>
              </div>
              <span className="canvas-caption">1080 × 1920 · black · centred fit</span>
            </div>

            <div className="preview-strip">
              <p className="muted preview-note">
                {plan.groups.length === 1
                  ? 'One continuous cut — every transition inside it is preserved exactly as rendered.'
                  : 'Separate cuts join with hard cuts. Nothing is faded or added that the source does not already contain.'}
              </p>
              <div className="strip">
                {plan.groups.map((group) => {
                  const frame = preflight?.previewFrames?.find((f) => f.groupIndex === group.index)
                  return (
                    <div
                      key={group.index}
                      className="strip-cut"
                      style={{ flexGrow: Math.max(0.2, group.durationSeconds) }}
                      title={`${formatTimecode(group.startSeconds)} → ${formatTimecode(group.endSeconds)}`}
                    >
                      {frame ? (
                        <img src={frame.url} alt="" loading="lazy" />
                      ) : (
                        <span className="strip-blank" aria-hidden="true" />
                      )}
                      <span className="strip-caption">
                        {group.numbers.join(' + ')} · {group.durationSeconds.toFixed(1)}s
                        {group.preservedTransitions > 0 && (
                          <em> · {group.preservedTransitions} transition kept</em>
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
              {preflight?.cachedShortId && (
                <p className="cached-note">
                  ✓ An identical Short already exists — rendering will reuse it instead of
                  encoding again.
                </p>
              )}
              {preflight?.blockingIssues?.map((issue) => (
                <p key={issue} className="preview-blocking">
                  ✕ {issue}
                </p>
              ))}
              {preflight?.warnings
                ?.filter((warning) => !warning.includes('Content ID'))
                .map((warning) => (
                  <p key={warning} className="muted">
                    ⚠ {warning}
                  </p>
                ))}
              <button
                className="refresh-preview"
                onClick={() => void refreshPreflight(slug)}
                disabled={running}
              >
                Refresh preview
              </button>
            </div>
          </div>
        </section>
      )}

      {/* --- 4. live progress --------------------------------------------- */}
      {running && job && (
        <section className="card render-live">
          <div className="render-head">
            <h2>{PHASE_LABEL[live?.phase ?? job.phase] ?? 'Building the Short'}</h2>
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

      {/* --- 5. last result ------------------------------------------------ */}
      {job && !running && (
        <section className={`card render-result render-${job.status}`}>
          <div className="render-head">
            <h2>
              {job.status === 'completed'
                ? job.cacheReused
                  ? 'Reused an identical Short'
                  : 'Short complete'
                : job.status === 'cancelled'
                  ? 'Short cancelled'
                  : job.status === 'interrupted'
                    ? 'Short interrupted'
                    : 'Short failed'}
            </h2>
            <StatusPill status={job.status} />
          </div>

          {job.status === 'completed' && (
            <>
              <p className="muted">
                {job.outputFile} · {job.totalDurationSeconds.toFixed(1)}s ·{' '}
                {job.segmentCount} section{job.segmentCount === 1 ? '' : 's'} in{' '}
                {job.groupCount} cut{job.groupCount === 1 ? '' : 's'}
                {job.cacheReused && ' · nothing was re-encoded'}
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
                suggestion: job.errorSuggestion ?? 'Check the Short log for details.',
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

      {/* --- 6. history ---------------------------------------------------- */}
      {history.length > 0 && (
        <section className="card">
          <h2>Shorts in this project</h2>
          <table className="history-table shorts-history">
            <thead>
              <tr>
                <th scope="col">Sections</th>
                <th scope="col">File</th>
                <th scope="col">From</th>
                <th scope="col">Length</th>
                <th scope="col">Created</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {history.map((entry) => (
                <tr key={entry.shortId}>
                  <td className="history-sections">{entry.sectionNumbers.join(' → ') || '—'}</td>
                  <td className="history-file">{entry.filename}</td>
                  <td className="muted">{entry.sourceVideo}</td>
                  <td className="muted">{entry.durationSeconds.toFixed(1)}s</td>
                  <td className="muted">{new Date(entry.createdAt).toLocaleString()}</td>
                  <td className="history-actions">
                    <a
                      className="button-link"
                      href={entry.url}
                      download
                      aria-label={`Download ${entry.filename}`}
                    >
                      Download
                    </a>
                    <button
                      className="danger"
                      onClick={() => setPendingDelete(entry.shortId)}
                      aria-label={`Delete ${entry.filename}`}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this Short?"
          body={
            <p>
              Only this Short and its side-car files are removed. The long render it was cut
              from, the project and every other Short are left alone.
            </p>
          }
          confirmLabel="Delete Short"
          destructive
          onConfirm={() => {
            const id = pendingDelete
            setPendingDelete(null)
            void remove(slug, id)
          }}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  )
}

function SectionCard({
  section,
  fps,
  order,
  selection,
  total,
  minClipSeconds,
  onToggle,
  onMove,
  onRemove,
  onTrim,
}: {
  section: ShortTimelineSection
  fps: number
  order: number | null
  selection: { startSeconds: number | null; endSeconds: number | null } | null
  total: number
  minClipSeconds: number
  onToggle: () => void
  onMove: (direction: -1 | 1) => void
  onRemove: () => void
  onTrim: (edge: 'start' | 'end', value: number | null) => void
}) {
  const selected = selection !== null
  const start = selection?.startSeconds ?? section.safeStartSeconds
  const end = selection?.endSeconds ?? section.safeEndSeconds
  const length = Math.max(0, end - start)
  const hintId = `safe-${section.unitId}`

  return (
    <div className={`section-card ${selected ? 'selected' : ''}`}>
      <label className="section-pick">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={`Include section ${section.number}, ${section.title}`}
        />
        <span className="section-number">{section.number}</span>
        {order !== null && (
          <span className="order-badge" aria-label={`Position ${order} in the Short`}>
            {order}
          </span>
        )}
      </label>

      <div className="section-body">
        <div className="section-title-row">
          <span className="section-title">{section.title}</span>
          <span className="section-kind">{section.kind}</span>
        </div>
        <div className="section-times">
          {formatTimecode(section.safeStartSeconds)} – {formatTimecode(section.safeEndSeconds)} ·{' '}
          {section.safeDurationSeconds.toFixed(1)}s usable
          {section.transitionDurationSeconds > 0 && (
            <em> · {section.transitionDurationSeconds.toFixed(2)}s transition after</em>
          )}
        </div>
        <div className="section-track" aria-hidden="true">
          <span
            className="section-span"
            style={{
              left: `${(section.startSeconds / Math.max(total, 1)) * 100}%`,
              width: `${(section.durationSeconds / Math.max(total, 1)) * 100}%`,
            }}
          />
          {selected && (
            <span
              className="section-chosen"
              style={{
                left: `${(start / Math.max(total, 1)) * 100}%`,
                width: `${(Math.max(length, 0.05) / Math.max(total, 1)) * 100}%`,
              }}
            />
          )}
        </div>

        {selected && (
          <div className="section-trim">
            <TrimInput
              label="Start"
              value={start}
              low={section.safeStartSeconds}
              high={section.safeEndSeconds}
              fps={fps}
              describedBy={hintId}
              onCommit={(next) => onTrim('start', next)}
            />
            <TrimInput
              label="End"
              value={end}
              low={section.safeStartSeconds}
              high={section.safeEndSeconds}
              fps={fps}
              describedBy={hintId}
              onCommit={(next) => onTrim('end', next)}
            />
            <span
              className={`trim-length ${length < minClipSeconds ? 'bad' : ''}`}
              id={hintId}
            >
              {length.toFixed(2)}s · trimmable between{' '}
              {section.safeStartSeconds.toFixed(2)}s and {section.safeEndSeconds.toFixed(2)}s at{' '}
              {fps} fps
            </span>
            <div className="section-controls">
              <button onClick={() => onMove(-1)} aria-label={`Move ${section.title} earlier`}>
                ↑
              </button>
              <button onClick={() => onMove(1)} aria-label={`Move ${section.title} later`}>
                ↓
              </button>
              <button onClick={onRemove} aria-label={`Remove ${section.title} from the Short`}>
                Remove
              </button>
              <button
                onClick={() => {
                  onTrim('start', null)
                  onTrim('end', null)
                }}
                aria-label={`Reset the trim on ${section.title}`}
              >
                Reset trim
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
