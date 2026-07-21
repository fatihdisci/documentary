# Content package schema

A **content package** is a single JSON file holding everything *authored* about
one animal video: narration, titles, image prompts and framing hints. It carries
no file paths, timings or render settings, so importing one never disturbs how
you have configured the video, style or audio.

Download a working example from **Content → Download example template**, or find
it at `backend/fixtures/dodo-content.json`.

## Top level

```jsonc
{
  "contentSchemaVersion": 1,

  "commonName":      "Dodo",
  "scientificName":  "Raphus cucullatus",

  "videoTitle":      "The Dodo: How We Erased a Bird in a Single Lifetime",
  "description":     "Full YouTube description, including chapters.",
  "tags":            ["dodo", "extinction"],
  "thumbnailText":   "GONE IN 100 YEARS",
  "thumbnailPrompt": "Prompt for generating the thumbnail image.",

  "pronunciation": {
    "Raphus cucullatus": "RAH-fus koo-koo-LAH-tus",
    "Mauritius": "muh-RISH-us"
  },

  "intro":  { /* section, see below */ },
  "scenes": [ /* 1-200 scenes, see below */ ],
  "outro":  { /* section */ }
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `contentSchemaVersion` | int | no | Defaults to `1`. |
| `commonName` | string | no | Fills the project's animal name. |
| `scientificName` | string | no | Shown as a subtitle and read by TTS. |
| `videoTitle` | string | no | Exported to `description.txt`. |
| `description` | string | no | Up to 10 000 characters. |
| `tags` | string[] | no | |
| `thumbnailText` | string | no | Overlaid text for your thumbnail. |
| `thumbnailPrompt` | string | no | Exported to `thumbnail.txt`. |
| `pronunciation` | object | no | Applied to narration before synthesis. |
| `scenes` | array | **yes** | At least 1, at most 200. |

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

Set `useFirstSceneImage: true` to reuse scene 1's image instead of supplying a
dedicated one.

## How images are matched to scenes

If a scene sets `imageFile`, that file is used. Every other scene is filled from
the project's uploaded images in **natural filename order**, which sorts
`2-x.png` before `10-x.png` (plain alphabetical sorting does not). Name your
files with a numeric prefix:

```
01-opening.png   02-habitat.png   03-anatomy.png   …   10-conservation.png
```

The import report tells you exactly what happened: how many images were mapped,
which scenes were left without one, and which images went unused. You can always
remap by hand afterwards.

## Import behaviour

- Importing **never** changes `video`, `style`, `audio`, `music`, `subtitles` or
  `export` settings.
- **Replace scenes** (default) rebuilds the scene list from the package. Per-scene
  tuning — generated audio, manual durations, motion overrides — is lost.
- **Update in place** matches scenes by position, so audio and manual timing
  survive. Scenes beyond the package's length are kept, not deleted, and the
  report says so.
- Unknown fields are ignored rather than rejected, so a package produced by a
  newer generator still imports what this build understands.

## Validation errors

Errors name the exact field path and are shown together, not one at a time:

```
scenes.0.focusX: Input should be less than or equal to 1
scenes.3.narration: String should have at most 20000 characters
```

A JSON syntax error reports the line and column and prints the surrounding
lines with the offending one marked `>>`.
