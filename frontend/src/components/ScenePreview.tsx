/**
 * In-browser Ken Burns + text preview.
 *
 * Mirrors the backend's motion math: the geometry (start/end scale and centre,
 * incl. the deterministic `auto` pick and clamping) comes straight from the
 * `/motion` endpoint — the same numbers the render uses — and this component
 * only evaluates the smoothstep interpolation the backend documents in
 * `kenburns.sample_transform`. So a scrub here matches the rendered clip
 * without building a proxy.
 */

import { useEffect, useRef, useState } from 'react'
import { api, describeError } from '@/api/client'
import type { ApiErrorPayload } from '@/api/types'
import type { Project, Scene, SceneMotion, TextPosition } from '@/api/project-types'
import { sampleTransform, staticMotion } from '@/lib/kenburns'
import './ScenePreview.css'

const POSITION_STYLE: Record<TextPosition, React.CSSProperties> = {
  'top-left': { top: 0, left: 0, textAlign: 'left', alignItems: 'flex-start', justifyContent: 'flex-start' },
  'top-center': { top: 0, left: 0, right: 0, textAlign: 'center', alignItems: 'center', justifyContent: 'flex-start' },
  'top-right': { top: 0, right: 0, textAlign: 'right', alignItems: 'flex-end', justifyContent: 'flex-start' },
  'middle-left': { top: 0, bottom: 0, left: 0, textAlign: 'left', alignItems: 'flex-start', justifyContent: 'center' },
  'middle-center': { inset: 0, textAlign: 'center', alignItems: 'center', justifyContent: 'center' },
  'middle-right': { top: 0, bottom: 0, right: 0, textAlign: 'right', alignItems: 'flex-end', justifyContent: 'center' },
  'bottom-left': { bottom: 0, left: 0, textAlign: 'left', alignItems: 'flex-start', justifyContent: 'flex-end' },
  'bottom-center': { bottom: 0, left: 0, right: 0, textAlign: 'center', alignItems: 'center', justifyContent: 'flex-end' },
  'bottom-right': { bottom: 0, right: 0, textAlign: 'right', alignItems: 'flex-end', justifyContent: 'flex-end' },
}

const PLAY_SECONDS_FALLBACK = 5

export function ScenePreview({ project, scene }: { project: Project; scene: Scene }) {
  const [motions, setMotions] = useState<SceneMotion[] | null>(null)
  const [error, setError] = useState<ApiErrorPayload | null>(null)
  const [progress, setProgress] = useState(0)
  const [playing, setPlaying] = useState(false)
  const raf = useRef<number | null>(null)
  const slug = project.slug

  useEffect(() => {
    let cancelled = false
    api
      .getMotion(slug)
      .then((m) => !cancelled && setMotions(m))
      .catch((err) => !cancelled && setError(describeError(err)))
    return () => {
      cancelled = true
    }
  }, [slug])

  const duration =
    scene.sceneDurationSeconds ?? scene.audioDurationSeconds ?? PLAY_SECONDS_FALLBACK

  // Play loop: advance progress over the scene's real duration, then stop.
  useEffect(() => {
    if (!playing) return
    let start: number | null = null
    const from = progress >= 1 ? 0 : progress
    const step = (t: number) => {
      if (start === null) start = t
      const elapsed = (t - start) / 1000
      const next = Math.min(1, from + elapsed / Math.max(0.5, duration))
      setProgress(next)
      if (next < 1) {
        raf.current = requestAnimationFrame(step)
      } else {
        setPlaying(false)
      }
    }
    raf.current = requestAnimationFrame(step)
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current)
    }
    // Restart the loop only when play is toggled, not on every progress tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, duration])

  const motion = motions?.find((m) => m.unitId === scene.id) ?? staticMotion(scene.id)
  const { scale, cx, cy } = sampleTransform(motion, progress)

  // Emulate the crop-at-scale-centred-on-(cx,cy) with a CSS transform.
  // origin top-left: image point (cx,cy) maps to the box centre.
  const tx = (0.5 - scale * cx) * 100
  const ty = (0.5 - scale * cy) * 100
  const objectFit = scene.fitMode === 'fit' ? 'contain' : 'cover'

  const style = project.style
  const showTitle = scene.title.trim() !== ''
  const showSubtitle = scene.subtitle.trim() !== ''
  const posStyle = POSITION_STYLE[style.textPosition]
  const margin = `${(style.textSafeMargin / 1080) * 100}%`

  return (
    <div className="scene-preview">
      <div className="preview-stage">
        {scene.imageFile ? (
          <img
            className="preview-image"
            src={`/api/projects/${slug}/media/images/${encodeURIComponent(scene.imageFile)}`}
            alt=""
            style={{
              objectFit,
              transform: `translate(${tx}%, ${ty}%) scale(${scale})`,
            }}
          />
        ) : (
          <div className="preview-empty">Bu sahnede görsel yok</div>
        )}

        {style.overlayOpacity > 0 && (
          <div
            className="preview-scrim"
            style={{ opacity: style.overlayOpacity, ['--pos' as string]: _scrimEdge(style.textPosition) }}
          />
        )}

        {(showTitle || showSubtitle) && (
          <div className="preview-text" style={{ ...posStyle, padding: margin }}>
            <div className="preview-text-inner" style={{ textAlign: posStyle.textAlign }}>
              {showTitle && (
                <span
                  className="preview-title"
                  style={{ color: style.title.color, fontWeight: style.title.fontWeight }}
                >
                  {scene.title}
                </span>
              )}
              {showSubtitle && (
                <span
                  className="preview-subtitle"
                  style={{ color: style.subtitle.color, fontWeight: style.subtitle.fontWeight }}
                >
                  {scene.subtitle}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="preview-controls">
        <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? 'Duraklat' : 'Oynat'}>
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          type="range"
          min={0}
          max={1}
          step={0.001}
          value={progress}
          aria-label="Önizlemede ileri geri git"
          onChange={(e) => {
            setPlaying(false)
            setProgress(Number(e.target.value))
          }}
        />
        <span className="preview-time">
          {(progress * duration).toFixed(1)}s / {duration.toFixed(1)}s
        </span>
      </div>

      <p className="preview-caption">
        {error ? (
          'Hareket bilgisi alınamadı — sabit görüntü gösteriliyor.'
        ) : (
          <>
            <span className="preview-move">{motion.description}</span>
            {' · '}
            {motion.preset}
          </>
        )}
      </p>
    </div>
  )
}

// The scrim sits under the text, so its gradient points from the text edge.
function _scrimEdge(pos: TextPosition): string {
  if (pos.startsWith('top')) return 'to bottom'
  if (pos.startsWith('middle')) return 'to center'
  return 'to top'
}
