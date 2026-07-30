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

## What this application is

Not a video renderer with extras bolted on: an end-to-end documentary
production pipeline. One content package per animal goes in; a finished long
video, its Shorts, its thumbnail brief and its publishing drafts come out.
Content production and rendering share **one data model** — the content package
is the pipeline's input format, and every field in it is consumed by the app
rather than kept as a note for later.

## Workflow
1. Create project.
2. Import content-package JSON. This fills in the animal, the video metadata,
   the thumbnail text and prompt, the pronunciation table, the narration voice
   (`tts`), the branded opening (`longIntro`), the intro/scenes/outro, and the
   Shorts plan including each Short's opening hook.
3. Upload numbered images (intro image first, then one per scene).
4. Auto-map images to the intro and scenes.
5. Select TTS or import audio.
6. Generate missing audio.
7. Review timing, style and preview (use the fast Preview quality).
8. Select music.
9. Render final video.
10. Export MP4, SRT, narration audio, description, thumbnail prompt and logs.
11. Apply a planned Short in the Shorts tab: its sections, caption mode and hook
    are already filled in.
12. Publish from the Publish tab, where the plan's per-platform copy, the
    thumbnail text and the hook are all in view.
13. Record every production, upload, schedule and publication change in
    `deliverables/channel-sources/vanished-earth-content-tracker.xlsx`.

## Operational content tracker

`deliverables/channel-sources/vanished-earth-content-tracker.xlsx` is the
operational source of truth for long videos and Shorts. Update both `İçerik
Takibi` and `Yayın Takvimi` whenever a package, render, upload, schedule or
publication status changes. Do not mark an item `Published` until its public
YouTube URL has been verified; a past `Scheduled` record remains flagged for
verification until then.

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
- A subtitle-free **clean master** is prepared beside the export so Shorts can
  draw their own large captions on the vertical canvas. It carries neither the
  burned-in captions nor the branded opening.

## The branded opening (`longIntro`)

Every long video opens the same way: the animal's name types itself out, the
scientific name fades in small underneath, and a red `EXTINCT` stamp lands over
both. The 4.2-second arc leaves separate beats for reveal, identification,
impact and a readable hold before it dissolves.

- It is a **separate pre-roll clip**. It adds 4.2 seconds before the reusable
  content timeline, fades fully to black, and only then starts the ordinary
  intro section, narration and subtitles.
- Cards are drawn with Pillow — one per revealed state, not one per frame — and
  baked into a single transparent track, so the assemble filtergraph gains one
  input rather than two dozen. Everything is cached by content hash.
- Blank titles resolve to the project's own `commonName` / `scientificName` at
  render time, so a project that never touches the block still opens correctly.
- **Long videos only.** The clean master is rendered with the opening switched
  off, so a Short that includes the intro section never opens with the long
  video's title sequence. Consequence worth knowing: an export that carries the
  opening can no longer be published as its own clean master, so a project with
  burn-in off now pays one extra assemble pass. The render log says exactly
  that, and turning the opening off restores the shortcut.
- Turned on by default for new projects; the v2 → v3 migration turns it on for
  existing ones too, and one toggle in the Texts tab turns it off.

## Shorts opening hooks

Every planned Short carries a `hook`: at most two lines, drawn near the vertical
canvas's eye line on its own 2.2-second black pre-roll. The first line sets up
the thought; the second lands larger, red, and with a short upward punch. The
hook fades out completely before the selected picture, audio and captions begin.

- Composed onto the 1080×1920 canvas during the Shorts compose pass, in every
  caption mode. Never burned into the long video or into the file the cut came
  from.
- Placed near eye level; captions stay in the lower safe area, so the two never
  collide.
- Part of the Short's cache key: changing the words means a different Short.
- An empty hook draws nothing and is omitted from the request entirely, so a
  Short re-cut from a plan authored before hooks existed produces exactly the
  bytes it always did.
- Project v4 retimes untouched v3 openings and hooks. User-edited timing values
  are preserved.
- Sound effects are synthesized locally and cached: typewriter/stamp for the
  long opening, rise/impact for a Short hook. Long-opening SFX stay out of the
  Shorts clean master.

## Text constraint
The development FFmpeg build does not include drawtext or libass.
All titles, subtitles, captions, intro/outro text and watermarks are rendered with Pillow as transparent RGBA PNG overlays.
Do not make drawtext or libass mandatory.

## TTS
Supported paths:
- Kokoro, running locally (the default)
- Edge TTS
- Imported WAV/MP3 or compatible audio
- Optional ElevenLabs

The basic workflow must remain usable without paid APIs.

A content package may name the voice it was written for, in a `tts` block
(`provider`, `voice`, `speechRate`, `speechPitch`, plus free-text `notes`).
Only the fields actually present are applied, and mix settings — volumes,
loudness, ducking — are never imported: a content package writes words, not a
mix.

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

## Shorts production manifest

Every new content-package JSON for this channel must also carry a `shortsPlan`.
It is the hand-off for 3–5 planned Shorts: ordered references to the source
sections (`intro`, one-based `scene` numbers, or `outro`), a clear editorial
angle, estimated duration, the Short's opening `hook`, and publication-ready
English copy for YouTube, Instagram, Facebook and TikTok. The plan uses
`shorts-native` / `large` captions, prefers two to four adjacent scenes, and
uses `FULL_VIDEO_URL` until the long video has a public link.

Do not invent render timestamps or filenames in this plan. TTS creates the
actual duration, so final safe ranges come from the completed render timeline.

The plan is **imported into the project**, not merely archived. The Shorts tab
lists each planned Short and applies its sections, caption mode and hook in one
click; the Publish tab matches a rendered Short back to its plan and seeds the
per-platform drafts from it.

## Development guidance
- Inspect the current repository before suggesting code changes.
- Preserve the local-first architecture.
- Preserve Pillow text overlays.
- Preserve FFmpeg as final renderer.
- Prefer incremental changes over rewrites.
- Add regression tests.
- Validate actual outputs, not only process exit codes.
