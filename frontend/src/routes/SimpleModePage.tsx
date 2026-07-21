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

export function SimpleModePage({ goTo }: { goTo: (tab: string) => void }) {
  const { project, images } = useProjectStore()
  const [step, setStep] = useState(0)

  if (!project) {
    return (
      <div className="page">
        <h1>Guided setup</h1>
        <p className="page-subtitle">Open a project first.</p>
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
          <h1>Guided setup</h1>
          <p className="page-subtitle">
            Six steps to a finished video. Need finer control? Each step links to its full tab.
          </p>
        </div>
        <button onClick={() => goTo('content')}>Switch to the full editor</button>
      </header>

      <ol className="stepper" aria-label="Progress">
        {STEPS.map((name, i) => (
          <li key={name}>
            <button
              className={`step-chip ${i === step ? 'active' : ''} ${done[name] ? 'done' : ''}`}
              onClick={() => setStep(i)}
              aria-current={i === step ? 'step' : undefined}
            >
              <span className="step-num">{done[name] ? '✓' : i + 1}</span>
              {name}
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
          ← Back
        </button>
        <span className="simple-progress">
          Step {step + 1} of {STEPS.length}
        </span>
        <button
          className="primary"
          onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
          disabled={step === STEPS.length - 1}
        >
          Next →
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
      <h2>1 · Content</h2>
      <p className="muted">
        Import a content package — a JSON file with narration, titles and image prompts for every
        scene. It fills the whole project at once.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        hidden
        aria-label="Content package JSON"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) void importFile(file)
          e.target.value = ''
        }}
      />
      <div className="row">
        <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy ? 'Importing…' : 'Import content package'}
        </button>
        <button onClick={() => goTo('content')}>Download the example / edit by hand →</button>
      </div>
      <p className={`step-status ${project!.scenes.length > 0 ? 'ok' : ''}`}>
        {project!.scenes.length > 0
          ? `✓ ${project!.scenes.length} scenes ready.`
          : 'No scenes yet.'}
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
      <h2>2 · Images</h2>
      <p className="muted">
        Upload one image per scene. Name them in order — <code>01-opening.png</code>,{' '}
        <code>02-habitat.png</code> — and they map onto scenes automatically.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp"
        hidden
        aria-label="Scene images"
        onChange={(e) => {
          void upload(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />
      <div className="row">
        <button className="primary" onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy ? 'Uploading…' : 'Upload images'}
        </button>
        <button onClick={() => goTo('scenes')}>Arrange scenes →</button>
      </div>
      <p className={`step-status ${mapped === project!.scenes.length && images.length > 0 ? 'ok' : ''}`}>
        {images.length === 0
          ? 'No images yet.'
          : `${images.length} uploaded · ${mapped}/${project!.scenes.length} scenes have an image.`}
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
      <h2>3 · Voice</h2>
      <p className="muted">
        Pick who narrates. Edge TTS is free and needs no API key (but needs internet). Or import
        your own audio later.
      </p>
      <div className="field-grid">
        <label>
          Provider
          <select
            value={providerName}
            onChange={(e) =>
              edit((d) => void (d.audio.ttsProvider = e.target.value as typeof d.audio.ttsProvider))
            }
          >
            {providers.map((p) => (
              <option key={p.name} value={p.name} disabled={!p.available}>
                {p.name}
                {p.available ? '' : ' (unavailable)'}
              </option>
            ))}
          </select>
        </label>
        {providerName !== 'imported' && (
          <label>
            Voice
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
        <button onClick={() => void goTo('audio')}>Rate, pitch and mixing →</button>
      </div>
      <p className="step-status ok">✓ Narrated by {providerName === 'imported' ? 'your own audio' : project!.audio.voice}.</p>
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
      <h2>4 · Music <span className="optional">optional</span></h2>
      <p className="muted">Background music is optional. Pick one:</p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      <input
        ref={fileInput}
        type="file"
        accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac"
        hidden
        aria-label="Music track"
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
          No music
        </button>
        <button className={source === 'uploaded' ? 'primary' : ''} onClick={() => fileInput.current?.click()} disabled={busy}>
          {busy ? 'Uploading…' : source === 'uploaded' && project!.music.file ? `Track: ${project!.music.file}` : 'Upload a track'}
        </button>
        <button
          className={source === 'generated-ambient' ? 'primary' : ''}
          onClick={() => edit((d) => void ((d.music.source = 'generated-ambient'), (d.music.file = null)))}
        >
          Generated ambient bed
        </button>
      </div>
      <div className="row">
        <button onClick={() => goTo('music')}>Manage the music library →</button>
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
      setResult(`${r.generatedCount} generated, ${r.reusedCount} reused.`)
      await openProject(slug)
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h2>5 · Narration</h2>
      <p className="muted">
        Generate the spoken audio for every scene. Scene lengths are then measured from the real
        clips.
      </p>
      {error && <ErrorBox error={error} onDismiss={() => setError(null)} />}
      {importedProvider ? (
        <p className="step-status">
          You chose imported audio — upload a clip per scene on the Audio tab.
          <button className="linkish" onClick={() => goTo('audio')}>Open Audio →</button>
        </p>
      ) : (
        <>
          <div className="row">
            <button className="primary" onClick={() => void generate()} disabled={busy || missing === 0}>
              {busy ? 'Generating…' : `Generate narration (${missing} missing)`}
            </button>
            <button onClick={() => goTo('audio')}>Per-scene control →</button>
          </div>
          {result && <p className="step-status ok">✓ {result}</p>}
          <p className={`step-status ${missing === 0 && units.length > 0 ? 'ok' : ''}`}>
            {units.length === 0
              ? 'No narration written yet — add it on the Content tab.'
              : missing === 0
                ? '✓ Every scene has audio.'
                : `${missing} scene${missing === 1 ? '' : 's'} still need audio.`}
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
      <h2>6 · Render</h2>
      <p className="muted">Produces a 1920×1080, 60 FPS MP4 plus subtitles and side-car files.</p>
      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {preflight && !preflight.ready && (
        <div className="blocking">
          <strong>Fix these first:</strong>
          {preflight.blockingIssues.map((i) => (
            <p key={i}>✕ {i}</p>
          ))}
        </div>
      )}
      {preflight?.ready && !running && (
        <p className="step-status ok">
          ✓ Ready — about {String(preflight.timing.totalFormatted ?? '—')} long.
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
          {busy || running ? 'Rendering…' : 'Render video'}
        </button>
        <button onClick={() => goTo('export')}>Open the Export tab for downloads →</button>
      </div>

      {job && !running && job.status === 'completed' && (
        <p className="step-status ok">
          ✓ Done. Download it on the{' '}
          <button className="linkish" onClick={() => goTo('export')}>
            Export tab
          </button>
          .
        </p>
      )}
    </div>
  )
}
