/**
 * Guided setup — a linear, minimal-decision path for a first-time user.
 *
 * Distinct from the full editor: it walks the six things that actually have to
 * happen — content → images → voice → music → narration → render — one at a
 * time, driving the same API and stores the individual tabs use. Every step
 * links out to its full editor tab for anyone who wants the fine controls.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { TTSProviderStatus, Voice } from '@/api/audio-types'
import { useProjectStore } from '@/store/project'
import { useRenderStore } from '@/store/render'
import { ErrorBox } from '@/components/ErrorBox'
import './SimpleModePage.css'

interface StepProps {
  goTo: (tab: string) => void
}

const STEPS = ['Content', 'Images', 'Voice', 'Music', 'Narration', 'Render'] as const
type StepName = (typeof STEPS)[number]

/** Adım adları ekranda Türkçe görünür; anahtarlar kodda sabit kalır. */
const STEP_LABEL: Record<StepName, string> = {
  Content: 'Metinler',
  Images: 'Görseller',
  Voice: 'Ses',
  Music: 'Müzik',
  Narration: 'Seslendirme',
  Render: 'Video',
}

export function SimpleModePage({ goTo }: { goTo: (tab: string) => void }) {
  const { project, images } = useProjectStore()
  const [step, setStep] = useState(0)

  if (!project) {
    return (
      <div className="page">
        <h1>Kolay kurulum</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  const audioUnits = [
    ...(project.intro.enabled && project.intro.narration.trim() ? [project.intro] : []),
    ...project.scenes,
    ...(project.outro.enabled && project.outro.narration.trim() ? [project.outro] : []),
  ]
  const missingAudio = audioUnits.filter((u) => u.narration.trim() && !u.audioFile).length
  const mappedScenes = project.scenes.filter((s) => s.imageFile).length

  // What each step needs before it counts as "done".
  const done: Record<StepName, boolean> = {
    Content: project.scenes.length > 0,
    Images: images.length > 0 && mappedScenes === project.scenes.length,
    Voice: true, // there is always a default voice
    Music: true, // optional
    Narration: audioUnits.length > 0 && missingAudio === 0,
    Render: false,
  }

  const current = STEPS[step]

  return (
    <div className="page simple-mode">
      <header className="page-header">
        <div>
          <h1>Kolay kurulum</h1>
          <p className="page-subtitle">
            Altı adımda videonuz hazır. Daha fazla ayar isterseniz her adımdan ilgili sekmeye
            geçebilirsiniz.
          </p>
        </div>
        <button onClick={() => goTo('content')}>Tüm ayarları göster</button>
      </header>

      <ol className="stepper" aria-label="Adımlar">
        {STEPS.map((name, i) => (
          <li key={name}>
            <button
              className={`step-chip ${i === step ? 'active' : ''} ${done[name] ? 'done' : ''}`}
              onClick={() => setStep(i)}
              aria-current={i === step ? 'step' : undefined}
            >
              <span className="step-num">{done[name] ? '✓' : i + 1}</span>
              {STEP_LABEL[name]}
            </button>
          </li>
        ))}
      </ol>

      <section className="card simple-step">
        {current === 'Content' && <ContentStep goTo={goTo} />}
        {current === 'Images' && <ImagesStep goTo={goTo} />}
        {current === 'Voice' && <VoiceStep goTo={goTo} />}
        {current === 'Music' && <MusicStep goTo={goTo} />}
        {current === 'Narration' && <NarrationStep goTo={goTo} />}
        {current === 'Render' && <RenderStep goTo={goTo} />}
      </section>

      <div className="simple-nav">
        <button onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
          ← Geri
        </button>
        <span className="simple-progress">
          Adım {step + 1} / {STEPS.length}
        </span>
        <button
          className="primary"
          onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
          disabled={step === STEPS.length - 1}
        >
          İleri →
        </button>
      </div>
    </div>
  )
}

function ContentStep({ goTo }: StepProps) {
  const { project, openProject } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const slug = project!.slug

  async function importFile(file: File) {
    setBusy(true)
    setError(null)
    try {
      await api.importContentFile(slug, file, true, true)
      await openProject(slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>1 · Metinler</h2>
      <p className="muted">
        Videonun konuşma metinlerini ve başlıklarını içeren hazır bir dosya yükleyin. Tüm sahneler
        tek seferde dolar.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        hidden
        aria-label="Metin dosyası (JSON)"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void importFile(file)
          e.target.value = ''
        }}
      />
      <div className="row">
        <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy ? 'Yükleniyor…' : 'Metin dosyası yükle'}
        </button>
        <button onClick={() => goTo('content')}>Örneği indir / elle yaz →</button>
      </div>
      <p className={`step-status ${project!.scenes.length > 0 ? 'ok' : ''}`}>
        {project!.scenes.length > 0
          ? `✓ ${project!.scenes.length} sahne hazır.`
          : 'Henüz sahne yok.'}
      </p>
    </div>
  )
}

function ImagesStep({ goTo }: StepProps) {
  const { project, images, openProject } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const slug = project!.slug
  const mapped = project!.scenes.filter((s) => s.imageFile).length

  async function upload(files: File[]) {
    if (files.length === 0) return
    setBusy(true)
    setError(null)
    try {
      await api.uploadImages(slug, files)
      await openProject(slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>2 · Görseller</h2>
      <p className="muted">
        Her sahne için bir görsel yükleyin. Dosyaları sırayla adlandırın —{' '}
        <code>01-acilis.png</code>, <code>02-yasam-alani.png</code> — sahnelere kendiliğinden
        dağıtılırlar.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp"
        hidden
        aria-label="Sahne görselleri"
        onChange={(e) => {
          void upload(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />
      <div className="row">
        <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy ? 'Yükleniyor…' : 'Görsel yükle'}
        </button>
        <button onClick={() => goTo('scenes')}>Sahneleri düzenle →</button>
      </div>
      <p className={`step-status ${mapped === project!.scenes.length && images.length > 0 ? 'ok' : ''}`}>
        {images.length === 0
          ? 'Henüz görsel yok.'
          : `${images.length} görsel yüklendi · ${project!.scenes.length} sahnenin ${mapped} tanesinde görsel var.`}
      </p>
    </div>
  )
}

function VoiceStep({ goTo }: StepProps) {
  const { project, edit } = useProjectStore()
  const [providers, setProviders] = useState<TTSProviderStatus[]>([])
  const [voices, setVoices] = useState<Voice[]>([])
  const providerName = project!.audio.ttsProvider

  useEffect(() => {
    void api.listProviders().then((r) => setProviders(r.providers)).catch(() => setProviders([]))
  }, [])
  useEffect(() => {
    if (providerName === 'imported') return setVoices([])
    void api.listVoices(providerName).then(setVoices).catch(() => setVoices([]))
  }, [providerName])

  const enVoices = voices.filter((v) => v.locale.toLowerCase().startsWith('en'))

  return (
    <div>
      <h2>3 · Ses</h2>
      <p className="muted">
        Metinleri kim okusun? Edge ücretsizdir, kayıt gerektirmez, sadece internet ister. İsterseniz
        kendi ses kayıtlarınızı da yükleyebilirsiniz.
      </p>
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
        </label>
        {providerName !== 'imported' && (
          <label>
            Konuşmacı
            <select
              value={project!.audio.voice}
              onChange={(e) => edit((d) => void (d.audio.voice = e.target.value))}
            >
              <option value={project!.audio.voice}>{project!.audio.voice}</option>
              {enVoices
                .filter((v) => v.id !== project!.audio.voice)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.id} — {v.gender}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
      <div className="row">
        <button onClick={() => void goTo('audio')}>Hız ve ses dengesi →</button>
      </div>
      <p className="step-status ok">
        ✓ Seslendiren: {providerName === 'imported' ? 'kendi ses kayıtlarınız' : project!.audio.voice}.
      </p>
    </div>
  )
}

function MusicStep({ goTo }: StepProps) {
  const { project, edit, openProject, save } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const slug = project!.slug
  const source = project!.music.source

  async function upload(file: File) {
    setBusy(true)
    setError(null)
    try {
      const { filename } = await api.uploadMusic(slug, file)
      edit((d) => {
        d.music.source = 'uploaded'
        d.music.file = filename
      })
      await save()
      await openProject(slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>4 · Müzik <span className="optional">isteğe bağlı</span></h2>
      <p className="muted">Arka plan müziği zorunlu değil. Birini seçin:</p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac"
        hidden
        aria-label="Müzik dosyası"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void upload(file)
          e.target.value = ''
        }}
      />
      <div className="choice-row">
        <button
          className={source === 'none' ? 'primary' : ''}
          onClick={() => edit((d) => void ((d.music.source = 'none'), (d.music.file = null)))}
        >
          Müzik yok
        </button>
        <button className={source === 'uploaded' ? 'primary' : ''} onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy
            ? 'Yükleniyor…'
            : source === 'uploaded' && project!.music.file
              ? `Parça: ${project!.music.file}`
              : 'Müzik yükle'}
        </button>
        <button
          className={source === 'generated-ambient' ? 'primary' : ''}
          onClick={() => edit((d) => void ((d.music.source = 'generated-ambient'), (d.music.file = null)))}
        >
          Uygulama üretsin
        </button>
      </div>
      <div className="row">
        <button onClick={() => goTo('music')}>Müzik listesini yönet →</button>
      </div>
    </div>
  )
}

function NarrationStep({ goTo }: StepProps) {
  const { project, openProject } = useProjectStore()
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const slug = project!.slug

  const units = [
    ...(project!.intro.enabled && project!.intro.narration.trim() ? [project!.intro] : []),
    ...project!.scenes,
    ...(project!.outro.enabled && project!.outro.narration.trim() ? [project!.outro] : []),
  ]
  const missing = units.filter((u) => u.narration.trim() && !u.audioFile).length
  const importedProvider = project!.audio.ttsProvider === 'imported'

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      const r = await api.generateNarration(slug, [], false)
      setResult(`${r.generatedCount} bölüm seslendirildi, ${r.reusedCount} bölüm hazırdan kullanıldı.`)
      await openProject(slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>5 · Seslendirme</h2>
      <p className="muted">
        Her sahnenin sesi burada üretilir. Sahne süreleri de bu kayıtlara göre belirlenir.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      {importedProvider ? (
        <p className="step-status">
          Kendi ses kayıtlarınızı kullanmayı seçtiniz — her sahne için Seslendirme sekmesinden
          dosya yükleyin.
          <button className="linkish" onClick={() => goTo('audio')}>Seslendirmeyi aç →</button>
        </p>
      ) : (
        <>
          <div className="row">
            <button className="primary" onClick={() => void generate()} disabled={busy || missing === 0}>
              {busy ? 'Seslendiriliyor…' : `Seslendir (${missing} eksik)`}
            </button>
            <button onClick={() => goTo('audio')}>Sahne sahne ayar →</button>
          </div>
          {result && <p className="step-status ok">✓ {result}</p>}
          <p className={`step-status ${missing === 0 && units.length > 0 ? 'ok' : ''}`}>
            {units.length === 0
              ? 'Henüz metin yazılmamış — Metinler sekmesinden ekleyin.'
              : missing === 0
                ? '✓ Bütün sahnelerin sesi hazır.'
                : `${missing} sahnenin sesi eksik.`}
          </p>
        </>
      )}
    </div>
  )
}

function RenderStep({ goTo }: StepProps) {
  const { project } = useProjectStore()
  const { preflight, job, event, busy, error, loadPreflight, start, clearError } = useRenderStore()
  const slug = project!.slug

  const load = useCallback(() => void loadPreflight(slug), [loadPreflight, slug])
  useEffect(() => {
    load()
  }, [load])

  const running = job !== null && (job.status === 'queued' || job.status === 'running')
  const percent = Math.round((event?.progress ?? job?.progress ?? 0) * 100)

  return (
    <div>
      <h2>6 · Video</h2>
      <p className="muted">
        1920×1080 boyutunda, altyazılı bir MP4 dosyası ve yanında ayrı bir .srt altyazı dosyası
        oluşturulur.
      </p>
      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {preflight && !preflight.ready && (
        <div className="blocking">
          <strong>Önce bunları düzeltin:</strong>
          {preflight.blockingIssues.map((i) => (
            <p key={i}>✕ {i}</p>
          ))}
        </div>
      )}
      {preflight?.ready && !running && (
        <p className="step-status ok">
          ✓ Hazır — video yaklaşık {String(preflight.timing.totalFormatted ?? '—')} sürecek.
        </p>
      )}

      {running && (
        <div className="render-progress">
          <div className="progress-track" role="progressbar" aria-valuenow={percent}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
          <span>
            {percent}% · {event?.message ?? job?.message}
          </span>
        </div>
      )}

      <div className="row">
        <button
          className="primary"
          onClick={() => void start(slug)}
          disabled={busy || running || !preflight?.ready}
        >
          {busy || running ? 'Oluşturuluyor…' : 'Videoyu oluştur'}
        </button>
        <button onClick={() => goTo('export')}>İndirmek için Video sekmesini aç →</button>
      </div>

      {job && !running && job.status === 'completed' && (
        <p className="step-status ok">
          ✓ Bitti. İndirmek için{' '}
          <button className="linkish" onClick={() => goTo('export')}>
            Videoyu oluştur
          </button>{' '}
          sekmesine gidin.
        </p>
      )}
    </div>
  )
}
