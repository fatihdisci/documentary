import { useEffect } from 'react'
import type { JobPhase, RenderJob } from '@/api/render-types'
import type { QualityPreset } from '@/api/types'
import { useProjectStore } from '@/store/project'
import { useRenderStore } from '@/store/render'
import { ErrorBox } from '@/components/ErrorBox'
import './ExportPage.css'

const PHASE_LABEL: Record<JobPhase, string> = {
  validate: 'Proje kontrol ediliyor',
  'verify-sources': 'Görseller ve sesler kontrol ediliyor',
  'generate-tts': 'Metinler seslendiriliyor',
  'probe-audio': 'Ses süreleri ölçülüyor',
  'compute-timeline': 'Sahne süreleri hesaplanıyor',
  'build-subtitles': 'Altyazılar hazırlanıyor',
  'normalize-images': 'Görseller hazırlanıyor',
  'render-text-cards': 'Yazılar çiziliyor',
  'render-scene-clips': 'Sahneler oluşturuluyor',
  assemble: 'Sahneler birleştiriliyor',
  'mix-audio': 'Sesler karıştırılıyor',
  encode: 'Video kaydediliyor',
  'validate-output': 'Video kontrol ediliyor',
  'prepare-shorts-source': 'Kısa video kaynağı hazırlanıyor',
  'write-artifacts': 'Dosyalar yazılıyor',
  cleanup: 'Temizlik yapılıyor',
}

/** Durum rozetleri. Ham İngilizce durum adlarını ekrana yazmamak için. */
const STATUS_LABEL: Record<string, string> = {
  queued: 'sırada',
  running: 'çalışıyor',
  completed: 'tamamlandı',
  failed: 'başarısız',
  cancelled: 'iptal edildi',
  interrupted: 'yarıda kaldı',
}

const QUALITIES: { id: QualityPreset; label: string; hint: string }[] = [
  { id: 'youtube-hq', label: 'YouTube kalitesi', hint: 'Yayınlamak için en iyisi. En yavaşı.' },
  { id: 'high', label: 'Yüksek', hint: 'Yüksek kalite, biraz daha hızlı.' },
  { id: 'standard', label: 'Normal', hint: 'İyi kalite, daha hızlı.' },
  { id: 'preview', label: 'Hızlı deneme', hint: 'Kaba ve hızlı. Sadece kontrol için.' },
]

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—'
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return minutes > 0 ? `${minutes} dk ${rest} sn` : `${rest} sn`
}

function formatBytes(bytes: number): string {
  if (bytes > 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`
  if (bytes > 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

function StatusPill({ status }: { status: RenderJob['status'] }) {
  return <span className={`status-pill status-${status}`}>{STATUS_LABEL[status] ?? status}</span>
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
        <h1>Videoyu oluştur</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
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
          <h1>Videoyu oluştur</h1>
          <p className="page-subtitle">
            Her şey hazırsa videonuzu burada oluşturursunuz. Sonuç: 1920×1080, 60 FPS bir MP4
            dosyası ve yanında altyazı dosyası.
          </p>
        </div>
        <div className="header-actions">
          {running ? (
            <button className="danger" onClick={() => void cancel()}>
              İptal et
            </button>
          ) : (
            <button
              className="primary"
              onClick={() => void start(slug)}
              disabled={busy || !preflight?.ready}
              title={preflight?.ready ? 'Videoyu oluşturmaya başla' : 'Önce aşağıdaki sorunları giderin'}
            >
              {busy ? 'Başlatılıyor…' : 'Videoyu oluştur'}
            </button>
          )}
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {/* --- live progress --- */}
      {running && (
        <section className="card render-live">
          <div className="render-head">
            <h2>{PHASE_LABEL[live?.phase ?? job.phase] ?? 'Oluşturuluyor'}</h2>
            <StatusPill status={job.status} />
          </div>
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <div className="render-meta">
            <span className="percent">{percent}%</span>
            <span>{live?.message ?? job.message}</span>
            <span className="spacer" />
            <span>Geçen süre {formatDuration(live?.elapsedSeconds ?? 0)}</span>
            {live?.estimatedRemainingSeconds != null && (
              <span>yaklaşık {formatDuration(live.estimatedRemainingSeconds)} kaldı</span>
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
                ? 'Video hazır'
                : job.status === 'cancelled'
                  ? 'İptal edildi'
                  : job.status === 'interrupted'
                    ? 'Yarıda kaldı'
                    : 'Video oluşturulamadı'}
            </h2>
            <StatusPill status={job.status} />
          </div>

          {job.status === 'completed' && (
            <>
              <p className="muted">
                {job.outputFile} · {formatDuration(job.totalDurationSeconds)} ·{' '}
                {job.scenesRendered} sahne oluşturuldu, {job.scenesReused} sahne hazırdan
                kullanıldı
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
                suggestion: job.errorSuggestion ?? 'Ayrıntılar için kayıt dosyasına bakın.',
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
          <h2>Başlamadan önce</h2>

          {preflight.blockingIssues.length > 0 ? (
            <div className="blocking">
              <strong>Önce bunların çözülmesi gerekiyor:</strong>
              {preflight.blockingIssues.map((issue) => (
                <p key={issue}>✕ {issue}</p>
              ))}
            </div>
          ) : (
            <p className="ready-note">✓ Her şey hazır, videoyu oluşturabilirsiniz.</p>
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
              <span className="label">Video süresi</span>
            </div>
            <div className="timing-stat">
              <span className="value">{Number(preflight.timing.sceneCount ?? 0)}</span>
              <span className="label">Sahne</span>
            </div>
            <div className="timing-stat">
              <span className="value">
                {Number(preflight.timing.transitionSeconds ?? 0).toFixed(1)}s
              </span>
              <span className="label">Geçişler</span>
            </div>
            <div className="timing-stat">
              <span className="value">
                {Number(preflight.disk.totalMb ?? 0) > 1024
                  ? `${(Number(preflight.disk.totalMb) / 1024).toFixed(1)} GB`
                  : `${Number(preflight.disk.totalMb ?? 0).toFixed(0)} MB`}
              </span>
              <span className="label">Gereken disk alanı</span>
            </div>
            <div className="timing-stat">
              <span className="value">~{formatDuration(preflight.estimatedRenderSeconds)}</span>
              <span className="label">Tahmini süre</span>
            </div>
          </div>
        </section>
      )}

      {/* --- options --- */}
      <section className="card">
        <h2>Kalite ve seçenekler</h2>
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
          Ekran kartını kullan
          <span className="hint">
            Çok daha hızlı, ama aynı dosya boyutunda kalite biraz daha düşük olur.
          </span>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.subtitles.burnIn}
            onChange={(e) => edit((d) => void (d.subtitles.burnIn = e.target.checked))}
            disabled={running}
          />
          Altyazıyı videonun içine göm
          <span className="hint">
            Açık gelir. Ayrı bir .srt altyazı dosyası her hâlükârda oluşur; görüntünün temiz
            kalmasını isterseniz bunu kapatın.
          </span>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.export.prepareCleanMasterForShorts}
            onChange={(e) =>
              edit((d) => void (d.export.prepareCleanMasterForShorts = e.target.checked))
            }
            disabled={running}
          />
          Kısa video için altyazısız kopya da hazırla
          <span className="hint">
            {project.subtitles.burnIn
              ? 'Videonun altyazısız ikinci bir kopyasını saklar. Böylece kısa videolarda altyazılar telefon ekranına göre büyük ve okunaklı çizilebilir. Süreyi yaklaşık iki katına çıkarır ve bir video boyutu kadar daha yer kaplar. Bunu şimdi yapmazsanız, sonradan eklenemez.'
              : 'Burada ek bir maliyeti yok: altyazı videoya gömülmediği için bu video zaten altyazısız ve doğrudan kopya olarak kullanılır.'}
          </span>
        </label>
      </section>

      {/* --- history --- */}
      {history.length > 0 && (
        <section className="card">
          <h2>Geçmiş</h2>
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
                        Tekrar dene
                      </button>
                    )}
                    {entry.logFile && (
                      <a className="button-link" href={`/api/jobs/${entry.id}/log`} download>
                        Kayıt dosyası
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
          <h2>Bilgisayarınızdaki dosyalar</h2>
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
