import { useCallback, useRef, useState } from 'react'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api } from '@/api/client'
import type { Scene } from '@/api/project-types'
import { useProjectStore } from '@/store/project'
import { ErrorBox } from '@/components/ErrorBox'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { ScenePreview } from '@/components/ScenePreview'
import './ScenesPage.css'

type Cleanup =
  | { kind: 'image'; filename: string }
  | { kind: 'allImages' }
  | { kind: 'cache' }

function SortableSceneCard({
  scene,
  slug,
  index,
  selected,
  onSelect,
}: {
  scene: Scene
  slug: string
  index: number
  selected: boolean
  onSelect: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: scene.id,
  })

  const issues: string[] = []
  if (!scene.imageFile) issues.push('Görsel yok')
  if (!scene.narration.trim()) issues.push('Metin yok')
  if (!scene.audioFile) issues.push('Ses henüz yok')

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`scene-card ${selected ? 'selected' : ''} ${isDragging ? 'dragging' : ''} ${
        scene.enabled ? '' : 'disabled'
      }`}
      onClick={onSelect}
    >
      <div className="scene-drag" {...attributes} {...listeners} aria-label={`${index + 1}. sahneyi taşı`}>
        ⠿
      </div>
      <div className="scene-thumb">
        {scene.imageFile ? (
          <img
            src={`/api/projects/${slug}/media/thumbnails/${scene.imageFile.replace(/\.[^.]+$/, '.jpg')}`}
            alt=""
            loading="lazy"
          />
        ) : (
          <span className="thumb-placeholder">görsel yok</span>
        )}
        <span className="scene-number">{index + 1}</span>
      </div>
      <div className="scene-body">
        <h3>{scene.title || <em className="muted">Başlıksız sahne</em>}</h3>
        {scene.subtitle && <p className="scene-subtitle">{scene.subtitle}</p>}
        <p className="scene-narration">{scene.narration || '—'}</p>
        <div className="scene-tags">
          <span className="tag">{scene.animationPreset}</span>
          {scene.audioDurationSeconds != null && (
            <span className="tag">{scene.audioDurationSeconds.toFixed(1)} sn ses</span>
          )}
          {issues.map((issue) => (
            <span key={issue} className="tag tag-warn">
              {issue}
            </span>
          ))}
        </div>
      </div>
    </li>
  )
}

export function ScenesPage() {
  const { project, images, selectedSceneId, selectScene, openProject, setError, error, clearError } =
    useProjectStore()
  const [uploading, setUploading] = useState(false)
  const [dropActive, setDropActive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [cleanup, setCleanup] = useState<Cleanup | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const uploadFiles = useCallback(
    async (files: File[]) => {
      if (!project || files.length === 0) return
      setUploading(true)
      try {
        await api.uploadImages(project.slug, files)
        await openProject(project.slug)
      } catch (err) {
        setError(err)
      } finally {
        setUploading(false)
      }
    },
    [project, openProject, setError],
  )

  if (!project) {
    return (
      <div className="page">
        <h1>Sahneler</h1>
        <p className="page-subtitle">Önce bir proje açın.</p>
      </div>
    )
  }

  const slug = project.slug

  async function runCleanup(action: () => Promise<string>) {
    setBusy(true)
    setNotice(null)
    clearError()
    try {
      const message = await action()
      await openProject(slug)
      setNotice(message)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
      setCleanup(null)
    }
  }

  function confirmCleanup() {
    if (cleanup === null) return
    if (cleanup.kind === 'image') {
      const { filename } = cleanup
      void runCleanup(async () => {
        await api.deleteImage(slug, filename)
        return `${filename} silindi.`
      })
    } else if (cleanup.kind === 'allImages') {
      void runCleanup(async () => {
        const { removed } = await api.deleteAllImages(slug)
        return `${removed} görsel silindi.`
      })
    } else {
      void runCleanup(async () => {
        const { removed } = await api.cleanDerived(slug)
        return `${removed} geçici dosya temizlendi.`
      })
    }
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id || !project) return
    const ids = project.scenes.map((s) => s.id)
    const from = ids.indexOf(String(active.id))
    const to = ids.indexOf(String(over.id))
    if (from < 0 || to < 0) return
    try {
      await api.reorderScenes(slug, arrayMove(ids, from, to))
      await openProject(slug)
    } catch (err) {
      setError(err)
    }
  }

  const selectedScene = project.scenes.find((s) => s.id === selectedSceneId) ?? null
  const unmapped = project.scenes.filter((s) => !s.imageFile).length
  // The intro can own the first image, so it counts as used too.
  const usedImages = new Set(
    [project.intro?.imageFile, ...project.scenes.map((s) => s.imageFile)].filter(Boolean),
  )
  const unusedImages = images.filter((image) => !usedImages.has(image.filename))

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Sahneler</h1>
          <p className="page-subtitle">
            {project.scenes.length} sahne · {images.length} görsel
            {unmapped > 0 && ` · ${unmapped} sahnede görsel yok`}
          </p>
        </div>
        <div className="header-actions">
          <button onClick={() => fileInput.current?.click()} disabled={uploading}>
            {uploading ? 'Yükleniyor…' : 'Görsel yükle'}
          </button>
          <button
            onClick={() =>
              void api
                .remapImages(slug)
                .then(() => openProject(slug))
                .catch(setError)
            }
          >
            Görselleri sahnelere dağıt
          </button>
          <button
            onClick={() =>
              void api
                .addScene(slug)
                .then(() => openProject(slug))
                .catch(setError)
            }
          >
            Sahne ekle
          </button>
          <button
            className="subtle"
            onClick={() => setCleanup({ kind: 'cache' })}
            disabled={busy}
            title="Video oluştururken üretilen geçici dosyaları siler. Görselleriniz, sesleriniz ve hazır videolarınız durur."
          >
            Geçici dosyaları temizle
          </button>
        </div>
      </header>

      <input
        ref={fileInput}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp"
        hidden
        aria-label="Sahne görsellerini yükle"
        onChange={(e) => {
          void uploadFiles(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {selectedScene?.imageFile && (
        <section className="card preview-panel">
          <h2>Önizleme — {selectedScene.title || 'seçili sahne'}</h2>
          <ScenePreview project={project} scene={selectedScene} />
        </section>
      )}

      <div
        className={`dropzone ${dropActive ? 'active' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDropActive(true)
        }}
        onDragLeave={() => setDropActive(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDropActive(false)
          void uploadFiles(Array.from(e.dataTransfer.files))
        }}
      >
        Görselleri buraya sürükleyin (PNG, JPEG veya WebP). Dosya adına göre sırayla sahnelere
        dağıtılır. Sahne sayınızdan bir fazla görsel verirseniz ilki giriş görseli olur — dosyaları{' '}
        <code>00-giris.png</code>, <code>01-sahne.png</code> gibi adlandırın.
      </div>

      {notice && <p className="notice ok">{notice}</p>}

      {unusedImages.length > 0 && (
        <p className="notice">
          {unusedImages.length} görsel hiçbir sahnede kullanılmıyor:{' '}
          {unusedImages.map((i) => i.filename).join(', ')}
        </p>
      )}

      {images.length > 0 && (
        <section className="card image-manager">
          <div className="image-manager-head">
            <h2>Yüklenen görseller ({images.length})</h2>
            <button
              className="danger"
              onClick={() => setCleanup({ kind: 'allImages' })}
              disabled={busy}
            >
              Tüm görselleri sil
            </button>
          </div>
          <ul className="image-grid">
            {images.map((image) => (
              <li key={image.filename} className="image-tile">
                <img
                  src={`/api/projects/${slug}/media/thumbnails/${image.filename.replace(/\.[^.]+$/, '.jpg')}`}
                  alt=""
                  loading="lazy"
                />
                <span className="image-name" title={image.filename}>
                  {image.filename}
                </span>
                <button
                  className="image-delete"
                  aria-label={`${image.filename} dosyasını sil`}
                  onClick={() => setCleanup({ kind: 'image', filename: image.filename })}
                  disabled={busy}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {images.some((i) => i.warnings.length > 0) && (
        <div className="notice warn">
          {images
            .filter((i) => i.warnings.length > 0)
            .map((image) => (
              <p key={image.filename}>
                <strong>{image.filename}</strong>: {image.warnings.join(' ')}
              </p>
            ))}
        </div>
      )}

      {project.scenes.length === 0 ? (
        <div className="empty">
          <h2>Henüz sahne yok</h2>
          <p>Metinler sekmesinden hazır bir dosya yükleyin ya da elle sahne ekleyin.</p>
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={(e) => void handleDragEnd(e)}
        >
          <SortableContext items={project.scenes.map((s) => s.id)} strategy={rectSortingStrategy}>
            <ul className="scene-grid">
              {project.scenes.map((scene, index) => (
                <SortableSceneCard
                  key={scene.id}
                  scene={scene}
                  slug={slug}
                  index={index}
                  selected={scene.id === selectedSceneId}
                  onSelect={() => selectScene(scene.id)}
                />
              ))}
            </ul>
          </SortableContext>
        </DndContext>
      )}

      {cleanup && (
        <ConfirmDialog
          title={
            cleanup.kind === 'cache'
              ? 'Geçici dosyalar temizlensin mi?'
              : cleanup.kind === 'allImages'
                ? 'Tüm görseller silinsin mi?'
                : 'Bu görsel silinsin mi?'
          }
          body={
            cleanup.kind === 'cache' ? (
              <p>
                Video oluştururken üretilen geçici dosyalar silinir. Bir sonraki videoda
                kendiliğinden yeniden üretilirler. Görselleriniz, ses kayıtlarınız ve hazır
                videolarınıza <strong>dokunulmaz</strong>.
              </p>
            ) : cleanup.kind === 'allImages' ? (
              <p>
                Yüklediğiniz {images.length} görselin tamamı kalıcı olarak silinir ve tüm
                sahnelerden kaldırılır. Bu işlem geri alınamaz.
              </p>
            ) : (
              <p>
                <strong>{cleanup.filename}</strong> kalıcı olarak silinir ve onu kullanan
                sahnelerden kaldırılır. Bu işlem geri alınamaz.
              </p>
            )
          }
          confirmLabel={cleanup.kind === 'cache' ? 'Temizle' : 'Sil'}
          destructive={cleanup.kind !== 'cache'}
          onCancel={() => setCleanup(null)}
          onConfirm={confirmCleanup}
        />
      )}
    </div>
  )
}
