# Publishing (the **Yayınla** tab)

The Publish panel takes a video you have already made — a long render or a
Short — and puts it on a platform: YouTube or a TikTok post.

Everything on this page happens on your own computer. The only services
contacted are the ones you connect, and only when you ask for it.

- [What you can publish](#what-you-can-publish)
- [Connecting a YouTube account](#connecting-a-youtube-account)
  - [One-time Google Cloud setup](#one-time-google-cloud-setup)
  - [Existing credentials are detected automatically](#existing-credentials-are-detected-automatically)
  - [Where the token is stored](#where-the-token-is-stored)
  - [Why OAuth and not an API key](#why-oauth-and-not-an-api-key)
  - [Reconnecting, and what to do when scopes change](#reconnecting-and-what-to-do-when-scopes-change)
- [Filling in the metadata](#filling-in-the-metadata)
- [Uploading now](#uploading-now)
- [Scheduling an upload](#scheduling-an-upload)
- [Thumbnails](#thumbnails)
- [Subtitles (.srt)](#subtitles-srt)
- [Upload history](#upload-history)
- [Duplicate protection](#duplicate-protection)
- [TikTok](#tiktok)
  - [The audit requirement](#the-audit-requirement)
  - [Setting up the TikTok app](#setting-up-the-tiktok-app)
- [Where every credential lives](#where-every-credential-lives)
- [Troubleshooting](#troubleshooting)

---

## What you can publish

The file list shows two kinds of media, newest first:

- **Long videos** — every completed render that still has its manifest and its
  MP4 on disk. The manifest is written only when a render finishes and its
  output passes validation, so anything listed here really is finished.
- **Shorts** — every finished Short in `exports/shorts/`.

Each card shows the filename, the type, when it was made, its duration, size,
resolution and source project, plus a **Önizle** link that opens the file.

Renders made at **Hızlı deneme** (preview) quality, and files that have changed
since they were made, are marked and hidden behind
*"Yayına uygun olmayanları da göster"* — they are still selectable if you really
want one.

Selecting a file loads that file's own publishing draft. Drafts are per file:
editing the long video's title never touches a Short's.

---

## Connecting a YouTube account

Go to **Ayarlar → Bağlantılar ve servisler**. The YouTube block shows exactly
what is and is not in place: the OAuth client file, the stored authorization,
whether the permissions are sufficient, and the connected channel's name and ID.

### One-time Google Cloud setup

This is the only part you do outside the app, and only once per Google account:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create
   (or pick) a project.
2. Enable **YouTube Data API v3** for it.
3. Configure the **OAuth consent screen**. While the project is unpublished, add
   your own Google account under **Test users** — otherwise Google refuses the
   sign-in.
4. Create an **OAuth Client ID** and choose **Desktop app** as the type. Not
   *Web application*: the app uses the desktop loopback flow, and a web client
   will be rejected with an explanation.
5. Download the JSON file it offers.
6. In the app: **Ayarlar → Bağlantılar ve servisler → OAuth istemci dosyası
   seç**, and pick that file. (Or copy it into
   `~/ExtinctVideoBuilder/secrets/` yourself — the app finds it either way.)
7. Press **YouTube'a bağlan**. Your browser opens; sign in with the account that
   owns the channel and grant the permissions.

The uploaded file is validated before it is stored: it must be valid JSON, have
an `installed` section, and contain `client_id`, `client_secret`, `auth_uri` and
`token_uri`. An invalid file is refused and never written to disk.

### Existing credentials are detected automatically

If you already used the release scripts in `backend/scripts/`, you already have
both files, and there is nothing to set up. The app looks in
`~/ExtinctVideoBuilder/secrets/` for:

- `client_secret_*.json` (or `oauth-client-*.json`) — the OAuth client;
- `youtube-upload-token.json` — the stored authorization.

If several client files are present, the app uses the one you picked in Settings;
with no choice made, it uses the newest one that has a valid structure. Only the
file's *name* is ever shown in the interface — never its contents, and never the
full path.

The Publish panel and `backend/scripts/youtube_schedule.py` now share the same
client file, the same token and the same scopes, so connecting in one place
connects both.

### Where the token is stored

```
~/ExtinctVideoBuilder/secrets/
├── client_secret_*.json          the OAuth client you installed   (0600)
├── youtube-upload-token.json     your authorization               (0600)
└── youtube-channel-cache.json    channel name/ID cache, no secrets (0600)
```

The directory is created with `0700` and every file with `0600`, so only your
user account can read them. These files are:

- **never** returned by any API endpoint,
- **never** written to a log — even masked,
- **never** included in a project backup or an export bundle,
- **never** shown in the interface,
- ignored by Git (see `.gitignore`).

Google's own error messages are scrubbed of anything credential-shaped before
they reach a log or the screen.

**Bağlantıyı kaldır** deletes `youtube-upload-token.json` and nothing else. Your
OAuth client file stays where it is, so reconnecting is one click. Nothing
happens to the videos already on your channel.

### Why OAuth and not an API key

An API key identifies an *application*; it cannot act on behalf of a person. The
YouTube Data API refuses `videos.insert`, `thumbnails.set` and `captions.insert`
with a key alone — uploading requires an account's permission. That is why this
panel asks you to connect an account rather than to paste a key.

### Reconnecting, and what to do when scopes change

The app requests three permissions:

```
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtube.readonly
https://www.googleapis.com/auth/youtube.force-ssl
```

`youtube.force-ssl` is what allows subtitle uploads. Tokens created by the older
release scripts only carried the first two. The app detects this and says so:

> Altyazı yükleme yetkisi için YouTube hesabınızı yeniden bağlayın.

It will not quietly use the narrower token and then fail at the subtitle step
with the video already uploaded. Press **Yeniden bağlan**, and the browser flow
runs again with the full set. Nothing else changes: same channel, same videos,
same client file.

---

## Filling in the metadata

When you select a file, the draft is seeded from the project's own metadata:

| Panel field | Comes from |
| --- | --- |
| Başlık | `project.metadata.videoTitle` (or the project name) |
| Açıklama | `project.metadata.description` |
| Etiketler | `project.metadata.tags` |
| Thumbnail metni | `project.metadata.thumbnailText` |
| Thumbnail prompt'u | `project.metadata.thumbnailPrompt` |

Every field can be changed by hand before uploading. **Editing here never
changes the project.** The values are stored as a *publishing draft* belonging to
the selected file, in `<project>/publishing/drafts.json`. Use **Proje
metadatasından tekrar doldur** to throw the draft's text away and take the
project's values again.

Drafts save themselves shortly after you stop typing; the header shows
*Kaydedildi* / *Kaydedilmedi* so you always know where you stand.

**Thumbnail metni** and **Thumbnail prompt'u** are reference-only. They are never
sent to YouTube — they are there so you can read your own notes while making the
actual thumbnail image.

### Limits, checked on both sides

| Field | Limit |
| --- | --- |
| Başlık | 100 characters, and no `<` or `>` |
| Açıklama | 5 000 **UTF-8 bytes**, and no `<` or `>` |
| Etiketler | 500 characters in total |

The counters beside each field are live. The description limit is counted in
bytes, not characters, because that is how YouTube counts it: a Turkish `ğ` or an
emoji costs more than one. A tag containing a space is quoted on the wire, so it
costs two characters more than it looks; the counter accounts for that.

Tags are added with Enter or a comma, removed with the × on the chip, and empty
or repeated tags are dropped automatically.

Other fields: **Kategori** (default `15`, Pets & Animals), **Varsayılan metadata
dili** and **Varsayılan ses dili** (default `en`), **Çocuklara özel içerik**,
**Abonelere bildirim gönder**, **Yerleştirmeye izin ver** and **Gizlilik**
(Gizli / Liste dışı / Herkese açık).

---

## Uploading now

1. Select a file.
2. Check the metadata.
3. Press **YouTube'a yükle** on the YouTube card.
4. A confirmation window lists what is about to happen — the file, the title, the
   privacy, the publish time, whether a thumbnail and an SRT are attached, and
   whether subscribers will be notified. Nothing is sent until you confirm.

While it runs you see the overall percentage, the current step, the megabytes
sent so far, and a cancel button. The upload is *resumable* and sent in 8 MB
chunks, so progress moves steadily even on a slow line. Cancelling stops it
between chunks.

If the connection to the browser drops mid-upload the job keeps running on the
backend, and the panel picks it up again when you come back.

When it finishes you get the video ID, the link, the publish time, the privacy
status, and the result of the thumbnail and subtitle steps.

---

## Scheduling an upload

Choose **İleri tarihe planla** and pick a date and time. All times in this app
are **Europe/Istanbul** wall-clock times — what your own clock says. The backend
binds them to that zone using the IANA time-zone database (so summer time is
handled correctly, not assumed) and sends YouTube an RFC 3339 value with a real
offset.

Two things follow from how YouTube models scheduling:

- A scheduled video is uploaded as **private** and becomes visible at the chosen
  moment. This is YouTube's own model, not a decision of this app, and the
  privacy buttons are disabled while scheduling is selected.
- A time in the past is **rejected**, with a clear message. It is never silently
  treated as "publish now".

The button becomes **YouTube'a yükle ve planla** when scheduling is on.

---

## Thumbnails

Press **Görsel seç** and pick a JPEG or PNG, at most 2 MB (YouTube's own limit).

The file is validated from its *content*, not its extension: a GIF renamed to
`.png` is refused. It is stored inside the project, under
`<project>/publishing/assets/thumbnails/`, with a sanitized name.

The thumbnail is set after the video upload completes, with
`thumbnails().set()`. If that fails, **the video is not re-uploaded**: the job
finishes with a warning, the video and its link are already recorded, and
**Kalan adımları tekrar dene** retries only the thumbnail step.

Note that YouTube only accepts custom thumbnails on verified accounts. If yours
is not verified, the video uploads fine and only this step fails.

---

## Subtitles (.srt)

For a long render the app looks for the English `.srt` that render produced, and
attaches it automatically. The match comes from the **render job's recorded
artifacts** — not from guessing at filenames — so a stray `.srt` in the exports
folder can never be attached to the wrong video. If the render is old enough that
its job record has been pruned, no file is attached and you can pick one by hand.

You can always choose your own file with **.srt seç**. It is validated from its
content: an empty file, one that is not UTF-8, or one with no timing lines is
refused rather than uploaded to produce silently-empty captions.

Defaults: language `en`, track name `English`, `isDraft` off.

For **Shorts** the automatic option is off by default, because a Short's captions
are usually burned into the picture and a second track would double them up. You
can still attach one manually.

Subtitles are uploaded after the video, with `captions().insert()`. As with
thumbnails, a failure here never re-uploads the video — the retry resumes from
the subtitle step alone.

---

## Upload history

Every video that reached YouTube is recorded in
`<project>/publishing/history.json` **the moment it got an ID** — before the
thumbnail and subtitle steps run. That is deliberate: a video that uploaded but
whose thumbnail failed is still listed, with its link, so nothing that exists on
your channel is invisible here.

Each row shows the title (linked), the file, the publish time, the privacy
status, whether the thumbnail and subtitles went up, and YouTube's processing
state.

YouTube processes a video for a while after the upload finishes. When it has not
finished processing, the panel says:

> Video YouTube'a gönderildi, YouTube tarafından işleniyor.

**Durumu yenile** re-reads that video's state with `videos.list` and updates the
row.

---

## Duplicate protection

Before an upload starts, the app records the source file's name, size and
SHA-256. If a file with the same checksum has already been published **to that
platform** from this computer, the panel says so:

> Bu dosya daha önce YouTube'a yüklenmiş.

…and the publish button is disabled until you tick the "yine de yükle" box,
which is off by default and asks again in the confirmation window.

**The check is per platform.** A video already on YouTube can still go to
TikTok, and a post already on TikTok is no reason to refuse YouTube. Each
platform keeps its own job, its own history entry and its own guard, and a
failure on one never causes a re-upload anywhere else.

Detection is by file content, never by title: two different videos may
legitimately share a title, and the same bytes going up twice is the thing worth
warning about. It also covers the case where the same file is kept under two
names — two exports with different `mediaId`s but identical bytes cannot be
queued for the same platform at the same time.

If the source file has changed since the draft was written — a re-render, say —
the panel warns and blocks the upload until you review the metadata. The checksum
is verified once more immediately before the upload begins, and the history is
re-read one last time before any bytes leave.

---

## TikTok

TikTok takes the **file itself**. The bytes go straight from your computer to
TikTok's upload URL in chunks, so the video is never placed anywhere public on
its way there — exactly like a YouTube upload.

### The audit requirement

Until your TikTok app passes the **Content Posting API audit**, everything it
posts is restricted to *you only*. This is TikTok's rule, not a limitation of
this app, and the app is explicit about it:

- the TikTok card shows a warning while the app is unaudited;
- the **Gizlilik** dropdown lists only the options TikTok itself reported for
  your account — so an unaudited app shows only *Yalnızca ben*;
- the finished job carries a warning saying the post is visible only to you;
- a self-only post has no public link, and none is invented.

To lift this, apply for the Content Posting API audit in the TikTok Developer
portal. Nothing in the app changes when you are approved except the options
TikTok starts reporting.

### Setting up the TikTok app

1. Create an app at TikTok for Developers.
2. Add the **Login Kit** and **Content Posting API** products.
3. Request the scopes `user.info.basic` and `video.publish`.
4. Add the callback URL as a **Redirect URI**:
   ```
   http://localhost:8756/api/publishing/tiktok/callback
   ```
   **TikTok only accepts HTTPS redirect URIs.** A loopback backend cannot serve
   HTTPS on its own, so to complete the flow you need an HTTPS address pointing
   at this path — a tunnel or a reverse proxy — and then set `tiktokRedirectUri`
   in `settings.json` to that address. The app does not pretend the default one
   is registrable; the card says this in as many words.
5. Enter the **Client Key** and **Client Secret** in the app. They are
   write-only: stored at `0600`, never returned, replaceable but not readable.
6. Press **TikTok'a bağlan**. The flow uses PKCE (S256); the verifier never
   leaves the backend.

Before every post the app queries `creator_info`, so the privacy options and the
comment/duet/stitch toggles reflect what your account can actually do right now.
A privacy level the account cannot use is refused *before* a single byte is
sent.

---

## Where every credential lives

```
~/ExtinctVideoBuilder/
├── secrets.json                  0600  API keys, TikTok app credentials
└── secrets/                      0700
    ├── client_secret_*.json      0600  the Google OAuth client
    ├── youtube-upload-token.json 0600  your YouTube authorization
    ├── youtube-channel-cache.json 0600 channel name/ID cache, no secrets
    └── tiktok-token.json         0600  the TikTok grant
```

Every one of these is:

- **never** returned by any API endpoint,
- **never** written to a log — even masked,
- **never** included in a project backup or an export bundle,
- **never** shown in the interface,
- ignored by Git.

Each platform module scrubs anything credential-shaped out of error text before
it reaches a log or the screen: Google's `ya29.…` tokens and TikTok's `act.…`
tokens.

---

## Troubleshooting

**"YouTube için OAuth istemci dosyası bulunamadı."**
No client file is installed. Follow [One-time Google Cloud
setup](#one-time-google-cloud-setup), or copy an existing
`client_secret_*.json` into `~/ExtinctVideoBuilder/secrets/`.

**"Bu bir 'Web application' istemcisi."**
The OAuth client was created with the wrong type. Create a new one and choose
**Desktop app**.

**"YouTube bağlantınız altyazı yükleme yetkisini içermiyor."**
Your token predates the subtitle feature. Press **Yeniden bağlan**; see
[Reconnecting](#reconnecting-and-what-to-do-when-scopes-change).

**"YouTube kotası doldu."**
The Data API has a daily quota, and an upload is expensive against it. It resets
at midnight Pacific time. Wait and retry — nothing is lost.

**The video uploaded but is private, even though I chose public.**
An *unverified* Google Cloud project can force uploads to stay private, whatever
the request asked for. The app detects the difference and adds a warning to the
job. To fix it, complete Google's verification for the Cloud project, or set the
video's visibility on YouTube Studio afterwards.

**"Bu Google hesabına bağlı bir YouTube kanalı bulunamadı."**
The account you authorized has no channel. Create one, or reconnect with the
account that owns the channel.

**The upload was interrupted by a restart.**
Jobs running when the backend stops are marked *interrupted* on disk and shown
that way. Press **Tekrar dene**: if the video had already reached the platform it
is *not* uploaded again, and only the remaining steps run.

**My TikTok post is not visible to anyone.**
That is the audit restriction, not a bug. See [The audit
requirement](#the-audit-requirement).

**TikTok will not accept my redirect URI.**
TikTok requires HTTPS. Point an HTTPS address at
`/api/publishing/tiktok/callback` and set `tiktokRedirectUri` in
`settings.json` to it.

**Where are the logs?**
`~/ExtinctVideoBuilder/logs/backend.log`. Credentials never appear in it.

---

See also: [`docs/user-guide.md`](user-guide.md) for the tabs that come before
this one, and [`docs/troubleshooting.md`](troubleshooting.md) for render
problems.
