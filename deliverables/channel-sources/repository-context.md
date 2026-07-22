# Extinct Video Builder Repository Context

Repository:
https://github.com/fatihdisci/documentary

Application:
Extinct Video Builder

Status:
M1–M7 completed.

## Stack
Frontend: React, TypeScript, Vite, Zustand
Backend: Python 3.11, FastAPI, Pydantic
Media: FFmpeg, ffprobe, Pillow, Edge TTS, imported audio, optional ElevenLabs

## Workflow
1. Create project.
2. Import content-package JSON.
3. Upload numbered images (intro image first, then one per scene).
4. Auto-map images to the intro and scenes.
5. Select TTS or import audio.
6. Generate missing audio.
7. Review timing, style and preview (use the fast Preview quality).
8. Select music.
9. Render final video.
10. Export MP4, SRT, narration audio, description, thumbnail prompt and logs.

## Rendering
- Final render uses FFmpeg, not browser Canvas.
- Default full-quality output is 1920 × 1080, constant 60 FPS.
- The **Preview** quality renders at 1920 × 1080 but 30 FPS with light
  supersampling — roughly 8× faster than a full export — for quick timing and
  caption checks. It caches its clips separately, so a preview never discards the
  clips a full render built.
- Scene duration uses measured audio duration.
- Timeline is the single source of truth.
- Per-scene rendering and caching are used.
- Output is validated with ffprobe.

## Subtitles
- Subtitles are **burned into the video by default**, so a finished MP4 is
  captioned without any extra steps.
- An external `.srt` (and per-scene SRTs) is always exported alongside.
- Burning can be turned off per project for a clean image (e.g. for a YouTube
  upload, which prefers the sidecar).

## Text constraint
The development FFmpeg build does not include drawtext or libass.
All titles, subtitles, captions, intro/outro text and watermarks are rendered with Pillow as transparent RGBA PNG overlays.
Do not make drawtext or libass mandatory.

## TTS
Supported paths:
- Edge TTS
- Imported WAV/MP3 or compatible audio
- Optional ElevenLabs

The basic workflow must remain usable without paid APIs.

## Image naming and the intro image
The intro gets **its own image** instead of reusing the first scene's picture, so
the opening and the first scene no longer show the same frame. Provide **one more
image than you have scenes** — eleven images for a ten-scene video — the first of
which is the intro. The simplest layout keeps the scene names and prepends the
intro:

    00-intro.png
    01-opening.png
    02-habitat.png
    03-anatomy.png
    04-behavior.png
    05-ecosystem.png
    06-human-arrival.png
    07-decline.png
    08-last-years.png
    09-evidence.png
    10-legacy.png

With exactly one image per scene (no spare), the intro falls back to reusing the
first scene's image, exactly as before — so ten-image projects are unaffected.
Set `useFirstSceneImage: true` on the intro to force the old shared-image behavior.

## Development guidance
- Inspect the current repository before suggesting code changes.
- Preserve the local-first architecture.
- Preserve Pillow text overlays.
- Preserve FFmpeg as final renderer.
- Prefer incremental changes over rewrites.
- Add regression tests.
- Validate actual outputs, not only process exit codes.
