# Channel Style Guide

## Identity
- Channel name:
- Handle:
- Primary language: English
- Target audience: Global natural-history and wildlife viewers

## Tone
- Informative
- Calm
- Cinematic
- Accessible
- Scientifically responsible

Avoid low-quality clickbait, fake certainty, graphic violence, repeated information and long generic intros.

## Standard video
- Duration: 4–7 minutes
- Resolution: 1920 × 1080
- Frame rate: constant 60 FPS (full render); the fast Preview quality uses 30 FPS
- Structure: intro, 8–12 scenes, outro
- Narration: AI TTS or imported narration
- Scene timing: measured audio duration
- Transitions: restrained dissolve or fade through black
- Subtitles: burned into the video by default, plus an external SRT

## Intro
Start with a species-specific hook. Do not use "Welcome back to the channel."
The intro has its **own image** (`00-intro.png`), distinct from the first scene —
never reuse the first scene's picture for the opening.

## Branded opening card (`longIntro`)
Every long video opens the same way, so the channel is recognisable in the first
second:

- Style: `typewriter-stamp` — the animal's name types itself out, the scientific
  name fades in small underneath, a red stamp lands over both.
- Duration: 4.2 seconds, typing 1.8 s, stamp at 2.65 s, then a readable hold.
- Stamp text: `EXTINCT`, or `EXTINCT IN THE WILD` for an EW species. Stamping the
  wrong status is a factual error, not a design choice.
- It is drawn over the intro image, so give that image a relatively calm
  centre-upper area — the type goes there.
- It never appears in a Short. Shorts have their own opening line (below).
- The renderer adds dry typewriter clacks and a restrained stamp impact. No
  external sound-effect asset is required.

## Shorts opening hook
Every Short opens with at most two short lines near the vertical canvas's eye
line as a 2.2-second two-beat reveal. The setup appears first; the larger red
impact line follows with a short upward punch and synchronized rise/impact sound.

- Short, strong, curious. Documentary tone — never clickbait.
- The Short must keep the promise the hook makes.
- Break the line where the sentence breaks; the app shrinks type but never
  re-breaks a hook.
- A different hook for every Short; never reuse one.
- Write in normal case — it is drawn upper case.

Good: `WHEN HE DIED,` / `THE SPECIES ENDED` · `THIS GIANT` / `DISAPPEARED
FOREVER` · `BILLIONS OF BIRDS.` / `THEN NONE.`

## Outro
End with a conservation message, next-episode teaser and one short subscribe call.

## TTS
- Preferred provider: Kokoro (local, no account, no network once downloaded)
- Preferred voice: af_bella
- Speech rate: 0.9
- Pitch: 0
- Default pronunciation dictionary: Yes

Narration should use short, natural sentences with minimal parentheses.

Every content package states this in its own `tts` block, so a package narrates
the way it was written without anyone having to remember the settings. Mix
levels — volumes, loudness, ducking — stay out of the package; they are the
editor's decision, not the writer's.

## Text
- Primary font:
- Secondary font:
- Title position: bottom-left
- Safe margin:
- Title color:
- Subtitle color:
- Background box opacity:
- Shadow:
- Outline:

## Visual style
cinematic wildlife documentary reconstruction, scientifically plausible extinct animal, realistic anatomy, historically appropriate natural environment, photorealistic, natural lighting, subtle film grain, restrained natural color grading, high detail, no text, no watermark, no logo, no modern objects, 16:9 widescreen composition

Keep the animal's physical description consistent across all scenes. Mix portrait, habitat, behavior, threat, decline and legacy shots. Give the intro a distinct establishing / hero shot so it does not repeat the first scene.

## Images
- Provide one more image than you have scenes (e.g. 11 images for 10 scenes).
- The first image is the intro's; the rest map to the scenes in filename order.
- Naming: `00-intro.png`, `01-opening.png`, … `10-legacy.png`.
- No spaces, Turkish characters, parentheses or long descriptions in filenames.

## Thumbnail
- One large animal
- Simple background
- Strong face or silhouette
- 2–4 words maximum
- Do not repeat the title word for word
- The thumbnail text and the strongest Short's hook should make the *same
  promise* without being the same words. The Publish panel shows them side by
  side for exactly this check.

## Music
- Preferred genre: ambient documentary
- Lyrics: none
- Ducking enabled: yes
- Attribution format:

## Publishing
- Upload day: Every other day, in a repeating two-day cycle.
- Upload time: Day 0 — long video at 22:00, then Short at 23:00; Day 1 — Shorts at 18:00 and 23:00 (Türkiye time, Europe/Istanbul).
- Long videos per week: 3–4 (3.5 average).
- Shorts per long video: 3, all tied to that long video.
