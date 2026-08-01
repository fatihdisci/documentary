# Extinct Animals Documentary Channel — Persistent Production Guide

## Purpose and audience

Create 4–7 minute English-language documentary videos for a global YouTube channel. Each episode tells the story of one extinct animal. Speak to the project owner in Turkish; every viewer-facing asset must be in English.

The workflow is: import the content JSON, generate images from the prompts, upload them using the exact filenames, choose TTS and music, create audio, check the 1080p/30 FPS preview, then render the 1920×1080/60 FPS final video. Kokoro is the default local TTS provider; Edge, imported audio and optional ElevenLabs remain available. Final video is rendered with FFmpeg, not Canvas. Text is made as Pillow RGBA PNG overlays; do not require FFmpeg `drawtext` or `libass`.

## Research standard

Research every new species before writing. Prefer IUCN Red List, BirdLife International, Smithsonian, Natural History Museum, universities, museums, peer-reviewed research, conservation bodies, and government agencies. Verify common and scientific names, taxonomic status, habitat, appearance, last confirmed record, extinction status or date, drivers of decline, and last individual when known.

When sources differ, avoid false certainty. Use careful wording such as “by the late seventeenth century,” “the last confirmed sighting,” and “several pressures contributed.” Keep a source list with direct links in every package.

## Narration and TTS rules

- Write natural, engaging English documentary narration with short, smooth sentences.
- Prefer common, easy-to-pronounce words. Avoid Latin, technical labels, and scientific jargon in spoken narration.
- Treat Kokoro as the default TTS target. Write plain text only; do not use SSML, HTML, or Markdown in narration.
- Use punctuation for natural pauses: full stops for longer pauses and commas for shorter ones.
- Write numbers, years, dates, and abbreviations as they should be spoken. For example, use “nineteen fourteen” instead of “1914.”
- Keep each intro, scene, and outro between two and six sentences; five natural sentences is the default target.
- Keep scientific names in metadata whenever possible. Use unavoidable difficult proper names only once or twice, and add easy phonetic replacements to the JSON `pronunciation` field.
- Avoid long parentheticals, tongue-twisters, dense lists, complicated clauses, filler, and repeated facts.
- Use a strong hook immediately. Never open with “Welcome back to the channel.”
- Do not pad scenes to equal length; each scene should earn its duration.
- Outro = meaningful close + conservation message + brief teaser of the next animal + one concise subscribe sentence.

## Story structure

Use one intro, 8–12 main scenes, and one outro. Adapt the sequence to the animal, generally moving through hook, introduction, habitat, appearance, behaviour/diet, ecosystem role, contact with people/threats, decline, last years, evidence, legacy, and present-day lesson.

## Image direction — mandatory variety

Use a dedicated cold-open intro image. It must not reuse scene one. Supply exactly one more image than the number of scenes: `00-intro.png` plus one image for every scene. Set `intro.imageFile` to `00-intro.png` and `intro.useFirstSceneImage` to `false`.

Every prompt must repeat the same core, scientifically plausible description of the animal so it remains consistent. The standard style is: cinematic wildlife documentary reconstruction, historically appropriate environment, realistic anatomy, photorealistic, natural lighting, restrained colour grading, subtle film grain, high detail, no text, no watermark, no logo, no modern objects, 16:9 widescreen.

Make every image visually distinct. Vary all of the following across the set:

- camera distance and angle: aerial, shoreline-level, underwater, close detail, wide landscape, over-the-shoulder, low angle;
- moment and weather: dawn, noon, blue hour, moonlight, fog, snow squall, calm water, storm aftermath;
- subject count and action: solitary animal, pair, family group, grazing, travelling, distant silhouette, scientific evidence;
- framing and depth: foreground kelp or rocks, negative space, background animal, layered wide view.

Do not place the animal in the centre of every image. Prefer left/right thirds, foreground edges, or distant background placement. Avoid near-duplicate images in a single video. Do not make graphic violence; show hunting or threat through implication, distance, tools, boats, empty water, or aftermath.

## JSON and scene requirements

Valid JSON only: no comments and no trailing commas. Required top-level fields: `contentSchemaVersion`, `commonName`, `scientificName`, `videoTitle`, `description`, `tags`, `thumbnailText`, `thumbnailPrompt`, `pronunciation`, `intro`, `scenes`, `outro`. Every newly authored channel package must also include the `shortsPlan` production manifest.

Each scene should include `title`, `subtitle`, `narration`, `imagePrompt`, `factNote`, `suggestedAnimation`, `focusX`, `focusY`, `titleStartSeconds`, `titleDurationSeconds`, `subtitleStartSeconds`, `subtitleDurationSeconds`, and `imageFile`. Image filenames must match the prompts exactly, be short ASCII names, and contain no spaces or parentheses. Suggested convention: `00-intro.png`, then `01-opening.png`, `02-habitat.png`, and onward.

Subtitles are embedded by default and an external SRT is always exported. Embedded subtitles may be disabled for a clean visual version.

### Shorts plan — mandatory hand-off

Put three to five Short proposals in `shortsPlan` in the same JSON. Each proposal
must identify its source with ordered section references, for example
`[{"kind":"scene","number":2},{"kind":"scene","number":3}]`, rather
than vague scene titles. Include: a stable `id`, priority, editorial purpose,
estimated duration, YouTube title/alternatives/description/tags/hashtags/pinned
comment, and a TikTok caption, hashtags and call to action. Use `shorts-native`
captions with the `large` preset.
Keep social hashtags out of `caption`; the publishing layer appends the
`hashtags` array automatically. Keep YouTube hashtags in its description.

Prefer two to four adjacent scenes per Short and target roughly 20–55 seconds.
The estimate is not a timecode: the final render's measured timeline is
authoritative for trimming. Use `FULL_VIDEO_URL` in copy until the long video is
published. The current app preserves the uploaded source JSON but does not yet
apply `shortsPlan` automatically to the Shorts or Publish screens.

## Delivery checklist

For every episode deliver: research summary; sources; at least three titles and a recommended title; thumbnail text and prompt; YouTube description; tags; intro; scene packages; outro; import-ready JSON including `shortsPlan`; a TXT list of every prompt including `00-intro`; exact filename list; ready-to-use publishing copy for every planned Short; next-episode teaser; and a content-tracker update.

## Content tracker

Never re-suggest an animal already produced as a main video. Status values: `Planned`, `Researching`, `Package Ready`, `Images Ready`, `Audio Ready`, `Rendering`, `Scheduled`, `Published`, `Revisit Candidate`.

Initial priority list: Dodo; Tasmanian tiger; Steller’s sea cow; Passenger pigeon; Carolina parakeet; Pinta Island tortoise; Chinese paddlefish; Golden toad; Rocky Mountain locust; Xerces blue butterfly; Southern gastric-brooding frog; Bramble Cay melomys; Sea mink; Labrador duck; Stephens Island wren; Alaotra grebe; Atitlán grebe; Cape Verde giant skink; Round Island burrowing boa; Delcourt’s giant gecko.
