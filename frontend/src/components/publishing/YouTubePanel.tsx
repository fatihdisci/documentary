/**
 * The YouTube platform card: connection, the upload button, live progress and
 * the result.
 *
 * The upload button is disabled unless the account is connected *and* a file is
 * selected, so the failure "you are not connected" is never discovered halfway
 * through an upload.
 */

import type {
  AssetStatus,
  MediaItem,
  PublishDraft,
  PublishJob,
  PublishJobEvent,
  YouTubeConnection,
} from '@/api/publishing-types'
import { ErrorBox } from '@/components/ErrorBox'
import { formatBytes, formatDateTime } from '@/lib/format'

interface Props {
  connection: YouTubeConnection | null
  draft: PublishDraft | null
  media: MediaItem | null
  job: PublishJob | null
  event: PublishJobEvent | null
  busy: boolean
  blockedReason: string | null
  onConnect: () => void
  onPublish: () => void
  onCancel: () => void
  onRetry: (jobId: string) => void
}

const PHASE_LABEL: Record<PublishJobEvent['phase'], string> = {
  validate: 'Bilgiler kontrol ediliyor',
  authenticate: 'YouTube bağlantısı doğrulanıyor',
  'hash-source': 'Dosya parmak izi hesaplanıyor',
  'upload-video': 'Video yükleniyor',
  'set-thumbnail': 'Kapak görseli konuluyor',
  'upload-captions': 'Altyazı gönderiliyor',
  // Phases TikTok uses. Listed so the map stays exhaustive; a YouTube job never
  // reaches either of them.
  'create-container': 'Platform videoyu alıyor',
  'await-processing': 'Platform videoyu işliyor',
  'fetch-status': 'Video durumu okunuyor',
  complete: 'Tamamlandı',
}

const ASSET_LABEL: Record<AssetStatus, string> = {
  skipped: 'gönderilmedi',
  pending: 'bekliyor',
  uploaded: 'yüklendi',
  failed: 'başarısız',
}

const PRIVACY_LABEL: Record<string, string> = {
  private: 'Gizli',
  unlisted: 'Liste dışı',
  public: 'Herkese açık',
}

export function YouTubePanel({
  connection, draft, media, job, event, busy, blockedReason,
  onConnect, onPublish, onCancel, onRetry,
}: Props) {
  const running = job !== null && (job.status === 'queued' || job.status === 'running')
  const percent = Math.round((event?.progress ?? job?.progress ?? 0) * 100)
  const scheduled = draft?.youtube.publishMode === 'schedule'
  const connected = connection?.connected === true && connection.scopesSufficient

  return (
    <section className="card platform-card youtube">
      <div className="platform-head">
        <div>
          <h3>YouTube</h3>
          {connected ? (
            <p className="muted">
              {connection?.channelTitle ?? 'Kanal'}{' '}
              {connection?.channelId && <code>{connection.channelId}</code>}
            </p>
          ) : (
            <p className="muted">{connection?.statusMessage ?? 'Bağlantı durumu okunuyor…'}</p>
          )}
        </div>
        {connection?.channelThumbnailUrl && connected && (
          <img className="channel-avatar" src={connection.channelThumbnailUrl} alt="" />
        )}
      </div>

      {!connected && (
        <div className="platform-connect">
          <p>
            {connection?.problem ??
              'YouTube hesabınız bağlı değil. Yükleme yapabilmek için hesabınızı bağlayın.'}
          </p>
          {connection?.suggestion && <p className="hint">{connection.suggestion}</p>}
          <button type="button" className="primary" onClick={onConnect} disabled={busy}>
            {connection?.tokenPresent ? 'Yeniden bağlan' : "YouTube'a bağlan"}
          </button>
          <p className="hint">
            Ayrıntılı kurulum adımları Ayarlar → Bağlantılar ve servisler bölümünde.
          </p>
        </div>
      )}

      {!running && (
        <div className="platform-actions">
          <button
            type="button"
            className="primary"
            onClick={onPublish}
            disabled={!connected || !media || !draft || busy || blockedReason !== null}
            title={blockedReason ?? undefined}
          >
            {scheduled ? "YouTube'a yükle ve planla" : "YouTube'a yükle"}
          </button>
          {blockedReason && <span className="hint">{blockedReason}</span>}
        </div>
      )}

      {running && (
        <div className="publish-progress">
          <div className="render-head">
            <h4>{PHASE_LABEL[event?.phase ?? job.phase]}</h4>
            <span className="status-pill status-running">yükleniyor</span>
          </div>
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <div className="render-meta">
            <span className="percent">{percent}%</span>
            <span>{event?.message ?? job.message}</span>
            <span className="spacer" />
            {(event?.totalBytes ?? job.totalBytes) > 0 && (
              <span>
                {formatBytes(event?.uploadedBytes ?? job.uploadedBytes)} /{' '}
                {formatBytes(event?.totalBytes ?? job.totalBytes)}
              </span>
            )}
          </div>
          <button type="button" className="danger" onClick={onCancel}>
            Yüklemeyi iptal et
          </button>
        </div>
      )}

      {job && !running && job.status === 'completed' && (
        <div className="publish-result">
          <h4>
            {job.thumbnailStatus === 'failed' || job.captionStatus === 'failed'
              ? 'Video yüklendi, bazı adımlar tamamlanamadı'
              : job.requestedPublishAt
                ? 'Video yüklendi ve planlandı'
                : 'Video YouTube’a yüklendi'}
          </h4>
          <dl className="result-grid">
            <dt>Video numarası</dt>
            <dd><code>{job.videoId}</code></dd>
            <dt>Bağlantı</dt>
            <dd>
              {job.videoUrl && (
                <a href={job.videoUrl} target="_blank" rel="noreferrer">
                  {job.videoUrl}
                </a>
              )}
            </dd>
            <dt>Yayın zamanı</dt>
            <dd>
              {job.actualPublishAt || job.requestedPublishAt
                ? formatDateTime(job.actualPublishAt ?? job.requestedPublishAt)
                : 'Hemen yayımlandı'}
            </dd>
            <dt>Gizlilik</dt>
            <dd>
              {PRIVACY_LABEL[job.actualPrivacyStatus ?? job.requestedPrivacyStatus] ??
                (job.actualPrivacyStatus ?? job.requestedPrivacyStatus)}
            </dd>
            <dt>Kapak görseli</dt>
            <dd>{ASSET_LABEL[job.thumbnailStatus]}{job.thumbnailError ? ` — ${job.thumbnailError}` : ''}</dd>
            <dt>Altyazı</dt>
            <dd>{ASSET_LABEL[job.captionStatus]}{job.captionError ? ` — ${job.captionError}` : ''}</dd>
            {job.processingStatus && (
              <>
                <dt>YouTube işleme durumu</dt>
                <dd>{job.processingStatus}</dd>
              </>
            )}
          </dl>

          {job.processingStatus && job.processingStatus !== 'succeeded' && (
            <p className="muted">Video YouTube’a gönderildi, YouTube tarafından işleniyor.</p>
          )}

          {job.warnings.length > 0 && (
            <div className="warnings">
              {job.warnings.map((warning) => (
                <p key={warning}>⚠ {warning}</p>
              ))}
            </div>
          )}

          {(job.thumbnailStatus === 'failed' || job.captionStatus === 'failed') && (
            <button type="button" onClick={() => onRetry(job.id)} disabled={busy}>
              Kalan adımları tekrar dene
            </button>
          )}
        </div>
      )}

      {job && !running && job.status !== 'completed' && job.errorMessage && (
        <ErrorBox
          error={{
            code: job.errorCode ?? 'youtube_upload_failed',
            message: job.errorMessage,
            details: job.errorDetails,
            suggestion: job.errorSuggestion ?? 'Ayrıntılar için kayıt dosyasına bakın.',
            logPath: null,
            context: {},
          }}
          onRetry={() => onRetry(job.id)}
        />
      )}
    </section>
  )
}
