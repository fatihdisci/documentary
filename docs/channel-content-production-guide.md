# Extinct Animals Documentary Channel — Persistent Production Guide

## Purpose and audience

Create 4–7 minute English-language documentary videos for a global YouTube channel. Each episode tells the story of one extinct animal. Speak to the project owner in Turkish; every viewer-facing asset must be in English.

The workflow is: import the content JSON, generate images from the prompts, upload them using the exact filenames, choose TTS and music, create audio, check the 1080p/30 FPS preview, then render the 1920×1080/60 FPS final video. Final video is rendered with FFmpeg, not Canvas. Text is made as Pillow RGBA PNG overlays; do not require FFmpeg `drawtext` or `libass`.

## Research standard

Research every new species before writing. Prefer IUCN Red List, BirdLife International, Smithsonian, Natural History Museum, universities, museums, peer-reviewed research, conservation bodies, and government agencies. Verify common and scientific names, taxonomic status, habitat, appearance, last confirmed record, extinction status or date, drivers of decline, and last individual when known.

When sources differ, avoid false certainty. Use careful wording such as “by the late seventeenth century,” “the last confirmed sighting,” and “several pressures contributed.” Keep a source list with direct links in every package.

## Narration and TTS rules

- Write natural, engaging English documentary narration with short, smooth sentences.
- Prefer common, easy-to-pronounce words. Avoid Latin, technical labels, and scientific jargon in spoken narration.
- Keep a scientific name only in metadata when needed. Provide a pronunciation entry for any unavoidable difficult name.
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

Valid JSON only: no comments and no trailing commas. Required top-level fields: `contentSchemaVersion`, `commonName`, `scientificName`, `videoTitle`, `description`, `tags`, `thumbnailText`, `thumbnailPrompt`, `pronunciation`, `intro`, `scenes`, `outro`.

Each scene should include `title`, `subtitle`, `narration`, `imagePrompt`, `factNote`, `suggestedAnimation`, `focusX`, `focusY`, `titleStartSeconds`, `titleDurationSeconds`, `subtitleStartSeconds`, `subtitleDurationSeconds`, and `imageFile`. Image filenames must match the prompts exactly, be short ASCII names, and contain no spaces or parentheses. Suggested convention: `00-intro.png`, then `01-opening.png`, `02-habitat.png`, and onward.

Subtitles are embedded by default and an external SRT is always exported. Embedded subtitles may be disabled for a clean visual version.

## Delivery checklist

For every episode deliver: research summary; sources; at least three titles and a recommended title; thumbnail text and prompt; YouTube description; tags; intro; scene packages; outro; import-ready JSON; a TXT list of every prompt including `00-intro`; exact filename list; next-episode teaser; and a content-tracker update.

## Content tracker

Never re-suggest an animal already produced as a main video. Status values: `Planned`, `Researching`, `Package Ready`, `Images Ready`, `Audio Ready`, `Rendering`, `Published`, `Revisit Candidate`.

Initial priority list: Dodo; Tasmanian tiger; Steller’s sea cow; Passenger pigeon; Carolina parakeet; Pinta Island tortoise; Chinese paddlefish; Golden toad; Rocky Mountain locust; Xerces blue butterfly; Southern gastric-brooding frog; Bramble Cay melomys; Sea mink; Labrador duck; Stephens Island wren; Alaotra grebe; Atitlán grebe; Cape Verde giant skink; Round Island burrowing boa; Delcourt’s giant gecko.
