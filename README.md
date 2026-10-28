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

**Stories (Candlelit Tales)** - on the computer with `MACHINE_STORIES=yes` in `.env`, the panel
has a **Stories** button: choose a story and languages (default all), then **Check**, **Prep**
(Claude only, no audio cost), **Build trailers**, **Build long video** or **Build everything**.
Builds post the cost and wait for **Record narration / Cancel** before spending ElevenLabs
credits, report progress every ~15 s, and finish with a review card per video: long videos show
the thumbnail, title, length and the first 60 seconds; trailers look like Shorts cards. Approve /
Reject work the same as for Shorts. Slash commands: `/story-check`, `/story-prep`, `/story-build`.
While a story job runs, the daily Shorts run waits for it instead of being skipped.

Every new video arrives as a card with a preview, the script, and **Approve / Reject**
buttons. Approve uploads it straight away.

Each card also has **Facebook caption** and **Instagram caption** buttons (they stay after
Approve). Tapping one sends you just that caption, privately - long-press it, *Copy Text*, and
paste it into Facebook / Instagram. Claude writes them when the video is made (no spoilers, no
links, AI-voice disclosure, credits for public-domain stories); for older videos they are written
the first time you tap. They are also saved as `social.txt` in the video's folder. Put your
Instagram handles in `config.yaml` -> `social.handles` so captions say "follow @...". Turn on the
platform's AI label when posting.

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
  setting: "a remote English country cottage around 1900, winter nights"   # optional, guides footage
  footage_exclude: [flower, blossom, sunny]   # optional: stock clips tagged like this are skipped
  starts_with: "I."      # optional: drop front matter (e.g. a collection's title page) before this
  ```
  `setting` is given to Claude when it picks stock-footage search terms, so they fit the period.
  `config.yaml` -> `story.footage_exclude` already skips modern things (escalators, offices, cars...).
  Story footage is picked strictly: the stock site's best-ranked results first, and only clips
  whose tags share a real subject word with the search ("door", "candle", "hand"...). Shorts do
  the same with `video.strict_footage: true`; if that finds too few clips for an unusual topic it
  tops up with the normal search.

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
   - `output/review/<channel>/<slug>-trailer-1..3/` - 60-75 s Shorts with word-by-word captions
   `--no-trailers` builds the long video only; `--trailers-only` builds just the 3 Shorts
   trailers (~1,800 credits in English) - a cheap way to see how a story does before paying for
   the full narration. `--trailer 2,3` rebuilds only those trailers. Trailer voices are cached
   too, so re-doing a trailer's visuals is free.
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

## 8. สรุปให้ฟัง - Thai news recaps (timeline Shorts with sources)

Channel `summed_up` (สรุปให้ฟัง, @สรุปให้ฟังOfficial) turns a Thai news story or drama into a
60-90 s timeline Short: a neutral question as the hook, 4-7 timeline cards that slide in as each
beat is spoken (yellow date pill, Kanit text, "แหล่งข่าว: ..." line), then a red **ล่าสุด** card with
where things stand now. Word-by-word Thai captions run in the lower third. Not part of the daily run.

### Safety rules (enforced in code - `pipeline/newslib.py` - and repeated in every Claude prompt)

1. **No monarchy / royal family / Section 112 topics, directly or indirectly.** The topic is checked
   before any research; the research output (sources, timeline, sides, quoted search text) is checked
   again, and so is the script. Any hit blocks the story. **There is no override.** The word list
   is deliberately broad (e.g. "ราชินี" used as a nickname, "palace" in a club name also block) -
   if a safe story is blocked, choose another angle or ask Claude to review the list.
2. **Two credible outlets per fact.** Research only searches the outlets in `config.yaml` ->
   `news.credible_outlets`. A timeline event counts as *confirmed* only when **2 different outlets**
   reported it through URLs the web search actually returned (Claude's own claims are ignored).
   Unconfirmed events are shown to you but never reach the script. Allegations are phrased as
   allegations, each side's response is included when reported, and a second Claude pass reviews
   the script for opinions, verdict-like wording, unsupported claims and guilt-implying hooks.
3. **No private individuals:** stories centred on a non-public person, a minor or a crime victim
   are refused.
4. **No minors' names or faces, no victims' names, no graphic details.**
5. **No news footage or photos of real people:** visuals are our own timeline graphics plus neutral
   stock footage (people/faces/crowds/logos are filtered out).
6. **Every description lists the sources** (outlet, title, date, URL) plus a short disclaimer.

### How it works

```
python run.py news-research "<topic>"      web research -> news/<folder>/research.json + table
python run.py news-approve <folder>        you approve the facts (Discord: Approve facts)
python run.py news-build <folder>          script + safety review + voice + video -> review card
python run.py news-build <folder> --short  also a short version (60-85 s) in review/summed_up/<folder>-short
                                           (upload both: each description links to the other -
                                           "ดูฉบับเต็ม" / "ดูฉบับสั้น". The second upload edits the
                                           first one's description, which needs the channel signed in
                                           with edit permission: run auth --channel summed_up again once)
```

On Discord (computer with `MACHINE_NEWS=yes`): the **News** panel button (or `/news <topic>`) posts
the researched timeline as a
fact-review card (each event with date, confirmed / unconfirmed and source links, plus the safety
check) with **Approve facts / Reject / Edit**. Only after **Approve facts** does it record the voice
and build the video, which then arrives as the usual video review card (Approve uploads).
**Edit timeline** opens a box with the timeline as JSON (or download the attached
`research.json`, edit `timeline`, paste it back); source URLs must come from the original search,
confirmation is re-checked and the facts must be approved again. Blocked stories only offer Reject.

Setup: put a Thai voice ID in `config.yaml` -> `summed_up.voice_id`, run `python run.py fonts`
(Kanit), create the YouTube channel and connect it with `python run.py auth --channel summed_up`.

### Daily topic finder (computer with `MACHINE_NEWS=yes`)

Every day at `news.topics_time` (07:30, change with `/topics-time HH:MM`; `/topics` runs it now)
Claude searches the credible outlets for what Thai people are talking about in the last 24-48 h and
returns 6 candidates (`pipeline/topics.py`). The code then drops - no override - anything about the
monarchy / royal family (flag or any monarchy word), private individuals, minors or crime victims,
and anything without 2 different credible outlets among URLs the search really returned. Topics
covered in the last 14 days (`data/news_topics.json` + the `news/` folders) are skipped unless
there is a major new development (card labelled **อัปเดต**). The best 3 (fresh first, then
ad-friendliness high > medium > low) are posted as cards with **Make Short**, **Make Short + Long**
and **Skip**. Make starts the normal flow: research -> your fact approval -> video -> your video
approval. Each day's run is kept in `data/topics/<date>.json` (cards, dropped + reasons, raw candidates).

### Long recaps (16:9, 5-10 min)

**Make Short + Long** on a topic card (or `news-research "<topic>" --long`) researches deeper
(`news.long_searches`, 12-20 dated events incl. background). After you approve the facts, the bot
builds the Short and then the long recap (`news-build <folder> --long`, `pipeline/newslong.py`):
hook -> background ("เรื่องนี้เริ่มจาก...") -> 8-15 timeline beats -> each side -> what happens
next -> subscribe line, with the same code checks and Claude safety review as the Short. Layout:
the whole timeline as a column on the left (current beat highlighted), a card on the right,
sentence subtitles at the bottom (also uploaded as `subtitles.srt`), darkened neutral stock footage,
an intro title card and a 20 s end screen with room for YouTube's end-screen elements. Thumbnail:
headline + date pill + timeline motif, no photos. The description has chapters (from the beats)
and the full source list. It waits for your Approve like every video.

When the Short and the long recap are both uploaded, each description links to the other. The
YouTube API can't pin comments or set a Short's "related video", so the Discord upload message
gives you the comment text and tells you to set the related video in Studio.

## 9. Popular ideas - Wonder Minute (amazing facts)

Wonder Minute is a normal Shorts channel (one true, amazing fact per video, narrated over stock
footage). Its topics can come from what is popular: the bot searches YouTube for the most-viewed
Shorts of the last `trend_days` days for each of the channel's `trend_queries`, and Claude turns
them into **original** ideas. We never copy another creator's title, script or footage - popular
videos only show which subjects people want.

```
python run.py ideas --channel wonder_minute                 popular Shorts -> numbered ideas (data/ideas_wonder_minute.json)
python run.py generate --channel wonder_minute --idea 3     make idea 3 -> review card as usual
```

On Discord (computer with `MACHINE_IDEAS=yes`): **Popular ideas** posts the list with a
**Make 1..8** button per idea and **New ideas**. Nothing is made until you tap Make, and every video
still needs Approve before upload. Each search uses 100 of YouTube's 10,000 daily quota units
(6 searches per list).

**Auto-upload:** Wonder Minute has `auto_upload: true` - no review card; each video is uploaded
(private) as soon as it is made and Discord gets the link + caption buttons. Any channel can use it
by adding `auto_upload: true` under it in `config.yaml`; remove it to bring the review step back.
News (สรุปให้ฟัง) and story videos always keep their review step.

**Daily from ideas:** with `daily_from_ideas: true` the daily run makes the next idea not made yet
(✅ in the list) and searches for a fresh list when they are used up or a week old.

Setup: create the YouTube channel, then `python run.py auth --channel wonder_minute`. To also make
one Wonder Minute video in the daily run, add `wonder_minute` to `MACHINE_CHANNELS`.

## 10. Channel focus and hooks

Per channel in `config.yaml`:

| Setting | What |
|---|---|
| `enabled: false` | paused: files kept, skipped by the daily run and the Make video picker. Discord `/channel-enable` / `/channel-disable` override it (saved in `data/channel_state.json`). |
| `daily_count: 2` | videos per daily run (global `daily_count` is the default; `0` = manual only) |
| `publish_times: ["12:00", "20:30"]` | this channel's publish slots; `youtube.min_gap_hours` (6) keeps two videos of one channel apart |
| `script_seconds: [35, 45]` | spoken length for this channel (else `video.script_seconds`) |
| `hook_options: true` | the script comes with 3 opening lines (5-10 words, under 2 s, an open question); Claude scores curiosity / clarity and picks one (10 words max, checked in code). The review card lists all 3 with scores and why. |
| `hook_card: true` / `sentence` | big headline for the first ~2 s: a 2-6 word line, or (`sentence`) the spoken hook itself |

Current plan: Mindset Minute paused · Wonder Minute 2/day (12:00, 20:30) · Storytime Cuts 1/day
(18:00, 35-45 s, hook first, ends on the reveal) · สรุปให้ฟัง 1/day from the topic finder (you pick +
approve) · Candlelit manual. Every video waits for **Approve** in Discord before upload.

## 11. Cross-posting and the weekly report

**Cross-post pack (every upload):** after a video is approved and uploaded to YouTube, the bot
writes `output/crosspost/<channel>/<date>_<folder>/` with `video.mp4` and `caption_<lang>.txt`
(a Facebook / Instagram caption, a short TikTok caption with hashtags, and for news a
"แหล่งข่าว: ..." line). The Discord upload message shows the folder. TikTok drafts: section 13.

**Facebook Reels / Instagram / TikTok drafts:** see section 13.

**Weekly report:** every Monday 09:00 (`report:` in config; `/report` in Discord or
`python run.py report` any time) - per channel the last 7 days' videos with views, likes and
comments, subscribers, top 3 / bottom 3 and one suggested change; rows are appended to
`data/weekly_stats.csv`. Average % viewed needs YouTube Analytics: run
`python run.py auth-analytics --channel <key>` once per channel - it saves a separate read-only
token (`tokens/<key>_analytics.json`), so the upload sign-ins are not touched.

## 12. Without API credit - Claude Code does the writing

Two scheduled tasks in the Claude desktop app (Code tab -> Scheduled) write the scripts on the
Pro plan instead of the Anthropic API. The Claude app must be open on this PC.

- **Daily Shorts** (about 02:15): one Storytime Cuts + one Wonder Minute video (Wonder uses the
  saved ideas first). Tap **Pause daily run** in Discord so the bot's own 01:30 API run stops failing.
- **Discord requests** (hourly, 08:00-23:00): tap **Request (Claude Code)**, type what you want
  (e.g. "Wonder Minute: the lightning idea"); **Queue** shows progress. News requests are marked
  "needs session" - ask Claude in the Code tab for those (facts review).
- Videos made this way get normal review cards (the bot posts them from `data/claude_cards.json`);
  nothing is uploaded until you tap Approve.
- **New footage** on a Short's card re-makes it with different clips: same script, voice from the
  cache (no ElevenLabs credits). The original stays if it fails.
- Files: `pipeline/claude_queue.py` (queue + `python -m pipeline.claude_queue list|status|card`),
  `data/claude_queue.json`, `data/claude_scripts/`, `scripts/notify.py`.
- `claude_code.enabled: true` in config.yaml switches the bot over: its own 01:30 run and 07:30 topic
  finder are skipped, and **Make video**, **News**, topic picks and "facts approved" (script writing)
  are queued for Claude Code instead of calling the API.
- **สรุปให้ฟัง without the API** (`pipeline/claude_news.py`): the **topic finder** task (07:30) and the
  queue task get the SAME prompts from the code and pass their work through the SAME checks
  (credible outlets only, 2+ outlets per fact, monarchy / private people / minors refusals, script
  rules and length) before anything is saved. Flow: topic cards -> you pick -> research (queue) ->
  facts card -> you approve -> script (queue) -> the bot builds -> video card -> you approve.
- The queue task runs every 30 min (08:00-23:00).

### Channel plans (Oct analytics)
- **Wonder Minute**: 2 a day, 35-45 s, "contradiction" facts first, the most surprising detail saved
  for the last line, "Tomorrow: ..." teaser outro, "Amazing fact #N" in the description.
- **สรุปให้ฟัง**: Shorts 45-55 s (people left at 20-30 s), key number first; long recaps only for big
  stories; new long thumbnail = 2 lines of huge text + the key number in a big yellow box over a
  blurred frame of the video.
- **Storytime Cuts**: 3-part horror-mystery series (a new chilling clue at ~20 s, a terrifying twist in
  the final part, no gore), one part a night, Full Story built with Part 3 - approve it a week later.
  The series state is `data/storytime_series.json`.

## 13. Facebook Reels and TikTok drafts (after your Approve)

Per channel, opt-in in `config.yaml`:

```yaml
  wonder_minute:
    crosspost:
      facebook: {enabled: true, page_id: "1234567890", mode: publish}   # publish | schedule
      instagram: {enabled: false, ig_id: ""}
      tiktok: {enabled: false, mode: draft}
```

Nothing is posted until you tap **Approve** (same as YouTube). A failed Facebook / TikTok post
never touches the YouTube upload. Each platform is posted once per video (`meta.json`
"platforms"); `--force` posts again. Every post is a row in `uploads.csv` (`Platform`, `URL`).

### Facebook Reels (`pipeline/facebook.py`, Graph API v26.0)
- Reels publishing flow: start -> upload the file -> finish, then waits until Facebook has
  processed it. `is_ai_generated` is set (our voices are AI). `mode: schedule` uses the YouTube
  slot when it is 15 min - 28 days ahead, else posts now.
- Checked before posting: vertical 9:16, at least 540x960, 24-60 fps, **3-90 s** (so the 16:9
  long recaps are skipped with a note), max 30 Reels per Page per 24 h, and the saved token must
  be for exactly this channel's `page_id` (else nothing is posted).
- Errors: temporary Meta / network errors are retried (max 3 tries, 5 s / 15 s backoff).
- News captions include the full source list.

**One-time setup (you do the account steps):**
1. https://developers.facebook.com -> Create app -> use case **"Manage everything on your Page"**
   -> "I don't want to connect a business portfolio yet" -> create. Keep it in **Development**
   mode (no App Review needed while only you, the app admin, use it).
2. In the use case, check the permissions `pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts`. In the Facebook Login settings add the redirect URI
   `http://localhost:8765/fb-callback`.
3. App settings -> Basic: put **App ID** / **App Secret** in `.env` (`META_APP_ID`, `META_APP_SECRET`).
4. Your Facebook account needs full control of each channel's Page.
5. `python run.py fb-auth --channel wonder_minute` - the browser opens, you log in to Facebook
   yourself and allow the Page. Without a `page_id` in config it lists your Pages and their IDs:
   copy the right one into `channels.<key>.crosspost.facebook.page_id` and run it again. The Page
   token is saved to `tokens/facebook_<key>.json` (does not expire; git-ignored).
6. Set `enabled: true`. Test with an old video: `/crosspost` in Discord (channel, `last`) or
   `python run.py crosspost last --channel wonder_minute`.

### TikTok drafts (`pipeline/tiktok.py`, Content Posting API "Upload")
- After Approve, the video is uploaded (chunked FILE_UPLOAD) to the channel's **TikTok inbox as a
  draft**. Discord says "sent to TikTok drafts for <account>" and shows the caption: drafts can't
  carry a caption or an AI label, so paste the caption in TikTok and switch on
  **"AI-generated content"** before you tap Post.
- Checked before sending: the token is for this channel and the same TikTok account (and
  `display_name` from config if you set it), max 5 drafts per account per 24 h (TikTok's limit),
  MP4/H.264, 23-60 fps, <= 10 min. Rate limits / server errors are retried max 3 times.
- Login: `python run.py tiktok-auth --channel <key>` opens TikTok in your browser; you log in with
  that channel's account yourself (PKCE, redirect to `127.0.0.1:8766`). The token renews itself
  (access 24 h, refresh 365 days) and is saved to `tokens/tiktok_<key>.json`.

**One-time setup (developers.tiktok.com - you do these):**
1. Create an app (type **Other**), name KitchanaClipper, platform **Desktop**, Web/Desktop URL,
   Terms and Privacy URLs on the GitHub Pages site (`docs/`), verified via **URL properties ->
   URL prefix** with the signature file in `docs/`.
2. Products: **Login Kit** (Desktop redirect URI `http://127.0.0.1:*/tiktok-callback`) and
   **Content Posting API** (Upload on, Direct Post off). Scopes: `user.info.basic`, `video.upload`.
3. **Sandbox** (clone Production) -> **Target users**: add each channel's TikTok account (log in
   yourself). Sandbox **Credentials** -> `.env`: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`.
4. Sandbox works without review for those target accounts. For production: record a demo video
   of the full flow in sandbox, then **Submit for review** (the scopes must be approved for
   `video.upload`); switch `.env` to the production keys afterwards and run tiktok-auth again.

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
