/**
 * Shorts: cut a vertical 9:16 clip out of a finished long render.
 *
 * Nothing on this page re-renders a scene. It picks spans out of an MP4 that is
 * already finished, so the narration, music and in-scene transitions come
 * through exactly as they were mixed.
 *
 * Captions are the one thing that can differ. A long video's subtitles are
 * burned into a 16:9 picture, which shrinks to a third of the height on a
 * vertical canvas — so a render that prepared a subtitle-free clean master can
 * instead have large captions drawn on the 9:16 canvas itself. A render that did
 * not prepare one cannot be converted after the fact, and says so here.
 */

import { useEffect, useMemo, useState } from 'react'
import type {
  ShortCaptionMode,
  ShortCaptionPreset,
  ShortJob,
  ShortPhase,
  ShortTimelineSection,
} from '@/api/shorts-types'
import { captionSupportOf } from '@/api/shorts-types'
import { useProjectStore } from '@/store/project'
import { useShortsStore } from '@/store/shorts'
import { formatTimecode, frameAt, resolvePlan } from '@/lib/shortsPlan'
import { ErrorBox } from '@/components/ErrorBox'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import './ShortsPage.css'

const PHASE_LABEL: Record<ShortPhase, string> = {
  'validate-source': 'Kaynak video kontrol ediliyor',
  plan: 'Kesim listesi hazırlanıyor',
  'cut-segments': 'Seçilen bölümler kesiliyor',
  concat: 'Parçalar birleştiriliyor',
  'build-captions': 'Altyazılar çiziliyor',
  compose: 'Dikey görüntü hazırlanıyor',
  'validate-output': 'Kısa video kontrol ediliyor',
  publish: 'Kaydediliyor',
  cleanup: 'Temizlik yapılıyor',
}

const STATUS_LABEL: Record<string, string> = {
  queued: 'sırada',
  running: 'çalışıyor',
  completed: 'tamamlandı',
  failed: 'başarısız',
  cancelled: 'iptal edildi',
  interrupted: 'yarıda kaldı',
}

const CAPTION_PRESETS: { id: ShortCaptionPreset; label: string; hint: string }[] = [
  { id: 'standard', label: 'Normal', hint: 'İki satır, orta boy yazı. Güvenli seçim.' },
  { id: 'large', label: 'Büyük', hint: 'Daha büyük yazı, biraz daha yukarıda.' },
  { id: 'compact', label: 'Küçük', hint: 'Daha küçük yazı, görüntüye daha çok yer.' },
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

function StatusPill({ status }: { status: ShortJob['status'] }) {
  return <span className={`status-pill status-${status}`}>{STATUS_LABEL[status] ?? status}</span>
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
      <span className="trim-hint">{frameAt(value, fps)}. kare</span>
    </label>
  )
}

export function ShortsPage() {
  const { project } = useProjectStore()
  const {
    sources, selectedRenderId, timeline, selection, captionMode, captionPreset,
    preflight, job, event, history,
    error, loading, busy,
    loadSources, selectSource, toggleSection, moveSelection, removeSelection,
    setTrim, setCaptionMode, setCaptionPreset, refreshPreflight, start, cancel, retry,
    detach, reattachIfRunning, loadHistory, remove, clearError,
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
        <h1>Kısa video</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
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
  const captionSupport = captionSupportOf(source)
  const nativeBlocked = captionMode !== 'source-burned-in' && !captionSupport.nativeAvailable
  const canRender =
    !!source?.usable &&
    selection.length > 0 &&
    !overMaximum &&
    shortClips.length === 0 &&
    !nativeBlocked &&
    !running &&
    !busy

  return (
    <div className="page shorts-page">
      <header className="page-header">
        <div>
          <h1>Kısa video</h1>
          <p className="page-subtitle">
            Hazır videonuzdan telefona uygun dikey bir kısa video (1080×1920) keser. Görüntü
            siyah zeminin ortasına yerleşir; ses ve sahne geçişleri olduğu gibi korunur.
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
              disabled={!canRender}
              title={
                selection.length === 0
                  ? 'Önce en az bir bölüm seçin'
                  : overMaximum
                    ? `${maxSeconds} saniye sınırını aşıyor`
                    : nativeBlocked
                      ? 'Bu video büyük altyazıyı desteklemiyor'
                      : 'Kısa videoyu oluştur'
              }
            >
              {busy ? 'Başlatılıyor…' : 'Kısa videoyu oluştur'}
            </button>
          )}
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {/* --- 1. source render --------------------------------------------- */}
      <section className="card">
        <div className="section-head">
          <h2>Hangi videodan kesilsin?</h2>
          <button onClick={() => void loadSources(slug)} disabled={loading}>
            {loading ? 'Yenileniyor…' : 'Yenile'}
          </button>
        </div>

        {sources.length === 0 ? (
          <p className="muted empty-note">
            Henüz hazır video yok. Önce “Videoyu oluştur” sekmesinden uzun videoyu oluşturun —
            kısa video her zaman bitmiş bir videodan kesilir.
          </p>
        ) : (
          <div className="source-grid" role="radiogroup" aria-label="Kaynak video">
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
                    {entry.width}×{entry.height} · {entry.fps} fps · {entry.sectionCount} bölüm
                  </span>
                  {entry.issue && <span className="source-issue">⚠ {entry.issue}</span>}
                  <span
                    className={`source-captions ${
                      captionSupportOf(entry).nativeAvailable ? 'ready' : 'legacy'
                    }`}
                  >
                    {captionSupportOf(entry).nativeAvailable
                      ? '✓ Büyük altyazı kullanılabilir'
                      : 'Sadece videodaki mevcut altyazı'}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {/* --- 1b. captions --------------------------------------------------- */}
      {source && (
        <section className="card captions-card">
          <div className="section-head">
            <h2>Altyazılar</h2>
            {captionSupport.nativeAvailable && captionSupport.cueCount > 0 && (
              <span className="muted">Bu videoda {captionSupport.cueCount} altyazı satırı var</span>
            )}
          </div>

          <div className="caption-modes" role="radiogroup" aria-label="Altyazılar">
            <CaptionModeOption
              mode="source-burned-in"
              current={captionMode}
              label="Videodaki altyazıyı kullan"
              hint={
                captionSupport.sourceHasBurnedInSubtitles
                  ? 'Video olduğu gibi kesilir. Altyazılar geniş ekrana göre hazırlandığı için dikey videoda küçük görünür.'
                  : 'Video olduğu gibi kesilir. Bu videoda gömülü altyazı olmadığı için kısa videoda da altyazı olmaz.'
              }
              disabled={running}
              onSelect={() => setCaptionMode(slug, 'source-burned-in')}
            />
            <CaptionModeOption
              mode="shorts-native"
              current={captionMode}
              label="Büyük altyazı"
              hint="Altyazılar dikey ekrana yeniden çizilir: görüntünün altında, telefonda rahat okunacak büyüklükte."
              disabled={running || !captionSupport.nativeAvailable}
              blockedReason={captionSupport.reason}
              onSelect={() => setCaptionMode(slug, 'shorts-native')}
            />
            {captionSupport.nativeAvailable && (
              <CaptionModeOption
                mode="off"
                current={captionMode}
                label="Altyazı olmasın"
                hint="Hiç altyazı çizilmez. Sadece altyazısız kopyası olan videolarda seçilebilir."
                disabled={running}
                onSelect={() => setCaptionMode(slug, 'off')}
              />
            )}
          </div>

          {captionMode === 'shorts-native' && (
            <div className="caption-style">
              <div className="caption-presets" role="radiogroup" aria-label="Altyazı boyutu">
                {CAPTION_PRESETS.map((preset) => (
                  <label
                    key={preset.id}
                    className={`caption-preset ${captionPreset === preset.id ? 'selected' : ''}`}
                  >
                    <input
                      type="radio"
                      name="caption-preset"
                      checked={captionPreset === preset.id}
                      onChange={() => setCaptionPreset(slug, preset.id)}
                      disabled={running}
                      aria-label={`${preset.label} altyazı boyutu`}
                    />
                    <span className="caption-preset-label">{preset.label}</span>
                    <span className="hint">{preset.hint}</span>
                  </label>
                ))}
              </div>
              <p className="muted caption-note">
                Altyazılar dikey ekranın altında, görüntünün altındaki siyah alanda durur. YouTube'un
                oynatma çubuğu ve beğen/yorum düğmelerinin üstünü kapatmaz. En fazla iki satır olur;
                sığmazsa yazı biraz küçülür.
              </p>
              {preflight?.captionCueCount != null && preflight.captionCueCount > 0 && (
                <p className="muted caption-count">
                  Seçtiğiniz bölümlerde {preflight.captionCueCount} altyazı satırı var.
                </p>
              )}
            </div>
          )}

          {nativeBlocked && (
            <div className="blocking caption-blocking">
              <strong>Bu videoda büyük altyazı kullanılamıyor.</strong>
              <p>
                {captionSupport.reason ??
                  'Bu videonun altyazıları görüntünün içine gömülü. Büyük altyazı kullanmak için uzun videoyu, “Kısa video için altyazısız kopya da hazırla” seçeneği açıkken yeniden oluşturun.'}
              </p>
              <button onClick={() => setCaptionMode(slug, 'source-burned-in')}>
                Bunun yerine videodaki altyazıyı kullan
              </button>
            </div>
          )}
        </section>
      )}

      {/* --- 2. sections --------------------------------------------------- */}
      <section className="card">
        <div className="section-head">
          <h2>Hangi bölümler girsin?</h2>
          {selection.length > 0 && (
            <span className="muted">
              {selection.length} bölüm seçildi · seçme sırası, videodaki sıradır
            </span>
          )}
        </div>

        {!timeline ? (
          <p className="muted empty-note">
            Bölüm seçmek için yukarıdan bir video seçin.
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
          <h2>Önizleme</h2>

          <div className="duration-row">
            <div className="duration-readout">
              <span className={`duration-value ${overMaximum ? 'bad' : overWarn ? 'warn' : ''}`}>
                {total.toFixed(1)}s
              </span>
              <span className="label">
                toplam · {plan.groups.length} parça
              </span>
            </div>
            <div
              className="duration-meter"
              role="meter"
              aria-valuenow={Math.round(total)}
              aria-valuemin={0}
              aria-valuemax={Math.round(maxSeconds)}
              aria-label="Kısa video toplam süresi"
            >
              <span
                className="band"
                style={{
                  left: `${(bandLow / maxSeconds) * 100}%`,
                  width: `${((bandHigh - bandLow) / maxSeconds) * 100}%`,
                }}
                title={`Önerilen ${bandLow}–${bandHigh} sn`}
              />
              <span className="tick warn" style={{ left: `${(warnSeconds / maxSeconds) * 100}%` }} />
              <span
                className={`fill ${overMaximum ? 'bad' : overWarn ? 'warn' : withinBand ? 'good' : ''}`}
                style={{ width: `${Math.min(100, (total / maxSeconds) * 100)}%` }}
              />
            </div>
            <span className="muted duration-scale">
              önerilen {bandLow}–{bandHigh} sn · en fazla {maxSeconds} sn
            </span>
          </div>

          {overMaximum && (
            <div className="blocking">
              <strong>Kısa video için fazla uzun.</strong>
              <p>
                YouTube yalnızca {maxSeconds / 60} dakikaya kadar olan dikey videoları kısa video
                sayar. Bir bölümü çıkarın veya kırpın.
              </p>
            </div>
          )}
          {!overMaximum && overWarn && (
            <div className="warnings">
              <p>
                ⚠ {warnSeconds} saniyeyi aştı. Bir dakikadan uzun kısa videolar, içindeki müzik
                telifliyse tüm dünyada engellenebilir. Müzik sizinse veya lisanslıysa sorun yok;
                değilse bir dakikanın altında kalın.
              </p>
            </div>
          )}
          {shortClips.length > 0 && (
            <div className="blocking">
              <strong>Bir parça çok kısa.</strong>
              {shortClips.map((segment) => (
                <p key={segment.unitId}>
                  {segment.number}. bölüm — {segment.title}: {segment.durationSeconds.toFixed(2)} sn.
                  En az {timeline.minClipSeconds.toFixed(1)} sn olmalı.
                </p>
              ))}
            </div>
          )}

          <div className="preview-row">
            <div className="canvas-preview" aria-label="1080×1920 çıktı önizlemesi">
              <div className="canvas-frame">
                <div className="canvas-fit">
                  {preflight?.previewFrames?.[0] ? (
                    <img src={preflight.previewFrames[0].url} alt="Kısa videonun ilk karesi" />
                  ) : (
                    <span className="canvas-placeholder">16:9</span>
                  )}
                </div>
                {captionMode === 'shorts-native' && (
                  <div className="canvas-caption-band" aria-hidden="true">
                    <span>Altyazı burada</span>
                  </div>
                )}
              </div>
              <span className="canvas-caption">
                1080 × 1920 · siyah zemin · ortalanmış
                {captionMode === 'shorts-native' && ' · altyazı görüntünün altında'}
              </span>
            </div>

            <div className="preview-strip">
              <p className="muted preview-note">
                {plan.groups.length === 1
                  ? 'Tek parça hâlinde kesiliyor — içindeki geçişler aynen korunuyor.'
                  : 'Ayrı parçalar doğrudan uç uca eklenir. Videoda olmayan hiçbir efekt eklenmez.'}
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
                        {group.numbers.join(' + ')} · {group.durationSeconds.toFixed(1)} sn
                        {group.preservedTransitions > 0 && (
                          <em> · {group.preservedTransitions} geçiş korundu</em>
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
              {preflight?.cachedShortId && (
                <p className="cached-note">
                  ✓ Birebir aynısı zaten var — yeniden oluşturmak yerine mevcut dosya kullanılacak.
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
                Önizlemeyi yenile
              </button>
            </div>
          </div>
        </section>
      )}

      {/* --- 4. live progress --------------------------------------------- */}
      {running && job && (
        <section className="card render-live">
          <div className="render-head">
            <h2>{PHASE_LABEL[live?.phase ?? job.phase] ?? 'Kısa video hazırlanıyor'}</h2>
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

      {/* --- 5. last result ------------------------------------------------ */}
      {job && !running && (
        <section className={`card render-result render-${job.status}`}>
          <div className="render-head">
            <h2>
              {job.status === 'completed'
                ? job.cacheReused
                  ? 'Hazır olan kısa video kullanıldı'
                  : 'Kısa video hazır'
                : job.status === 'cancelled'
                  ? 'İptal edildi'
                  : job.status === 'interrupted'
                    ? 'Yarıda kaldı'
                    : 'Kısa video oluşturulamadı'}
            </h2>
            <StatusPill status={job.status} />
          </div>

          {job.status === 'completed' && (
            <>
              <p className="muted">
                {job.outputFile} · {job.totalDurationSeconds.toFixed(1)} sn ·{' '}
                {job.segmentCount} bölüm, {job.groupCount} parça
                {job.cacheReused && ' · yeniden işlenmedi'}
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

      {/* --- 6. history ---------------------------------------------------- */}
      {history.length > 0 && (
        <section className="card">
          <h2>Bu projedeki kısa videolar</h2>
          <table className="history-table shorts-history">
            <thead>
              <tr>
                <th scope="col">Bölümler</th>
                <th scope="col">Dosya</th>
                <th scope="col">Kaynak</th>
                <th scope="col">Altyazı</th>
                <th scope="col">Süre</th>
                <th scope="col">Oluşturuldu</th>
                <th scope="col">
                  <span className="visually-hidden">İşlemler</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {history.map((entry) => (
                <tr key={entry.shortId}>
                  <td className="history-sections">{entry.sectionNumbers.join(' → ') || '—'}</td>
                  <td className="history-file">{entry.filename}</td>
                  <td className="muted">{entry.sourceVideo}</td>
                  <td className="muted">
                    {entry.captionMode === 'shorts-native'
                      ? 'Büyük'
                      : entry.captionMode === 'off'
                        ? 'Yok'
                        : 'Videodaki'}
                  </td>
                  <td className="muted">{entry.durationSeconds.toFixed(1)} sn</td>
                  <td className="muted">{new Date(entry.createdAt).toLocaleString()}</td>
                  <td className="history-actions">
                    <a
                      className="button-link"
                      href={entry.url}
                      download
                      aria-label={`${entry.filename} dosyasını indir`}
                    >
                      İndir
                    </a>
                    <button
                      className="danger"
                      onClick={() => setPendingDelete(entry.shortId)}
                      aria-label={`${entry.filename} dosyasını sil`}
                    >
                      Sil
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
          title="Bu kısa video silinsin mi?"
          body={
            <p>
              Sadece bu kısa video ve yan dosyaları silinir. Kesildiği uzun videoya, projeye ve
              diğer kısa videolara dokunulmaz.
            </p>
          }
          confirmLabel="Kısa videoyu sil"
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

/** One caption-source choice. Disabled options say *why*, never just grey out. */
function CaptionModeOption({
  mode,
  current,
  label,
  hint,
  disabled,
  blockedReason,
  onSelect,
}: {
  mode: ShortCaptionMode
  current: ShortCaptionMode
  label: string
  hint: string
  disabled: boolean
  blockedReason?: string | null
  onSelect: () => void
}) {
  const selected = current === mode
  return (
    <label className={`caption-mode ${selected ? 'selected' : ''} ${disabled ? 'disabled' : ''}`}>
      <input
        type="radio"
        name="caption-mode"
        checked={selected}
        onChange={onSelect}
        disabled={disabled}
        aria-label={label}
      />
      <span className="caption-mode-label">{label}</span>
      <span className="hint">{hint}</span>
      {disabled && blockedReason && (
        <span className="caption-mode-blocked">{blockedReason}</span>
      )}
    </label>
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
          aria-label={`${section.number}. bölümü ekle: ${section.title}`}
        />
        <span className="section-number">{section.number}</span>
        {order !== null && (
          <span className="order-badge" aria-label={`Kısa videodaki sırası: ${order}`}>
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
          {section.safeDurationSeconds.toFixed(1)} sn kullanılabilir
          {section.transitionDurationSeconds > 0 && (
            <em> · sonrasında {section.transitionDurationSeconds.toFixed(2)} sn geçiş</em>
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
              label="Başlangıç"
              value={start}
              low={section.safeStartSeconds}
              high={section.safeEndSeconds}
              fps={fps}
              describedBy={hintId}
              onCommit={(next) => onTrim('start', next)}
            />
            <TrimInput
              label="Bitiş"
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
              {length.toFixed(2)} sn · {section.safeStartSeconds.toFixed(2)} sn ile{' '}
              {section.safeEndSeconds.toFixed(2)} sn arasında kırpabilirsiniz
            </span>
            <div className="section-controls">
              <button onClick={() => onMove(-1)} aria-label={`${section.title} bölümünü öne al`}>
                ↑
              </button>
              <button onClick={() => onMove(1)} aria-label={`${section.title} bölümünü geri al`}>
                ↓
              </button>
              <button onClick={onRemove} aria-label={`${section.title} bölümünü çıkar`}>
                Çıkar
              </button>
              <button
                onClick={() => {
                  onTrim('start', null)
                  onTrim('end', null)
                }}
                aria-label={`${section.title} bölümünün kırpmasını sıfırla`}
              >
                Kırpmayı sıfırla
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
