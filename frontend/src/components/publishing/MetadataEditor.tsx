/**
 * The metadata form.
 *
 * Two kinds of field live here, and the difference matters:
 *
 * * **Shared text** — title, description, tags. Edited once and written to both
 *   the draft's `common` block (the seed every platform reads) and its
 *   `youtube` block (what will actually be sent).
 * * **YouTube-specific** — category, languages, privacy, schedule, flags, the
 *   thumbnail image and the caption file.
 *
 * `thumbnailText` and `thumbnailPrompt` are shown but never uploaded anywhere:
 * they are the notes the user wrote while planning the thumbnail, kept in view
 * while they make the real image.
 */

import { useRef } from 'react'
import type { MediaItem, PublishDraft } from '@/api/publishing-types'
import {
  MAX_DESCRIPTION_BYTES,
  MAX_TAGS_LENGTH,
  MAX_TITLE_CHARS,
  tagsLength,
  utf8Bytes,
} from '@/api/publishing-types'
import { CountedInput, CountedTextarea, SchedulePicker, TagEditor } from './fields'

interface Props {
  draft: PublishDraft
  media: MediaItem
  slug: string
  busy: boolean
  onEdit: (mutate: (draft: PublishDraft) => void) => void
  onRefill: () => void
  onThumbnailFile: (file: File) => void
  onCaptionFile: (file: File) => void
}

/** YouTube's category list, trimmed to the ones a documentary would use. */
const CATEGORIES: { id: string; label: string }[] = [
  { id: '27', label: 'Eğitim' },
  { id: '22', label: 'İnsanlar ve bloglar' },
  { id: '24', label: 'Eğlence' },
  { id: '25', label: 'Haber ve politika' },
  { id: '15', label: 'Evcil hayvanlar ve hayvanlar' },
  { id: '28', label: 'Bilim ve teknoloji' },
  { id: '19', label: 'Seyahat ve etkinlikler' },
]

const LANGUAGES: { id: string; label: string }[] = [
  { id: 'en', label: 'İngilizce (en)' },
  { id: 'tr', label: 'Türkçe (tr)' },
  { id: 'de', label: 'Almanca (de)' },
  { id: 'es', label: 'İspanyolca (es)' },
  { id: 'fr', label: 'Fransızca (fr)' },
]

const PRIVACIES: { id: 'private' | 'unlisted' | 'public'; label: string; hint: string }[] = [
  { id: 'private', label: 'Gizli', hint: 'Yalnızca siz görürsünüz.' },
  { id: 'unlisted', label: 'Liste dışı', hint: 'Bağlantısı olan herkes görür.' },
  { id: 'public', label: 'Herkese açık', hint: 'Herkes bulabilir.' },
]

export function MetadataEditor({
  draft, media, busy, onEdit, onRefill, onThumbnailFile, onCaptionFile,
}: Props) {
  const thumbnailInput = useRef<HTMLInputElement>(null)
  const captionInput = useRef<HTMLInputElement>(null)
  const youtube = draft.youtube
  const scheduled = youtube.publishMode === 'schedule'

  return (
    <section className="card">
      <div className="publish-section-head">
        <h2>2. Bilgileri düzenleyin</h2>
        <button type="button" onClick={onRefill} disabled={busy}>
          Proje metadatasından tekrar doldur
        </button>
      </div>

      <CountedInput
        id="publish-title"
        label="Başlık"
        value={youtube.title}
        used={youtube.title.length}
        limit={MAX_TITLE_CHARS}
        unit="karakter"
        disabled={busy}
        onChange={(value) =>
          onEdit((next) => {
            next.common.title = value
            next.youtube.title = value
          })
        }
        hint="YouTube başlıkları en fazla 100 karakter olabilir ve < > karakterlerini kabul etmez."
      />

      <CountedTextarea
        id="publish-description"
        label="Açıklama"
        value={youtube.description}
        used={utf8Bytes(youtube.description)}
        limit={MAX_DESCRIPTION_BYTES}
        unit="bayt"
        rows={10}
        disabled={busy}
        onChange={(value) =>
          onEdit((next) => {
            next.common.description = value
            next.youtube.description = value
          })
        }
        hint="Sınır karakter değil bayt üzerinden sayılır; Türkçe harfler ve emoji birden fazla bayt tutar."
      />

      <TagEditor
        id="publish-tags"
        label="Etiketler"
        tags={youtube.tags}
        used={tagsLength(youtube.tags)}
        limit={MAX_TAGS_LENGTH}
        disabled={busy}
        onChange={(tags) =>
          onEdit((next) => {
            next.common.tags = tags
            next.youtube.tags = tags
          })
        }
      />

      <div className="field-grid">
        <label htmlFor="publish-category">
          Kategori
          <select
            id="publish-category"
            value={youtube.categoryId}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.categoryId = event.target.value
              })
            }
          >
            {CATEGORIES.map((category) => (
              <option key={category.id} value={category.id}>
                {category.label} ({category.id})
              </option>
            ))}
          </select>
        </label>

        <label htmlFor="publish-language">
          Varsayılan metadata dili
          <select
            id="publish-language"
            value={youtube.defaultLanguage}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.defaultLanguage = event.target.value
              })
            }
          >
            {LANGUAGES.map((language) => (
              <option key={language.id} value={language.id}>
                {language.label}
              </option>
            ))}
          </select>
        </label>

        <label htmlFor="publish-audio-language">
          Varsayılan ses dili
          <select
            id="publish-audio-language"
            value={youtube.defaultAudioLanguage}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.defaultAudioLanguage = event.target.value
              })
            }
          >
            {LANGUAGES.map((language) => (
              <option key={language.id} value={language.id}>
                {language.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* --- reference only --- */}
      <details className="publish-reference">
        <summary>Thumbnail notları (YouTube'a gönderilmez)</summary>
        <label className="publish-field" htmlFor="publish-thumbnail-text">
          <span className="publish-field-head">Thumbnail metni</span>
          <input
            id="publish-thumbnail-text"
            value={draft.common.thumbnailText}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.common.thumbnailText = event.target.value
              })
            }
          />
        </label>
        <label className="publish-field" htmlFor="publish-thumbnail-prompt">
          <span className="publish-field-head">Thumbnail prompt'u</span>
          <textarea
            id="publish-thumbnail-prompt"
            rows={4}
            value={draft.common.thumbnailPrompt}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.common.thumbnailPrompt = event.target.value
              })
            }
          />
        </label>
        <p className="hint">
          Bu iki alan yalnızca sizin için burada duruyor; kapak görselini hazırlarken
          bakabilesiniz diye. YouTube'a metadata olarak gönderilmezler.
        </p>
      </details>

      {/* --- files --- */}
      <div className="publish-assets">
        <div className="publish-asset">
          <strong>Kapak görseli (thumbnail)</strong>
          <p className="muted">
            {youtube.thumbnailFile ?? 'Seçilmedi — YouTube videodan bir kare seçer.'}
          </p>
          <input
            ref={thumbnailInput}
            type="file"
            accept="image/jpeg,image/png"
            className="visually-hidden"
            aria-label="Kapak görseli seç"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) onThumbnailFile(file)
              event.target.value = ''
            }}
          />
          <div className="row">
            <button type="button" disabled={busy} onClick={() => thumbnailInput.current?.click()}>
              Görsel seç
            </button>
            {youtube.thumbnailFile && (
              <button
                type="button"
                className="danger"
                disabled={busy}
                onClick={() =>
                  onEdit((next) => {
                    next.youtube.thumbnailFile = null
                  })
                }
              >
                Kaldır
              </button>
            )}
          </div>
          <span className="hint">JPEG ya da PNG, en fazla 2 MB.</span>
        </div>

        <div className="publish-asset">
          <strong>İngilizce altyazı (.srt)</strong>
          <p className="muted">
            {youtube.captionFile
              ? `${youtube.captionFile}${
                  youtube.captionSource === 'export' ? ' (videonun yanındaki dosya)' : ''
                }`
              : 'Seçilmedi.'}
          </p>
          <input
            ref={captionInput}
            type="file"
            accept=".srt,text/plain"
            className="visually-hidden"
            aria-label="Altyazı dosyası seç"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) onCaptionFile(file)
              event.target.value = ''
            }}
          />
          <div className="row">
            <button type="button" disabled={busy} onClick={() => captionInput.current?.click()}>
              .srt seç
            </button>
            {media.captionFilename && youtube.captionFile !== media.captionFilename && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  onEdit((next) => {
                    next.youtube.captionFile = media.captionFilename
                    next.youtube.captionSource = 'export'
                    next.youtube.uploadCaptions = true
                  })
                }
              >
                Videonun altyazısını kullan
              </button>
            )}
          </div>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={youtube.uploadCaptions}
              disabled={busy || !youtube.captionFile}
              onChange={(event) =>
                onEdit((next) => {
                  next.youtube.uploadCaptions = event.target.checked
                })
              }
            />
            Yükleme sonrası altyazıyı da gönder
          </label>
          <span className="hint">
            {media.kind === 'short'
              ? 'Kısa videolarda altyazı genellikle görüntünün içine gömülüdür; bu yüzden varsayılan olarak kapalıdır.'
              : 'Altyazı, video yüklendikten sonra ayrı bir adımda gönderilir.'}
          </span>
        </div>
      </div>

      {/* --- publishing options --- */}
      <div className="publish-options">
        <fieldset className="publish-privacy">
          <legend>Gizlilik</legend>
          {PRIVACIES.map((privacy) => (
            <label key={privacy.id} className={youtube.privacyStatus === privacy.id ? 'selected' : ''}>
              <input
                type="radio"
                name="publish-privacy"
                checked={youtube.privacyStatus === privacy.id}
                disabled={busy || scheduled}
                onChange={() =>
                  onEdit((next) => {
                    next.youtube.privacyStatus = privacy.id
                  })
                }
              />
              <span>{privacy.label}</span>
              <span className="hint">{privacy.hint}</span>
            </label>
          ))}
          {scheduled && (
            <p className="hint">
              Planlı videolar, zamanı gelene kadar YouTube tarafından gizli tutulur; zamanı
              gelince herkese açılır.
            </p>
          )}
        </fieldset>

        <SchedulePicker
          idPrefix="youtube"
          mode={youtube.publishMode}
          value={youtube.publishAtLocal}
          disabled={busy}
          onModeChange={(mode) =>
            onEdit((next) => {
              next.youtube.publishMode = mode
            })
          }
          onValueChange={(value) =>
            onEdit((next) => {
              next.youtube.publishAtLocal = value
            })
          }
        />
      </div>

      <div className="publish-flags">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={youtube.madeForKids}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.madeForKids = event.target.checked
              })
            }
          />
          Çocuklara özel içerik
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={youtube.notifySubscribers}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.notifySubscribers = event.target.checked
              })
            }
          />
          Abonelere bildirim gönder
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={youtube.embeddable}
            disabled={busy}
            onChange={(event) =>
              onEdit((next) => {
                next.youtube.embeddable = event.target.checked
              })
            }
          />
          Başka sitelere yerleştirmeye izin ver
        </label>
      </div>
    </section>
  )
}
