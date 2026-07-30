import { useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { ImportReport } from '@/api/project-types'
import { longIntroOf } from '@/api/project-types'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import './ContentPage.css'

export function ContentPage() {
  const { project, edit, openProject } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [report, setReport] = useState<ImportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [replaceScenes, setReplaceScenes] = useState(true)
  const fileInput = useRef<HTMLInputElement>(null)

  if (!project) {
    return (
      <div className="page">
        <h1>Metinler</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  async function importFile(file: File) {
    if (!project) return
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      const result = await api.importContentFile(project.slug, file, replaceScenes, true)
      setReport(result.report)
      await openProject(project.slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  async function downloadExample() {
    try {
      const example = await api.contentExample()
      const blob = new Blob([JSON.stringify(example, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'example-content-package.json'
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(describeError(err))
    }
  }

  async function exportContent() {
    if (!project) return
    try {
      const content = await api.exportContent(project.slug)
      const blob = new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${project.slug}-content.json`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(describeError(err))
    }
  }

  const longIntro = longIntroOf(project)

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Metinler</h1>
          <p className="page-subtitle">
            Videonun konuşma metinleri, başlıkları ve açıklaması. Hazır bir dosya yükleyip her
            sahneyi tek seferde doldurabilir ya da alanları elle yazabilirsiniz.
          </p>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <section className="card">
        <h2>Hazır metin dosyası yükle</h2>
        <p className="muted">
          İçinde tüm sahnelerin konuşma metni, başlığı ve görsel açıklaması olan bir dosya
          (JSON). Yüklemek görünüm, ses ve video ayarlarınıza dokunmaz.
        </p>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={replaceScenes}
            onChange={(e) => setReplaceScenes(e.target.checked)}
          />
          Mevcut sahnelerin yerine geçsin
          <span className="hint">
            {replaceScenes
              ? 'Sahneler dosyadan yeniden kurulur. Sahnelerde yaptığınız ince ayarlar kaybolur.'
              : 'Mevcut sahneler güncellenir; sesler ve elle verdiğiniz süreler korunur.'}
          </span>
        </label>

        <div className="row">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            aria-label="Metin dosyası (JSON)"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void importFile(file)
              e.target.value = ''
            }}
          />
          <button onClick={() => void downloadExample()} disabled={busy}>
            Örnek dosyayı indir
          </button>
          <button onClick={() => void exportContent()} disabled={busy}>
            Bu projenin metinlerini indir
          </button>
        </div>

        {busy && <p className="muted">Yükleniyor…</p>}

        {report && (
          <div className="import-report">
            <h3>Yükleme tamamlandı</h3>
            <ul>
              {report.scenesCreated > 0 && <li>{report.scenesCreated} sahne oluşturuldu</li>}
              {report.scenesUpdated > 0 && <li>{report.scenesUpdated} sahne güncellendi</li>}
              <li>{report.imagesMapped} görsel eşleştirildi</li>
              {report.introImage && <li>Giriş görseli: {report.introImage}</li>}
              {report.longIntroApplied && <li>Video açılışı dosyadan alındı</li>}
              {report.ttsApplied && <li>Seslendirme sesi dosyadan alındı</li>}
              {report.shortsWithHook != null && report.shortsWithHook > 0 && (
                <li>{report.shortsWithHook} kısa videonun açılış metni hazır</li>
              )}
            </ul>
            {report.warnings.length > 0 && (
              <div className="warnings">
                {report.warnings.map((warning) => (
                  <p key={warning}>⚠ {warning}</p>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="card">
        <h2>Video bilgileri</h2>
        <div className="field-grid">
          <label>
            Hayvanın adı
            <input
              value={project.animal.commonName}
              onChange={(e) => edit((d) => void (d.animal.commonName = e.target.value))}
            />
          </label>
          <label>
            Latince adı
            <input
              value={project.animal.scientificName}
              onChange={(e) => edit((d) => void (d.animal.scientificName = e.target.value))}
            />
          </label>
          <label className="span-2">
            Video başlığı
            <input
              value={project.metadata.videoTitle}
              onChange={(e) => edit((d) => void (d.metadata.videoTitle = e.target.value))}
            />
          </label>
          <label className="span-2">
            YouTube açıklaması
            <textarea
              rows={8}
              value={project.metadata.description}
              onChange={(e) => edit((d) => void (d.metadata.description = e.target.value))}
            />
          </label>
          <label>
            Kapak görselindeki yazı
            <input
              value={project.metadata.thumbnailText}
              onChange={(e) => edit((d) => void (d.metadata.thumbnailText = e.target.value))}
            />
          </label>
          <label>
            Kapak görseli için açıklama
            <input
              value={project.metadata.thumbnailPrompt}
              onChange={(e) => edit((d) => void (d.metadata.thumbnailPrompt = e.target.value))}
            />
          </label>
        </div>
      </section>

      <section className="card">
        <div className="section-head">
          <div>
            <h2>Video açılışı</h2>
            <p className="muted">
              Uzun videodan önce oynayan kanal açılışı: hayvanın adı daktilo efektiyle
              yazılır, altına Latince adı gelir, üstüne kırmızı damga vurulur. Kart tamamen
              solup bittikten sonra ilk sahne, anlatım ve normal yazılar başlar. Daktilo ve
              damga sesleri otomatik üretilir; kısa videolara girmez.
            </p>
          </div>
        </div>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={longIntro.enabled}
            onChange={(e) => edit((d) => void (d.longIntro = { ...longIntro, enabled: e.target.checked }))}
          />
          Açılış kartı kullanılsın
          <span className="hint">
            {longIntro.enabled
              ? 'Her videonun başında aynı açılış görünür. Kapatırsanız video doğrudan ilk sahneyle başlar.'
              : 'Kapalı. Bu video doğrudan ilk sahneyle başlar.'}
          </span>
        </label>

        {longIntro.enabled && (
          <div className="field-grid">
            <label>
              Üst yazı
              <input
                value={longIntro.primaryTitle}
                placeholder={project.animal.commonName || 'Hayvanın adı'}
                onChange={(e) =>
                  edit((d) => void (d.longIntro = { ...longIntro, primaryTitle: e.target.value }))
                }
              />
              <span className="hint">Boş bırakırsanız hayvanın adı kullanılır.</span>
            </label>
            <label>
              Alt yazı
              <input
                value={longIntro.secondaryTitle}
                placeholder={project.animal.scientificName || 'Latince adı'}
                onChange={(e) =>
                  edit((d) => void (d.longIntro = { ...longIntro, secondaryTitle: e.target.value }))
                }
              />
              <span className="hint">Boş bırakırsanız Latince ad kullanılır.</span>
            </label>
            <label>
              Damga yazısı
              <input
                value={longIntro.stampText}
                maxLength={24}
                onChange={(e) =>
                  edit((d) => void (d.longIntro = { ...longIntro, stampText: e.target.value }))
                }
              />
              <span className="hint">Boş bırakırsanız damga vurulmaz.</span>
            </label>
            <label>
              Süre (sn)
              <input
                type="number"
                step={0.1}
                min={0.8}
                max={6}
                value={longIntro.duration}
                onChange={(e) =>
                  edit((d) => {
                    const duration = Number(e.target.value)
                    if (!Number.isFinite(duration)) return
                    // The typing and the stamp must both still fit inside it;
                    // the schema rejects an intro whose parts outlast it.
                    d.longIntro = {
                      ...longIntro,
                      duration,
                      typewriterDuration: Math.min(longIntro.typewriterDuration, duration),
                      stampAt: Math.min(longIntro.stampAt, duration),
                    }
                  })
                }
              />
              <span className="hint">Önerilen: 2–3 saniye.</span>
            </label>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Giriş konuşması</h2>
        <textarea
          rows={4}
          value={project.intro.narration}
          onChange={(e) => edit((d) => void (d.intro.narration = e.target.value))}
          placeholder="Videonun başında, ilk görüntünün üzerinde okunur."
        />
        <h2>Kapanış konuşması</h2>
        <textarea
          rows={4}
          value={project.outro.narration}
          onChange={(e) => edit((d) => void (d.outro.narration = e.target.value))}
          placeholder="Kapanış cümlesi, abone olma daveti, sonraki bölümün tanıtımı."
        />
      </section>
    </div>
  )
}
