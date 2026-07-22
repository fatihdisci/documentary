# User guide

This walks through building one video end to end. The app autosaves as you go —
the top bar always shows the save state — so you can move between tabs freely.

The sidebar tabs run left-to-right in roughly the order you'll use them.

## 1. Diagnostics

Open this first. It probes your machine — FFmpeg binary and version, the text
engine, disk space, storage permissions, and which narration sources are
available — and reports measured facts, not assumptions. Fix anything marked
**Failed** before rendering. See [troubleshooting.md](troubleshooting.md).

## 2. Projects

Click **Create project** and give it a name. Each project is a self-contained
folder under `~/ExtinctVideoBuilder/projects/`. From here you can open,
duplicate, archive or delete projects, and export/import a project bundle (a
`.zip` containing everything — useful for backups or moving between machines).

## 3. Content

Fill in every scene at once by importing a **content package** (a JSON file
with narration, titles, image prompts and framing hints), or edit the fields by
hand.

- **Download example template** gives you the documented format with a working
  sample (the Dodo). The full schema is in
  [content-schema.md](content-schema.md).
- **Replace existing scenes** rebuilds scenes from the package (per-scene tuning
  is lost). Unchecked, scenes are updated in place, keeping audio and manual
  durations.
- Importing never changes your video, style or audio settings.

This tab also holds the video's metadata: animal name, video title, YouTube
description, thumbnail text/prompt, and the intro/outro narration.

## 4. Scenes

Upload your images (drag-and-drop works). Give **one more image than you have
scenes** — 11 for a 10-scene video — and the first is used as the intro's own
picture, so the opening no longer repeats the first scene's frame. Name them with
a numeric prefix — `00-intro.png`, `01-opening.png`, `02-habitat.png` — so they
map in the right order automatically. (With exactly one image per scene, the
intro reuses the first scene's image as before.) Reorder scenes by dragging their cards;
**Auto-map images** re-runs the filename-order mapping if you add or reorder
images later. Each card flags what it's still missing (no image / no narration /
no audio yet).

Selecting a scene that has an image opens a **preview** — a scrubbable,
in-browser Ken Burns + text mock-up that uses the exact motion the render will
use (same easing and geometry), so you can check the pan/zoom and where the
title sits without rendering a proxy clip.

## 5. Audio

Pick a TTS provider and voice, then **Generate missing** to synthesize
narration for every scene.

- **Edge TTS** is free and needs no API key (but needs an internet connection).
- **ElevenLabs** needs an API key (set it on Settings).
- **Imported** lets you upload your own audio per scene — fully offline.

Scene durations come from the *real measured length* of each narration clip, so
the "Expected runtime" panel is accurate. **Duration mode** controls how time is
allotted: audio-driven (scene = narration + padding), target duration (extra
hold time is spread to hit a target), or manual (you set every duration).

The **Mixing** controls — voice/music levels, automatic ducking under speech,
and the loudness target (−16 LUFS suits YouTube) — also live here.

## 6. Music (optional)

Upload one or more background tracks, audition them in the browser, and pick the
one the render uses. Or leave it on **No music**, or use the basic generated
ambient bed (set on the Audio tab). Deleting the track you'd selected safely
falls back to no music.

## 7. Style (optional)

Tune the look of every overlay. Titles, subtitles, captions and burned-in
subtitles each get their own tab with a **live preview**: font, weight, size,
colour, letter/line spacing, drop shadow, outline and a background box. Global
controls cover text position, safe margin, the readability scrim, the default
scene transition, and an optional watermark. Sensible defaults ship out of the
box, so this tab is entirely skippable.

## 8. Export

Check the **preflight** panel — it lists anything blocking a render and shows
the final runtime, scene count, disk needed and an estimated render time. Pick a
quality preset, then **Render video**. Progress streams live (you can cancel or
retry at any point). When it finishes, the output MP4 and every side-car file —
SRT subtitles, narration-only audio, description, thumbnail prompt, render log —
are listed with one-click downloads. A render history and the files on disk are
shown below.

## 9. Shorts (optional)

Turns a finished video into a vertical clip for YouTube Shorts. It only ever
cuts from a **completed render** — nothing is re-rendered, so the narration,
music, burned-in subtitles and scene transitions come through exactly as they
were mixed.

1. **Pick the source render.** Only completed renders that still match the file
   on disk are offered. If an export was deleted or edited after it was
   rendered, it is shown as unusable and the reason is given — render the long
   video again and use the new export.
2. **Choose sections.** Cards are numbered the way the video plays: `0 — Intro`,
   `1…N — Scene title`, `N+1 — Outro`. Tick them in the order you want them to
   appear; the badge on each card shows its position, and 6-then-2 really does
   put section 6 first. Non-adjacent picks are fine.
3. **Trim.** Each selected section gets a start and end field. The bounds come
   from the render manifest, not from guesswork — the excluded margins are the
   transition each section shares with its neighbours, and cutting inside one
   would duplicate frames. Sections you pick back-to-back and leave untrimmed at
   the join are cut as one continuous span, so the transition between them is
   preserved exactly. Trim at a join and the two become separate cuts joined by
   a hard cut; nothing is faded or invented.
4. **Watch the duration.** 25–50 seconds is the recommended band. Past **60
   seconds** you get a warning, because a Short over a minute can be blocked
   worldwide if any music in it has an active Content ID claim — fine when the
   music is yours or licensed, which is why it is only a warning. Past **three
   minutes** the render is blocked: YouTube only treats vertical or square video
   up to three minutes as a Short.
5. **Check the preview**, then **Render Short**. Progress streams live and can be
   cancelled or retried. Requesting a Short you already built reuses the existing
   file instead of encoding again, and says so.

Every Short is **1080×1920** with the 16:9 picture centred on full black at its
original aspect ratio — never stretched, cropped or zoomed to fill. A 1920×1080
source lands at 1080×608 with black above and below.

Finished Shorts live in `exports/shorts/` inside the project, separately from the
long video's exports. Deleting one removes only that Short's files; the long
render, the project and every other Short are untouched.

## Quality presets

| Preset | Use for |
|---|---|
| YouTube high quality | Final upload. Best quality, slowest. |
| High | High quality, a little faster. |
| Standard | Good quality, quicker. |
| Preview | Fast and rough — for checking timing only. |

## Subtitle timing

Subtitles are placed on the words the engine actually spoke, not on an estimate
of how long the text should take. Those word boundaries are stored next to the
narration audio, so they survive into later renders.

Narration generated before this existed has no stored boundaries — the render
warns about it, and one **Regenerate all** on the Audio tab fixes it for good.
Imported audio has no boundaries at all, so its cues are estimated from the text
(the silence at the head of the clip is measured and skipped).

## The one rule about timing

Everything timing-related comes from a single **Timeline** computed once from
the measured audio. No later stage recomputes a duration or offset. That's why
the runtime shown on the Audio and Export tabs matches the final file.
