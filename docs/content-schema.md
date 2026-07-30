# Content package schema

A **content package** is a single JSON file holding everything *authored* about
one animal: the video's metadata, its narration, its image prompts and framing
hints, the branded opening, the narration voice, and a Shorts plan in which
every Short already carries its own opening hook. It carries no file paths,
timings or render settings, so importing one never disturbs how you have
configured the video, style or audio.

It is a turnkey delivery, not a draft. If importing one leaves you having to
answer "what should the opening say?", "what is the Short's hook?" or "which
voice reads this?", the package is incomplete.

Download a working example from **Content → Download example template**, or find
it at `backend/fixtures/dodo-content.json`.

## Top level

```jsonc
{
  "contentSchemaVersion": 2,

  "commonName":      "Dodo",
  "scientificName":  "Raphus cucullatus",

  "videoTitle":      "The Dodo: How We Erased a Bird in a Single Lifetime",
  "description":     "Full YouTube description, including chapters.",
  "tags":            ["dodo", "extinction"],
  "thumbnailText":   "GONE IN 100 YEARS",
  "thumbnailPrompt": "Prompt for generating the thumbnail image.",

  "longIntro": { /* the branded opening, see below */ },

  "pronunciation": {
    "Raphus cucullatus": "RAH-fus koo-koo-LAH-tus",
    "Mauritius": "muh-RISH-us"
  },

  "tts": { /* which voice reads this, see below */ },

  "shortsPlan": { /* production manifest with per-Short hooks, see below */ },

  "intro":  { /* section, see below */ },
  "scenes": [ /* 1-200 scenes, see below */ ],
  "outro":  { /* section */ }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `contentSchemaVersion` | int | no | Defaults to `2`. A v1 package still imports unchanged. |
| `commonName` | string | no | Fills the project's animal name. |
| `scientificName` | string | no | Shown as a subtitle and read by TTS. |
| `videoTitle` | string | no | Exported to `description.txt`. |
| `description` | string | no | Up to 10 000 characters. |
| `tags` | string[] | no | |
| `thumbnailText` | string | no | Overlaid text for your thumbnail. |
| `thumbnailPrompt` | string | no | Exported to `thumbnail.txt`. |
| `longIntro` | object | no | The branded opening for the long video; see below. Absent means "keep the project's own". |
| `pronunciation` | object | no | Applied to narration before synthesis. |
| `tts` | object | no | Which voice reads this; see below. Absent means "keep the project's voice". |
| `shortsPlan` | object | no | Scene-based Shorts and publishing plan, each with its own hook; see below. |
| `scenes` | array | **yes** | At least 1, at most 200. |

## The branded opening (`longIntro`)

Every long video opens the same way: the animal's name types itself out, the
scientific name fades in small underneath, and a red `EXTINCT` stamp lands over
both. The 4.2-second arc gives each beat time to register before it dissolves.

```json
{
  "longIntro": {
    "enabled": true,
    "introStyle": "typewriter-stamp",
    "primaryTitle": "Dodo",
    "secondaryTitle": "Raphus cucullatus",
    "stampText": "EXTINCT",
    "duration": 4.2,
    "typewriterDuration": 1.8,
    "stampAt": 2.65
  }
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Off means the video starts straight on scene one. |
| `introStyle` | enum | `"typewriter-stamp"` | Or `"plain-title"`: the same layout with no typing and no stamp. |
| `primaryTitle` | string | `""` | Blank resolves to `commonName` at render time. |
| `secondaryTitle` | string | `""` | Blank resolves to `scientificName`. |
| `stampText` | string | `"EXTINCT"` | Blank draws no stamp. Use `"EXTINCT IN THE WILD"` for an EW species — stamping the wrong status is a factual error. |
| `duration` | 0.8–6.0 | `4.2` | Seconds on screen. Four beats: reveal, identify, stamp, hold. |
| `typewriterDuration` | 0.0–5.0 | `1.8` | How long the name takes to type itself out. |
| `stampAt` | 0.0–6.0 | `2.65` | When the stamp lands. |
| `fadeOutSeconds` | 0.0–2.0 | `0.65` | Dissolve at the end. |
| `primaryColor`, `secondaryColor`, `stampColor` | `#RRGGBB` | white / warm grey / red | Bounded design. Change only when a species genuinely needs it. |
| `scrimOpacity` | 0.0–1.0 | `0.55` | Dark wash under the card, so the title reads over a bright first frame. |

`typewriterDuration` and `stampAt` must both be less than or equal to
`duration`; a package that breaks this is rejected with a named field.

Three things are worth knowing about how it behaves:

* **It does not lengthen the video.** The card is composited over the first
  seconds of the finished picture, not inserted as a section, so the same
  project renders to the same duration with it on or off.
* **It never reaches a Short.** The subtitle-free clean master that Shorts are
  cut from is rendered without it, so a Short that includes the intro section
  opens with its own hook rather than the long video's title sequence.
* **It costs a render pass in one case.** A project that renders with burn-in
  *off* used to publish its export as its own clean master for free; with an
  opening it can no longer do so, and pays one extra assemble pass. The render
  log says exactly that, and turning the opening off restores the shortcut.

## Narration voice (`tts`)

What voice this package was written for. Hints, not settings: only the fields
actually present are applied, so a package can ask for a voice without
overwriting a speech rate the user has chosen.

```json
{
  "tts": {
    "provider": "kokoro",
    "voice": "af_bella",
    "speechRate": 0.9,
    "notes": "Plain English, no SSML. The scientific name is spoken once, in the intro."
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `provider` | enum | `kokoro` (local, the default) · `edge` · `elevenlabs` · `imported` |
| `voice` | string | Provider-specific voice id. |
| `speechRate` | 0.5–2.0 | |
| `speechPitch` | -50–50 | |
| `notes` | string | For the person reviewing the package. Never applied to anything. |

Mix settings — volumes, loudness targets, ducking — are deliberately **not** in
this block and are never imported. A content package writes words, not a mix.

## Shorts production plan (`shortsPlan`)

New channel packages include this optional production manifest so the editor
does not have to describe the scene joins and ask for publication text later.
It contains 3–5 vertical-cut recommendations and all viewer-facing copy.

```json
{
  "shortsPlan": {
    "version": 1,
    "captionMode": "shorts-native",
    "captionPreset": "large",
    "recommendedReleaseOrder": ["last-survivor"],
    "shorts": [
      {
        "id": "last-survivor",
        "priority": 1,
        "purpose": "The emotional final-survivor hook.",
        "sections": [
          { "kind": "scene", "number": 9 },
          { "kind": "scene", "number": 10 }
        ],
        "estimatedDurationSeconds": 43,
        "hook": {
          "enabled": true,
          "lines": ["When he died,", "the species ended"],
          "startSeconds": 0.0,
          "durationSeconds": 2.2
        },
        "youtube": {
          "title": "When One Death Ended a Species",
          "alternativeTitles": ["The Last Pinta Tortoise"],
          "description": "Lonesome George was the last known pure Pinta tortoise. When he died, a species ended.\n\nWatch the full documentary:\nFULL_VIDEO_URL\n\n#LonesomeGeorge #ExtinctAnimals #Shorts",
          "tags": ["lonesome george", "pinta tortoise", "extinct animals"],
          "hashtags": ["#LonesomeGeorge", "#ExtinctAnimals", "#Shorts"],
          "pinnedComment": "George was the last known pure Pinta tortoise, not the last Galápagos giant tortoise."
        },
        "instagram": {
          "caption": "One animal became the final symbol of an entire lost lineage. Full documentary: FULL_VIDEO_URL",
          "hashtags": ["#LonesomeGeorge", "#ExtinctAnimals"],
          "cta": "Watch the full documentary."
        },
        "facebook": {
          "caption": "Lonesome George was the last known pure Pinta tortoise. Full documentary: FULL_VIDEO_URL",
          "hashtags": ["#LonesomeGeorge", "#ExtinctAnimals"],
          "cta": "Watch the full documentary."
        },
        "tiktok": {
          "caption": "One death ended a species.",
          "hashtags": ["#LonesomeGeorge", "#ExtinctAnimals"],
          "cta": "Watch the full story."
        }
      }
    ]
  }
}
```

`sections` uses stable story references, not guessed timestamps: use
`{ "kind": "intro" }`, `{ "kind": "scene", "number": 1 }`, or
`{ "kind": "outro" }`. Prefer adjacent source-order sections so their
transition can survive the Short render. The duration is an estimate from the
narration; choose exact safe ranges only after the final render measures audio.
Use `FULL_VIDEO_URL` until the long video is live. Do not put a final filename,
render ID or guessed timecodes in the package.

For Instagram, Facebook and TikTok, keep hashtags out of `caption`; the Publish
screen appends the `hashtags` array automatically. YouTube hashtags should
remain in its `description`.

The application keeps the original uploaded JSON beside the project and imports
the plan itself: the Shorts tab lists each planned Short and applies its
sections, caption mode and hook in one click, and the Publish tab matches a
rendered Short back to its plan to seed the per-platform drafts. Unknown fields
are still ignored, so a package written for a newer build imports what this one
understands.

### The opening hook (`hook`)

A 2.2-second cold open: at most two lines, drawn in upper case near the vertical
canvas's **eye line**. It is what decides whether the Short is watched at
all, so it is authored with the section choice rather than left to the edit.

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | |
| `lines` | string[] | `[]` | At most two. Each under 42 characters, or the package is rejected. Empty means no hook. |
| `startSeconds` | 0.0–10.0 | `0.0` | |
| `durationSeconds` | 0.3–6.0 | `2.2` | Long enough for setup and impact to register. |

Rules that matter:

* **Two lines, short, strong, curious — not clickbait.** The documentary tone
  holds here too, and the Short must keep the promise the hook makes.
* **The line break is yours.** The app never re-breaks a hook; if it does not
  fit, the type shrinks. So break where the sentence breaks.
* **Write it in normal case.** Upper case is applied when drawing.
* **Give every Short a different hook.** Repeating one across two Shorts wastes
  both.

Good: `WHEN HE DIED,` / `THE SPECIES ENDED` · `THIS GIANT` / `DISAPPEARED
FOREVER` · `BILLIONS OF BIRDS.` / `THEN NONE.`

The first line is a restrained setup; the second lands larger, red, and with a
short upward punch. A locally synthesized rise and impact follow the same timing.
The hook is composited onto the 1080×1920 canvas when the Short is built, around
39–50% of its height and clear of the captions below it. It never touches the
long video, and it is never burned into the file the Short was cut from. It is
part of the Short's cache key, so changing the words produces a new Short rather
than serving the old one.

## Scene

```jsonc
{
  "title":       "A Bird Without Fear",
  "subtitle":    "Mauritius, before 1598",
  "narration":   "The dodo evolved in a world with no threats…",
  "imagePrompt": "A curious dodo standing calmly in a clearing…",
  "factNote":    "Mauritius had no native land mammals before humans arrived.",

  "suggestedAnimation": "slow-zoom-in",
  "focusX": 0.45,
  "focusY": 0.50,

  "titleStartSeconds":       0.6,
  "titleDurationSeconds":    4.5,
  "subtitleStartSeconds":    1.0,
  "subtitleDurationSeconds": 4.0,

  "imageFile": "01-opening.png"
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `title` | string | `""` | Large heading overlay. |
| `subtitle` | string | `""` | Smaller line beneath the title. |
| `narration` | string | `""` | What the voice says. **Drives the scene's duration.** |
| `imagePrompt` | string | `""` | Kept for reference and re-generation. |
| `factNote` | string | `""` | Optional caption. |
| `suggestedAnimation` | enum | `"auto"` | See below. `auto` lets the app choose. |
| `focusX`, `focusY` | 0.0–1.0 | `0.5` | Where the subject is, as a fraction of the image. `0,0` is top-left. Used for cropping to 16:9 and for focus zooms. |
| `titleStartSeconds` etc. | number | — | Seconds from the start of the scene. Omit to use defaults. |
| `imageFile` | string | `null` | Pins a specific image. Omit to map by filename order. |

### `suggestedAnimation` values

`auto` · `slow-zoom-in` · `slow-zoom-out` · `pan-left-to-right` ·
`pan-right-to-left` · `pan-top-to-bottom` · `pan-bottom-to-top` ·
`zoom-to-center` · `zoom-to-left` · `zoom-to-right` · `zoom-to-focus` ·
`gentle-diagonal` · `static`

`auto` is recommended for most scenes: the app assigns a deterministic,
non-repeating rotation of restrained documentary movements, so no two adjacent
scenes share an effect. Use an explicit value only where the shot needs it —
`zoom-to-focus` with a `focusX`/`focusY` on the subject is the most useful one.

## Section (intro / outro)

```jsonc
{
  "title":              "The Dodo",
  "subtitle":           "Raphus cucullatus",
  "hookText":           "Extinct within 100 years of meeting us",
  "narration":          "In 1598, Dutch sailors stepped onto an island…",
  "imagePrompt":        "Wide cinematic establishing shot…",
  "imageFile":          null,
  "useFirstSceneImage": false
}
```

The intro gets **its own image** by default (see below). Set
`useFirstSceneImage: true` to reuse scene 1's image instead — the old behaviour,
where the opening shares the first scene's picture.

## How images are matched

If a unit sets `imageFile`, that file is used. Everything else is filled from the
project's uploaded images in **natural filename order**, which sorts `2-x.png`
before `10-x.png` (plain alphabetical sorting does not).

The **intro takes the first image** whenever you upload at least one more image
than you have scenes — so a ten-scene video wants **eleven images**: one for the
intro, then one per scene. The simplest layout keeps your scene names and just
prepends the intro:

```
00-intro.png   01-opening.png   02-habitat.png   …   10-legacy.png
```

Without that spare image (exactly one per scene, or fewer) the intro falls back
to reusing the first scene's picture, exactly as before — so existing ten-image
projects are unaffected. The import report tells you exactly what happened: how
many images were mapped, which image became the intro's, which scenes were left
without one, and which images went unused. You can always remap by hand
afterwards.

## Import behaviour

- Importing **never** changes `video`, `style`, `music`, `subtitles` or `export`
  settings. The one thing it can change under `audio` is the narration voice,
  and only when the package names one in its `tts` block.
- `longIntro`, `tts` and every `hook` are optional. Absent means "keep what the
  project already has", which is what lets a package written before they existed
  import unchanged.
- **Replace scenes** (default) rebuilds the scene list from the package. Per-scene
  tuning — generated audio, manual durations, motion overrides — is lost.
- **Update in place** matches scenes by position, so audio and manual timing
  survive. Scenes beyond the package's length are kept, not deleted, and the
  report says so.
- Unknown fields are ignored rather than rejected, so a package produced by a
  newer generator still imports what this build understands.
- The import report says what arrived: how many scenes and images were mapped,
  whether the branded opening and the narration voice were applied, and how many
  planned Shorts came with a hook ready to draw.

## Completeness checklist

A package for this channel is finished when all of these are true:

- [ ] `contentSchemaVersion` is `2`.
- [ ] `videoTitle`, `description`, `tags`, `thumbnailText` and `thumbnailPrompt`
      are filled in.
- [ ] `longIntro` names both titles, and `typewriterDuration` and `stampAt` are
      both within `duration`. The stamp matches the species' real status.
- [ ] `pronunciation` covers every hard name spoken in the narration.
- [ ] `tts` names the voice the narration was written for.
- [ ] Every scene has `narration`, `title` and `imagePrompt`.
- [ ] `shortsPlan` holds 3–5 Shorts; each has a distinct `hook` of at most two
      lines, real `sections` numbers, and copy for all four platforms.
- [ ] There is one more image prompt than there are scenes, and the `imageFile`
      names match them exactly.

## Validation errors

Errors name the exact field path and are shown together, not one at a time:

```
scenes.0.focusX: Input should be less than or equal to 1
scenes.3.narration: String should have at most 20000 characters
```

A JSON syntax error reports the line and column and prints the surrounding
lines with the offending one marked `>>`.
