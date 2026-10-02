# Faceless Shorts Pipeline

Makes YouTube Shorts automatically for **Mindset Minute** and **Storytime Cuts**:

```
Claude writes script → ElevenLabs voice → Pexels/Pixabay stock footage → FFmpeg builds 9:16 video
with word-by-word captions → Discord: you tap Approve → uploads to YouTube
```

Runs 24/7 on your computers. You control everything from the Discord app on your phone.

---

## 1. Install (each computer)

1. **Python 3.11+** from python.org (Windows: tick *"Add Python to PATH"*).
2. **FFmpeg**
   - Windows: open PowerShell and run `winget install Gyan.FFmpeg`
   - Mac: install Homebrew, then `brew install ffmpeg`
3. Open a terminal **in this folder** and run:
   ```
   pip install -r requirements.txt
   ```
4. **Fonts**: run `python run.py fonts`. It downloads every font the pipeline uses into `fonts/`
   (all SIL Open Font License; each licence is saved as `fonts/LICENSE-<Family>.txt`):
   - **Anton** - Shorts captions (English)
   - **Cinzel** + **Noto Sans** - Candlelit Tales English titles + subtitles
   - **Chonburi** + **Noto Sans Thai** - Thai titles + subtitles (set `font: Noto Sans Thai` for a Thai Shorts channel)
   - **Noto Serif SC** + **Noto Sans SC** - Chinese titles + subtitles. These come from Google's
     `notofonts/noto-cjk` repo as fixed weights: the google/fonts versions are variable fonts whose
     default weight is Thin. They are large, so they are not stored in git - run `fonts` on each computer.

Check it works (no keys needed): `python run.py test-video` → opens `test_video.mp4`.

## 2. API keys → `.env`

Copy `.env.example` to `.env` and fill in:

| Key | Where | Cost |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys | pay per use, a few cents per script |
| `ELEVENLABS_API_KEY` | elevenlabs.io → Profile → API Keys | paid plan needed for commercial use (Starter ~$5/mo) |
| `PEXELS_API_KEY` | pexels.com/api | free (new keys sometimes paused) |
| `PIXABAY_API_KEY` | pixabay.com/api/docs (log in; key shown under *Parameters*) | free - use instead of Pexels |

**Voice:** in ElevenLabs open *Voices*, pick a voice per channel, copy its **Voice ID** into
`voice_id:` in `config.yaml`. A calm, deep voice suits Mindset; a warm, dramatic voice suits Storytime.

Language: set `language: th` for a channel to make it Thai (script, voice and captions).

## 3. Try one video by hand

```
python run.py generate --channel mindset_minute
python run.py review
```
Videos appear in `output/review/`. Edit the `style:` text in `config.yaml` to change the tone.

## 4. Connect YouTube (once per channel)

1. Go to console.cloud.google.com → create a project.
2. *APIs & Services → Library* → enable **YouTube Data API v3**.
3. *OAuth consent screen* → External → add your Gmail as a **test user**. Then click
   **Publish app** (otherwise Google logs you out every 7 days).
4. *Credentials → Create credentials → OAuth client ID → Desktop app* → download the JSON,
   rename it to **`client_secret.json`** and put it in this folder.
5. Run, for each channel:
   ```
   python run.py auth --channel mindset_minute
   python run.py auth --channel storytime_cuts
   ```
   In the browser, **choose the matching channel** (not your personal one).

## 5. Important YouTube limits

- **Private uploads until verified.** YouTube only allows API projects that passed its free
  *API audit* to publish videos publicly. Until then, videos upload as **private**, and the
  bot tells you the time slot to schedule them. Open the **YouTube Studio** app, open the video
  → Visibility → Schedule. It takes 20 seconds.
  To remove this step, apply for the audit (search "YouTube API Services audit and quota
  extension form"); once approved set `api_verified: true` in `config.yaml`.
- **Daily quota:** the default quota allows about **6 uploads per day per Google project**.
  Use a separate Google project for the second computer.
- **Keep the review step.** YouTube refuses to monetize mass-produced, repetitive content.
  Varied scripts and a human check protect your channels.
- Storytime videos say "This is a fictional story" in the description. Keep it.

## 6. Discord control bot (24/7)

### Create the bot (do this once per computer: PC-1 and PC-2 each get their own bot)
1. In Discord, create a server (e.g. "Clip Factory") with text channels **#pc1-control**
   and **#pc2-control**.
2. Go to discord.com/developers/applications → **New Application** (e.g. "PC-1 Bot")
   → *Bot* → **Reset Token** → copy it into `DISCORD_BOT_TOKEN`.
3. *OAuth2 → URL Generator*: tick **bot** and **applications.commands**; permissions:
   Send Messages, Embed Links, Attach Files, Read Message History. Open the link and add
   the bot to your server.
4. Discord *Settings → Advanced → Developer Mode* ON. Right-click the server → Copy Server ID →
   `DISCORD_SERVER_ID`. Right-click #pc1-control → Copy Channel ID → `DISCORD_CONTROL_CHANNEL_ID`.
5. In `.env` set `MACHINE_NAME=PC-1` and which channels this computer makes, e.g.
   - PC-1: `MACHINE_CHANNELS=mindset_minute`
   - PC-2: `MACHINE_CHANNELS=storytime_cuts`

   Never let two computers make the same channel.

### Start it
- Windows: double-click **`start_bot.bat`** (restarts automatically if it crashes)
- Mac: `chmod +x start_bot.sh` once, then `./start_bot.sh`

The bot posts "PC-1 online" in its control channel.

### Control panel (buttons)

The bot keeps a **control panel** message with buttons at the bottom of its control channel
(re-posted after every job, so it is always easy to find):

| Button | What it does |
|---|---|
| **Status** | Idle or working, paused, next run, videos waiting |
| **Make video** | Pick a channel (or all) and make one video now |
| **Show pending** | Re-post videos waiting for review |
| **Upload approved** | Upload all approved videos |
| **Pause / Resume daily run** | Stop or restart the daily run |
| **Set daily time** | Change the daily run time (24h, e.g. 01:30) |

The same actions also exist as slash commands (`/status`, `/generate`, `/pending`, `/upload`,
`/pause`, `/resume`, `/settime`), and `/panel` re-posts the button panel.

Every new video arrives as a card with a preview, the script, and **Approve / Reject**
buttons. Approve uploads it straight away.

### Keep the computer running 24/7
- **Windows:** Settings → System → Power → Sleep: **Never**. Make it start at boot: press
  Win+R, type `shell:startup`, and put a **shortcut** to `start_bot.bat` in that folder.
- **Mac:** System Settings → Energy → *Prevent automatic sleeping*. Add `start_bot.sh` to
  *Login Items* (or keep a Terminal window open).
- Turn on automatic restart after power failure in your BIOS or Energy settings if available.

## 7. Story mode - Candlelit Tales (long narrated stories + Shorts trailers)

Three channels narrate the **same public-domain story**: English (the original text), Thai and
Simplified Chinese (our **own** translations, made by Claude from the English original - never
an existing published translation).

| key | channel | language |
|---|---|---|
| `candlelit_en` | Candlelit Tales | English |
| `candlelit_th` | นิทานใต้แสงเทียน | Thai |
| `candlelit_zh` | 烛光夜话 | Simplified Chinese |

They are **not** part of the daily Shorts run. Settings live in `story:` in `config.yaml`
(which languages to publish, target length, music volume, crossfades, publish time ...).

### Copyright rules (enforced by the code)

Each story is a folder `stories/<slug>/` with:

- `source.txt` - the original English text (Project Gutenberg files are fine: the header,
  footer and licence are stripped automatically and "Project Gutenberg" never appears in videos)
- `story.yaml`:
  ```yaml
  title: The Monkey's Paw
  author: W. W. Jacobs
  year_published: 1902
  author_death_year: 1943
  source_url: https://www.gutenberg.org/ebooks/...
  rights: public_domain          # or: permission
  # permission_proof: permission.png   (required for rights: permission - the email/screenshot)
  # permission_link: https://...       (optional, shown in the description)
  ```

Every command refuses to work on a story unless:
- `rights: public_domain` - published **before 1931** (US: 95 years; the year moves forward every
  January) **and** the author died **more than 50 years ago** (Thailand: life + 50), or
- `rights: permission` - and the `permission_proof` file exists in the story folder.

Every description credits title, author, year, "Public domain" (or "Narrated with the author's
permission" + link), "Translated by Candlelit Tales" (Thai / Chinese) and the source link.

### Commands

```
python run.py story-check the-monkeys-paw              rights check + length / credit estimate
python run.py story-prep  the-monkeys-paw              clean + translate + titles/hooks/trailers (Claude only)
python run.py story-build the-monkeys-paw --lang en    narrate + render English
python run.py story-build the-monkeys-paw              all languages in config
```

1. **story-prep** writes, in the story folder: `clean_en.txt`, `translation_th.txt`,
   `translation_zh.txt` (translated in ~1500-word parts with a running glossary - `glossary_th.json`,
   `glossary_zh.json` - so names stay consistent), `scenes.json` (chapter titles + footage keywords)
   and `prep_<lang>.json` (title, hook, description, tags, 3 trailer scripts). **Read and edit the
   translations before building** - story-build narrates whatever is in those files. Re-running
   prep keeps your edits; `--force` redoes everything.
2. **story-build** shows the characters and ElevenLabs credits for every language and asks before
   paying (`--yes` skips the question). Every narration chunk is cached in
   `stories/<slug>/cache/`, so a failed run continues without paying again, and editing one
   paragraph only re-records that part. It then makes, per language:
   - `output/review/<channel>/<slug>/` - `video.mp4` (1920x1080), `thumbnail.jpg` (1280x720),
     `subtitles.srt`, `meta.json` (description with chapters + credits)
   - `output/review/<channel>/<slug>-trailer-1..3/` - 45 s Shorts with word-by-word captions
   `--no-trailers` builds the long video only.
3. Review and upload as usual (`python run.py review` / `upload`, or Discord). Long videos get
   the custom thumbnail and the `.srt` as a caption track; trailers upload as Shorts.

The video: spoken hook -> title card (channel, title, author) -> the story over slow, darkened
landscape footage chosen per scene with long crossfades -> "Subscribe for more stories" card.
Sentence subtitles are burned in at the bottom (Thai lines break between words using pythainlp,
Chinese between characters, never before punctuation).

**Music:** put royalty-free ambience tracks (YouTube Studio -> Audio Library) in `music/`. One
track is looped and ducked under the voice. With an empty folder the video is built without music
(you get a warning).

**Voices:** put a deep, calm storytelling voice ID for each Candlelit channel in `config.yaml`.

**Sign-in:** the Candlelit channels also upload caption tracks, which needs one extra YouTube
permission, so connect them with `python run.py auth --channel candlelit_en` (and `_th`, `_zh`)
like the others - the existing Shorts channels are not affected.

**YouTube limits for story videos:**
- Videos **longer than 15 minutes** need a **phone-verified** YouTube account
  (youtube.com/verify). Custom thumbnails need it too.
- One upload costs ~1,600 of the 10,000 daily quota units (captions +400, thumbnail +50). A full
  story (3 long videos + 9 trailers) is more than one day's quota - upload it over 2-3 days, or ask
  for more quota in the audit form.

**Tests:** `python -m unittest discover tests` - rights rules, text cleaning, chunking, timing,
SRT and Thai/Chinese line breaking, plus a render test (fake footage/voice, no API keys) that
leaves sample videos in `output/work/render-test/`.

## Files

| Path | What |
|---|---|
| `config.yaml` | channels, style prompts, colors, voice, times, `story:` settings |
| `stories/<slug>/` | story mode: `source.txt`, `story.yaml`, translations, prep files, `cache/` |
| `music/` | your ambience tracks for story videos |
| `tests/` | `python -m unittest discover tests` |
| `output/review` → `approved` → `uploaded` | each video's folder: `video.mp4`, `script.txt`, `meta.json` |
| `uploads.csv` | every upload (paste into your Clip Tracker) |
| `data/errors.log` | details when something fails |
| `data/history.json` | used topics and footage, so videos don't repeat |

Stock footage comes from Pexels, or Pixabay if only `PIXABAY_API_KEY` is set (both free to use,
no credit required). Pixabay has fewer vertical clips, so landscape clips get center-cropped.
