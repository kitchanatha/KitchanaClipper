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
4. **Fonts**: download from fonts.google.com and put the `.ttf` files in the `fonts/` folder:
   - **Anton** (English captions) → `Anton-Regular.ttf`
   - **Noto Sans Thai** (only if you post in Thai) → set `font: Noto Sans Thai` for that channel in `config.yaml`

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

### Commands (type `/` in the control channel)

| Command | What it does |
|---|---|
| `/status` | Idle or working, paused, next run, videos waiting |
| `/generate` | Make videos now (choose channel and count 1-5) |
| `/pending` | Re-post videos waiting for review |
| `/upload` | Upload all approved videos |
| `/pause` / `/resume` | Stop or restart the daily run |
| `/settime 01:30` | Change the daily run time |

Every new video arrives as a card with a preview, the script, and **Approve / Reject**
buttons. Approve uploads it straight away.

### Keep the computer running 24/7
- **Windows:** Settings → System → Power → Sleep: **Never**. Make it start at boot: press
  Win+R, type `shell:startup`, and put a **shortcut** to `start_bot.bat` in that folder.
- **Mac:** System Settings → Energy → *Prevent automatic sleeping*. Add `start_bot.sh` to
  *Login Items* (or keep a Terminal window open).
- Turn on automatic restart after power failure in your BIOS or Energy settings if available.

## Files

| Path | What |
|---|---|
| `config.yaml` | channels, style prompts, colors, voice, times |
| `output/review` → `approved` → `uploaded` | each video's folder: `video.mp4`, `script.txt`, `meta.json` |
| `uploads.csv` | every upload (paste into your Clip Tracker) |
| `data/errors.log` | details when something fails |
| `data/history.json` | used topics and footage, so videos don't repeat |

Stock footage comes from Pexels, or Pixabay if only `PIXABAY_API_KEY` is set (both free to use,
no credit required). Pixabay has fewer vertical clips, so landscape clips get center-cropped.
