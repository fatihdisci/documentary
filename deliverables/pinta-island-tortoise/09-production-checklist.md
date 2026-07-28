# Pinta Island tortoise — Production checklist

## Content

- [ ] Import `pinta-island-tortoise-content-package.json`
- [ ] Confirm ten scenes plus dedicated intro and outro
- [ ] Confirm `00-intro.png` is not reused by scene one

## Images

- [ ] Generate all 11 images from `04-image-prompts.txt`
- [ ] Save with the exact names in `05-image-file-list.txt`
- [ ] Check tortoise anatomy and shell shape for consistency
- [ ] Keep modern objects and visible text out of all images
- [ ] Upload all images and run Auto-map

## Audio and preview

- [ ] Use Kokoro with the project default `af_bella` voice at 0.90x, unless the established channel voice differs
- [ ] Generate all missing narration audio
- [ ] Review pronunciation replacements
- [ ] Select lyric-free ambient documentary music with ducking enabled
- [ ] Render 1080p/30 FPS Preview
- [ ] Check subtitle timing, title placement and all scene transitions
- [ ] Replace draft chapter times with times measured from the final timeline

## Final export

- [ ] Render 1920×1080 constant 60 FPS final video
- [ ] Keep burned-in subtitles for the main export
- [ ] Keep the clean master enabled for Shorts-native captions
- [ ] Export and retain MP4, SRT, narration audio, description, thumbnail prompt and render log
- [ ] Generate three Shorts from the clean master using the selections in `07-youtube-shorts-metadata.md`

## YouTube

- [ ] Upload the long video first as private
- [ ] Attach thumbnail and SRT
- [ ] Schedule using `08-youtube-upload-schedule.md`
- [ ] Insert the long-video URL into all Short descriptions
- [ ] Verify notification settings
- [ ] Record final URLs and change tracker status to `Scheduled`

