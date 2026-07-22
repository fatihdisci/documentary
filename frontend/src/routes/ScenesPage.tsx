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
  if (!scene.imageFile) issues.push('No image')
  if (!scene.narration.trim()) issues.push('No narration')
  if (!scene.audioFile) issues.push('No audio yet')

  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`scene-card ${selected ? 'selected' : ''} ${isDragging ? 'dragging' : ''} ${
        scene.enabled ? '' : 'disabled'
      }`}
      onClick={onSelect}
    >
      <div className="scene-drag" {...attributes} {...listeners} aria-label={`Reorder scene ${index + 1}`}>
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
          <span className="thumb-placeholder">no image</span>
        )}
        <span className="scene-number">{index + 1}</span>
      </div>
      <div className="scene-body">
        <h3>{scene.title || <em className="muted">Untitled scene</em>}</h3>
        {scene.subtitle && <p className="scene-subtitle">{scene.subtitle}</p>}
        <p className="scene-narration">{scene.narration || '—'}</p>
        <div className="scene-tags">
          <span className="tag">{scene.animationPreset}</span>
          {scene.audioDurationSeconds != null && (
            <span className="tag">{scene.audioDurationSeconds.toFixed(1)}s audio</span>
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
        <h1>Scenes</h1>
        <p className="page-subtitle">Open a project first.</p>
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
        return `Deleted ${filename}.`
      })
    } else if (cleanup.kind === 'allImages') {
      void runCleanup(async () => {
        const { removed } = await api.deleteAllImages(slug)
        return `Deleted ${removed} image${removed === 1 ? '' : 's'}.`
      })
    } else {
      void runCleanup(async () => {
        const { removed } = await api.cleanDerived(slug)
        return `Cleared ${removed} cached file${removed === 1 ? '' : 's'}.`
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
          <h1>Scenes</h1>
          <p className="page-subtitle">
            {project.scenes.length} scene{project.scenes.length === 1 ? '' : 's'} ·{' '}
            {images.length} image{images.length === 1 ? '' : 's'}
            {unmapped > 0 && ` · ${unmapped} without an image`}
          </p>
        </div>
        <div className="header-actions">
          <button onClick={() => fileInput.current?.click()} disabled={uploading}>
            {uploading ? 'Uploading…' : 'Upload images'}
          </button>
          <button
            onClick={() =>
              void api
                .remapImages(slug)
                .then(() => openProject(slug))
                .catch(setError)
            }
          >
            Auto-map images
          </button>
          <button
            onClick={() =>
              void api
                .addScene(slug)
                .then(() => openProject(slug))
                .catch(setError)
            }
          >
            Add scene
          </button>
          <button
            className="subtle"
            onClick={() => setCleanup({ kind: 'cache' })}
            disabled={busy}
            title="Delete cached clips, normalized images and text cards. Your images, audio and exports are kept."
          >
            Clear render cache
          </button>
        </div>
      </header>

      <input
        ref={fileInput}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp"
        hidden
        aria-label="Upload scene images"
        onChange={(e) => {
          void uploadFiles(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />

      {error && <ErrorBox error={error} onDismiss={clearError} />}

      {selectedScene?.imageFile && (
        <section className="card preview-panel">
          <h2>Preview — {selectedScene.title || 'selected scene'}</h2>
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
        Drop PNG, JPEG or WebP images here. They map in filename order. Give one more image than
        you have scenes and the first becomes the intro’s own picture — name them{' '}
        <code>00-intro.png</code>, <code>01-opening.png</code>, and so on.
      </div>

      {notice && <p className="notice ok">{notice}</p>}

      {unusedImages.length > 0 && (
        <p className="notice">
          {unusedImages.length} uploaded image{unusedImages.length === 1 ? ' is' : 's are'} not used:{' '}
          {unusedImages.map((i) => i.filename).join(', ')}
        </p>
      )}

      {images.length > 0 && (
        <section className="card image-manager">
          <div className="image-manager-head">
            <h2>Uploaded images ({images.length})</h2>
            <button
              className="danger"
              onClick={() => setCleanup({ kind: 'allImages' })}
              disabled={busy}
            >
              Delete all images
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
                  aria-label={`Delete ${image.filename}`}
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
          <h2>No scenes yet</h2>
          <p>Import a content package on the Content tab, or add scenes manually.</p>
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
              ? 'Clear render cache?'
              : cleanup.kind === 'allImages'
                ? 'Delete all images?'
                : 'Delete this image?'
          }
          body={
            cleanup.kind === 'cache' ? (
              <p>
                This deletes the cached render files (clips, normalized images, text cards and
                subtitle overlays). They rebuild automatically on the next render. Your images,
                narration audio and finished exports are <strong>not</strong> touched.
              </p>
            ) : cleanup.kind === 'allImages' ? (
              <p>
                This permanently deletes all {images.length} uploaded image
                {images.length === 1 ? '' : 's'} and removes them from every scene and the intro.
                This cannot be undone.
              </p>
            ) : (
              <p>
                This permanently deletes <strong>{cleanup.filename}</strong> and removes it from any
                scene or the intro using it. This cannot be undone.
              </p>
            )
          }
          confirmLabel={cleanup.kind === 'cache' ? 'Clear cache' : 'Delete'}
          destructive={cleanup.kind !== 'cache'}
          onCancel={() => setCleanup(null)}
          onConfirm={confirmCleanup}
        />
      )}
    </div>
  )
}
