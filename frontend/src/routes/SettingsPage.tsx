import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload, AppSettings, SettingsResponse } from '@/api/types'
import { ErrorBox } from '@/components/ErrorBox'
import './SettingsPage.css'

export function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<AppSettings | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [apiKey, setApiKey] = useState('')

  const load = useCallback(async () => {
    try {
      const response = await api.getSettings()
      setData(response)
      setDraft(response.settings)
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function set<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current))
    setSaved(false)
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    setError(null)
    try {
      const response = await api.updateSettings(draft)
      setData(response)
      setDraft(response.settings)
      setSaved(true)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  async function saveKey(value: string | null) {
    setBusy(true)
    try {
      setData(await api.setSecret('elevenlabs_api_key', value))
      setApiKey('')
      setError(null)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  if (!draft || !data) {
    return (
      <div className="page">
        <h1>Ayarlar</h1>
        {error ? <ErrorBox error={error} onRetry={() => void load()} /> : <p className="muted">Yükleniyor…</p>}
      </div>
    )
  }

  const keyConfigured = data.configuredSecrets.includes('elevenlabs_api_key')

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Ayarlar</h1>
          <p className="page-subtitle">
            Buradaki ayarlar yeni projeler için geçerlidir. Mevcut projeler kendi ayarlarını korur.
          </p>
        </div>
        <div className="header-actions">
          {saved && <span className="saved-pill">Kaydedildi</span>}
          <button className="primary" onClick={() => void save()} disabled={busy}>
            {busy ? 'Kaydediliyor…' : 'Ayarları kaydet'}
          </button>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <section className="card">
        <h2>Video motoru</h2>
        <div className="field-grid">
          <label>
            FFmpeg konumu
            <input value={draft.ffmpegPath} onChange={(e) => set('ffmpegPath', e.target.value)} />
            <span className="hint">Bulunan: {data.resolvedPaths.ffmpeg || 'bulunamadı'}</span>
          </label>
          <label>
            ffprobe konumu
            <input value={draft.ffprobePath} onChange={(e) => set('ffprobePath', e.target.value)} />
            <span className="hint">Bulunan: {data.resolvedPaths.ffprobe || 'bulunamadı'}</span>
          </label>
        </div>
        <p className="hint">
          Bilmiyorsanız <code>ffmpeg</code> ve <code>ffprobe</code> olarak bırakın; uygulama
          bilgisayarınızda kendisi arar.
        </p>
      </section>

      <section className="card">
        <h2>Dosya konumları</h2>
        <div className="field-grid">
          <label>
            Projeler klasörü
            <input
              value={draft.projectsDir}
              placeholder={data.resolvedPaths.projectsDir}
              onChange={(e) => set('projectsDir', e.target.value)}
            />
          </label>
          <label>
            Hazır videolar klasörü
            <input
              value={draft.exportsDir}
              placeholder={data.resolvedPaths.exportsDir}
              onChange={(e) => set('exportsDir', e.target.value)}
            />
          </label>
          <label>
            Geçici dosyalar klasörü
            <input
              value={draft.tempDir}
              placeholder={data.resolvedPaths.tempDir}
              onChange={(e) => set('tempDir', e.target.value)}
            />
          </label>
          <label>
            Başarısız denemelerin geçici dosyaları kaç gün saklansın?
            <input
              type="number"
              min={0}
              max={90}
              value={draft.tempRetentionDays}
              onChange={(e) => set('tempRetentionDays', Number(e.target.value))}
            />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.cleanupTempOnSuccess}
            onChange={(e) => set('cleanupTempOnSuccess', e.target.checked)}
          />
          Video başarıyla oluşunca geçici dosyaları sil
        </label>
      </section>

      <section className="card">
        <h2>Yeni projeler için varsayılanlar</h2>
        <div className="field-grid">
          <label>
            Çözünürlük
            <select
              value={`${draft.defaultWidth}x${draft.defaultHeight}`}
              onChange={(e) => {
                const [w, h] = e.target.value.split('x').map(Number)
                set('defaultWidth', w ?? 1920)
                set('defaultHeight', h ?? 1080)
              }}
            >
              <option value="1920x1080">1920 × 1080 (1080p)</option>
              <option value="2560x1440">2560 × 1440 (1440p)</option>
              <option value="3840x2160">3840 × 2160 (4K)</option>
            </select>
          </label>
          <label>
            Saniyedeki kare sayısı
            <select value={draft.defaultFps} onChange={(e) => set('defaultFps', Number(e.target.value))}>
              <option value={60}>60 fps</option>
              <option value={30}>30 fps</option>
              <option value={24}>24 fps</option>
            </select>
          </label>
          <label>
            Sahne geçişi
            <select
              value={draft.defaultTransition}
              onChange={(e) => set('defaultTransition', e.target.value as AppSettings['defaultTransition'])}
            >
              <option value="documentary-dissolve">Belgesel geçişi</option>
              <option value="cross-dissolve">Yumuşak geçiş</option>
              <option value="fade-through-black">Siyaha kararıp açılma</option>
              <option value="none">Geçiş yok</option>
            </select>
            <span className="hint">Burada sadece sakin, göze batmayan geçişler sunulur.</span>
          </label>
          <label>
            Video kalitesi
            <select
              value={draft.defaultQuality}
              onChange={(e) => set('defaultQuality', e.target.value as AppSettings['defaultQuality'])}
            >
              <option value="youtube-hq">YouTube kalitesi</option>
              <option value="high">Yüksek</option>
              <option value="standard">Normal</option>
              <option value="preview">Hızlı deneme (düşük kalite)</option>
            </select>
          </label>
          <label>
            Sahne başı sessizlik (saniye)
            <input
              type="number"
              step={0.05}
              min={0}
              max={5}
              value={draft.defaultSceneLeadInSeconds}
              onChange={(e) => set('defaultSceneLeadInSeconds', Number(e.target.value))}
            />
            <span className="hint">Sahne başladıktan kaç saniye sonra konuşma başlasın?</span>
          </label>
          <label>
            Sahne sonu bekleme (saniye)
            <input
              type="number"
              step={0.05}
              min={0}
              max={10}
              value={draft.defaultSceneTailSeconds}
              onChange={(e) => set('defaultSceneTailSeconds', Number(e.target.value))}
            />
            <span className="hint">Konuşma bittikten sonra görüntü ne kadar ekranda kalsın?</span>
          </label>
          <label>
            Ara dosya biçimi
            <select
              value={draft.intermediateCodec}
              onChange={(e) => set('intermediateCodec', e.target.value as AppSettings['intermediateCodec'])}
            >
              <option value="h264-crf14-fast">H.264 CRF 14 (hızlı, az yer kaplar)</option>
              <option value="h264-crf12">H.264 CRF 12 (daha kaliteli)</option>
              <option value="prores-lt">ProRes 422 LT (çok yer kaplar, çok hızlı)</option>
              <option value="prores-422">ProRes 422 (en çok yer kaplar)</option>
            </select>
            <span className="hint">
              Sadece sahnelerin geçici dosyaları için kullanılır; bitmiş videoyu etkilemez.
            </span>
          </label>
          <label>
            Varsayılan konuşmacı
            <input value={draft.defaultVoice} onChange={(e) => set('defaultVoice', e.target.value)} />
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.useHardwareEncoder}
            onChange={(e) => set('useHardwareEncoder', e.target.checked)}
          />
          Mümkünse ekran kartını kullan
          <span className="hint">
            Daha hızlı olur, ama aynı dosya boyutunda kalite biraz düşer.
          </span>
        </label>
      </section>

      <section className="card">
        <h2>Servis anahtarları</h2>
        <p className="muted">
          Sadece sizin okuyabileceğiniz bir dosyada saklanır. Anahtarlar hiçbir zaman ekrana
          yazılmaz, kayıt dosyalarına düşmez ve proje yedeğine eklenmez.
        </p>
        <div className="row">
          <input
            type="password"
            value={apiKey}
            placeholder={keyConfigured ? '•••••••• (kayıtlı)' : 'ElevenLabs anahtarı (isteğe bağlı)'}
            onChange={(e) => setApiKey(e.target.value)}
            aria-label="ElevenLabs anahtarı"
          />
          <button onClick={() => void saveKey(apiKey)} disabled={!apiKey || busy}>
            Anahtarı kaydet
          </button>
          {keyConfigured && (
            <button className="danger" onClick={() => void saveKey(null)} disabled={busy}>
              Anahtarı sil
            </button>
          )}
        </div>
        <p className="hint">
          Zorunlu değil. Edge seslendirmesi ücretsizdir ve anahtar istemez; dilerseniz kendi ses
          kayıtlarınızı da yükleyebilirsiniz.
        </p>
      </section>

      <section className="card">
        <h2>Sınırlar</h2>
        <div className="field-grid">
          <label>
            En büyük dosya yükleme boyutu (MB)
            <input
              type="number"
              min={1}
              max={2048}
              value={draft.maxUploadMb}
              onChange={(e) => set('maxUploadMb', Number(e.target.value))}
            />
          </label>
          <label>
            En büyük metin dosyası boyutu (MB)
            <input
              type="number"
              min={1}
              max={256}
              value={draft.maxJsonMb}
              onChange={(e) => set('maxJsonMb', Number(e.target.value))}
            />
          </label>
          <label>
            Boş bırakılacak disk alanı (MB)
            <input
              type="number"
              min={0}
              max={102400}
              value={draft.diskSafetyMarginMb}
              onChange={(e) => set('diskSafetyMarginMb', Number(e.target.value))}
            />
            <span className="hint">
              Diskte bu kadar boş yer kalmayacaksa video oluşturma başlatılmaz.
            </span>
          </label>
          <label>
            Kayıt ayrıntısı
            <select value={draft.logLevel} onChange={(e) => set('logLevel', e.target.value)}>
              <option value="DEBUG">Her şey (çok ayrıntılı)</option>
              <option value="INFO">Normal</option>
              <option value="WARNING">Sadece uyarılar</option>
              <option value="ERROR">Sadece hatalar</option>
            </select>
          </label>
        </div>
      </section>
    </div>
  )
}
