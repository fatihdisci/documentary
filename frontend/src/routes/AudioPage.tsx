import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { GenerateResponse, TimingResponse, TTSProviderStatus, Voice } from '@/api/audio-types'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import './AudioPage.css'

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60)
  const seconds = value % 60
  return `${minutes}:${seconds.toFixed(1).padStart(4, '0')}`
}

export function AudioPage() {
  const { project, edit, openProject } = useProjectStore()
  const [providers, setProviders] = useState<TTSProviderStatus[]>([])
  const [voices, setVoices] = useState<Voice[]>([])
  const [timing, setTiming] = useState<TimingResponse | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [lastRun, setLastRun] = useState<GenerateResponse | null>(null)
  const [voiceFilter, setVoiceFilter] = useState('en-')
  const uploadTarget = useRef<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const slug = project?.slug ?? null
  const providerName = project?.audio.ttsProvider ?? 'edge'

  const refreshTiming = useCallback(async () => {
    if (!slug) return
    try {
      setTiming(await api.getTiming(slug))
    } catch (err) {
      setError(describeError(err))
    }
  }, [slug])

  useEffect(() => {
    void api
      .listProviders()
      .then((r) => setProviders(r.providers))
      .catch((err) => setError(describeError(err)))
  }, [])

  useEffect(() => {
    void refreshTiming()
  }, [refreshTiming])

  useEffect(() => {
    if (providerName === 'imported') {
      setVoices([])
      return
    }
    void api
      .listVoices(providerName)
      .then(setVoices)
      .catch(() => setVoices([])) // a missing voice list is not fatal
  }, [providerName])

  if (!project || !slug) {
    return (
      <div className="page">
        <h1>Seslendirme</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  const provider = providers.find((p) => p.name === providerName)
  const filtered = voiceFilter
    ? voices.filter(
        (v) =>
          v.locale.toLowerCase().includes(voiceFilter.toLowerCase()) ||
          v.name.toLowerCase().includes(voiceFilter.toLowerCase()),
      )
    : voices

  const units = [
    ...(project.intro.enabled && project.intro.narration.trim()
      ? [{ id: 'intro', label: 'Giriş', unit: project.intro }]
      : []),
    ...project.scenes.map((s, i) => ({
      id: s.id,
      label: s.title || `Sahne ${i + 1}`,
      unit: s,
    })),
    ...(project.outro.enabled && project.outro.narration.trim()
      ? [{ id: 'outro', label: 'Kapanış', unit: project.outro }]
      : []),
  ]

  const missing = units.filter((u) => !u.unit.audioFile && u.unit.narration.trim()).length

  async function generate(unitIds: string[], force = false) {
    setBusy(true)
    setError(null)
    try {
      const result = await api.generateNarration(slug!, unitIds, force)
      setLastRun(result)
      await openProject(slug!)
      await refreshTiming()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  async function uploadAudio(file: File) {
    const unitId = uploadTarget.current
    if (!unitId) return
    setBusy(true)
    setError(null)
    try {
      await api.importAudio(slug!, unitId, file)
      await openProject(slug!)
      await refreshTiming()
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
      uploadTarget.current = null
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Seslendirme</h1>
          <p className="page-subtitle">
            Metinleriniz seslendirilir. Her sahne, kendi ses kaydı kadar ekranda kalır.
          </p>
        </div>
        <div className="header-actions">
          <button className="primary" onClick={() => void generate([])} disabled={busy || !missing}>
            {busy ? 'Çalışıyor…' : `Eksikleri seslendir (${missing})`}
          </button>
          <button onClick={() => void generate([], true)} disabled={busy}>
            Hepsini yeniden seslendir
          </button>
        </div>
      </header>

      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}

      <input
        ref={fileInput}
        type="file"
        accept="audio/wav,audio/mpeg,audio/mp4,.wav,.mp3,.m4a"
        hidden
        aria-label="Hazır ses dosyası yükle"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void uploadAudio(file)
          e.target.value = ''
        }}
      />

      <section className="card">
        <h2>Ses</h2>
        <div className="field-grid">
          <label>
            Ses kaynağı
            <select
              value={providerName}
              onChange={(e) =>
                edit((d) => void (d.audio.ttsProvider = e.target.value as typeof d.audio.ttsProvider))
              }
            >
              {providers.map((p) => (
                <option key={p.name} value={p.name} disabled={!p.available}>
                  {p.name}
                  {p.available ? '' : ' (kullanılamıyor)'}
                </option>
              ))}
            </select>
            {provider && <span className="hint">{provider.message}</span>}
          </label>

          {providerName !== 'imported' && (
            <label>
              Konuşmacı
              <select
                value={project.audio.voice}
                onChange={(e) => edit((d) => void (d.audio.voice = e.target.value))}
              >
                <option value={project.audio.voice}>{project.audio.voice}</option>
                {filtered
                  .filter((v) => v.id !== project.audio.voice)
                  .map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.id} — {v.gender}
                    </option>
                  ))}
              </select>
              <span className="hint">
                <input
                  className="inline-filter"
                  value={voiceFilter}
                  onChange={(e) => setVoiceFilter(e.target.value)}
                  placeholder="ara — örneğin tr-TR"
                  aria-label="Konuşmacı ara"
                />
                {voices.length} konuşmacı var
              </span>
            </label>
          )}

          <label>
            Konuşma hızı
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={project.audio.speechRate}
              onChange={(e) => edit((d) => void (d.audio.speechRate = Number(e.target.value)))}
            />
            <span className="hint">
              {project.audio.speechRate.toFixed(2)}× — bunu değiştirirseniz tüm seslendirme
              yeniden yapılır.
            </span>
          </label>

          <label>
            Sahne süreleri nasıl belirlensin?
            <select
              value={project.video.durationMode}
              onChange={(e) =>
                edit((d) => void (d.video.durationMode = e.target.value as typeof d.video.durationMode))
              }
            >
              <option value="audio">Konuşmaya göre (önerilen)</option>
              <option value="target">Hedef süreye göre (sahneler uzatılır)</option>
              <option value="manual">Elle (süreleri siz belirlersiniz)</option>
            </select>
          </label>
        </div>
      </section>

      {timing && (
        <section className="card">
          <h2>Tahmini video süresi</h2>
          <div className="timing-grid">
            <div className="timing-stat big">
              <span className="value">{String(timing.summary.totalFormatted ?? '—')}</span>
              <span className="label">Toplam süre</span>
            </div>
            <div className="timing-stat">
              <span className="value">{formatSeconds(Number(timing.summary.narrationSeconds))}</span>
              <span className="label">Konuşma</span>
            </div>
            <div className="timing-stat">
              <span className="value">{Number(timing.summary.transitionSeconds).toFixed(1)}s</span>
              <span className="label">Geçişler</span>
            </div>
            <div className="timing-stat">
              <span className="value">{formatSeconds(Number(timing.summary.introSeconds))}</span>
              <span className="label">Giriş</span>
            </div>
            <div className="timing-stat">
              <span className="value">{formatSeconds(Number(timing.summary.outroSeconds))}</span>
              <span className="label">Kapanış</span>
            </div>
            <div className="timing-stat">
              <span
                className={`value ${
                  Math.abs(Number(timing.summary.differenceSeconds)) > 45 ? 'off-target' : ''
                }`}
              >
                {Number(timing.summary.differenceSeconds) >= 0 ? '+' : ''}
                {Number(timing.summary.differenceSeconds).toFixed(0)}s
              </span>
              <span className="label">hedefe göre fark</span>
            </div>
            <div className="timing-stat">
              <span className="value">{timing.cueCount}</span>
              <span className="label">Altyazı satırı</span>
            </div>
          </div>

          {timing.warnings.length > 0 && (
            <div className="warnings">
              {timing.warnings.map((w) => (
                <p key={w}>⚠ {w}</p>
              ))}
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            <a className="button-link" href={`/api/projects/${slug}/audio/subtitles.srt`} download>
              Altyazı dosyasını indir (.srt)
            </a>
          </div>
        </section>
      )}

      <section className="card">
        <h2>Bölüm bölüm seslendirme</h2>
        <table className="audio-table">
          <thead>
            <tr>
              <th>Bölüm</th>
              <th>Kaynak</th>
              <th>Süre</th>
              <th>Ses</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {units.map((u) => (
              <tr key={u.id}>
                <td className="unit-label">{u.label}</td>
                <td>
                  <span className={`tag ${u.unit.audioSource === 'none' ? 'tag-warn' : ''}`}>
                    {u.unit.audioSource === 'none'
                      ? 'yok'
                      : u.unit.audioSource === 'generated'
                        ? 'seslendirildi'
                        : 'dışarıdan'}
                  </span>
                </td>
                <td>
                  {u.unit.audioDurationSeconds != null
                    ? `${u.unit.audioDurationSeconds.toFixed(2)}s`
                    : '—'}
                </td>
                <td>
                  {u.unit.audioFile ? (
                    <audio
                      controls
                      preload="none"
                      src={`/api/projects/${slug}/media/audio/${
                        u.unit.audioFile.includes('/imported/') ? 'imported' : 'generated'
                      }/${u.unit.audioFile.split('/').pop()}`}
                    />
                  ) : (
                    <span className="muted">ses yok</span>
                  )}
                </td>
                <td className="unit-actions">
                  <button
                    onClick={() => void generate([u.id], true)}
                    disabled={busy || !u.unit.narration.trim() || providerName === 'imported'}
                    title={
                      providerName === 'imported'
                        ? 'Seslendirme için ses kaynağını Edge yapın'
                        : 'Sadece bu bölümü yeniden seslendir'
                    }
                  >
                    Seslendir
                  </button>
                  <button
                    onClick={() => {
                      uploadTarget.current = u.id
                      fileInput.current?.click()
                    }}
                    disabled={busy}
                  >
                    Ses yükle
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {units.length === 0 && <p className="muted">Henüz metni olan sahne yok.</p>}
      </section>

      {lastRun && (
        <section className="card">
          <h2>Son işlem</h2>
          <p className="muted">
            {lastRun.generatedCount} bölüm seslendirildi, {lastRun.reusedCount} bölüm hazırdan
            kullanıldı.
          </p>
        </section>
      )}

      <section className="card">
        <h2>Ses dengesi</h2>
        <div className="field-grid">
          <label>
            Konuşma seviyesi (dB)
            <input
              type="number"
              step={0.5}
              min={-40}
              max={10}
              value={project.audio.voiceVolumeDb}
              onChange={(e) => edit((d) => void (d.audio.voiceVolumeDb = Number(e.target.value)))}
            />
          </label>
          <label>
            Müzik seviyesi (dB)
            <input
              type="number"
              step={0.5}
              min={-60}
              max={0}
              value={project.audio.musicVolumeDb}
              onChange={(e) => edit((d) => void (d.audio.musicVolumeDb = Number(e.target.value)))}
            />
          </label>
          <label>
            Müzik
            <select
              value={project.music.source}
              onChange={(e) =>
                edit((d) => {
                  d.music.source = e.target.value as typeof d.music.source
                  if (d.music.source !== 'uploaded') d.music.file = null
                })
              }
            >
              <option value="none">Müzik yok</option>
              <option value="uploaded">Yüklediğim müzik</option>
              <option value="generated-ambient">Basit fon müziği (uygulama üretir)</option>
            </select>
            <span className="hint">
              {project.music.source === 'generated-ambient'
                ? 'Basit bir fon sesi. Denemek için iyidir; yayınlarken gerçek bir parça kullanın.'
                : project.music.source === 'none'
                  ? 'Arka planda müzik çalmaz.'
                  : project.music.file
                    ? `“${project.music.file}” kullanılıyor. Parçaları Müzik sekmesinden yönetin.`
                    : 'Henüz parça seçilmedi — Müzik sekmesinden bir parça yükleyip seçin.'}
            </span>
          </label>
          <label>
            Genel ses seviyesi (LUFS)
            <input
              type="number"
              step={0.5}
              min={-30}
              max={-8}
              value={project.audio.targetLufs}
              onChange={(e) => edit((d) => void (d.audio.targetLufs = Number(e.target.value)))}
            />
            <span className="hint">YouTube için −16 uygundur.</span>
          </label>
        </div>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.audio.duckMusicUnderSpeech}
            onChange={(e) => edit((d) => void (d.audio.duckMusicUnderSpeech = e.target.checked))}
          />
          Konuşma varken müziği otomatik kıs
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={project.subtitles.burnIn}
            onChange={(e) => edit((d) => void (d.subtitles.burnIn = e.target.checked))}
          />
          Altyazıyı videonun içine göm
          <span className="hint">
            Açık gelir, böylece video altyazılı olur. Ayrı bir .srt dosyası her hâlükârda
            oluşur; görüntünün temiz kalmasını isterseniz bunu kapatın.
          </span>
        </label>
      </section>
    </div>
  )
}
