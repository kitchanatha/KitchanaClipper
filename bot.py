"""Discord control bot - runs 24/7, makes videos daily, lets you approve from your phone.

Start:  python bot.py
Each computer runs its own bot (own token, own control channel, own MACHINE_CHANNELS).
"""
import asyncio
import json
import os
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands

import run as core
from pipeline.common import load_config, env, ROOT

cfg = load_config()
TOKEN = env("DISCORD_BOT_TOKEN")
GUILD = discord.Object(id=int(env("DISCORD_SERVER_ID")))
CONTROL = int(env("DISCORD_CONTROL_CHANNEL_ID"))
MACHINE = os.environ.get("MACHINE_NAME", "PC")
MY_CHANNELS = [c.strip() for c in os.environ.get("MACHINE_CHANNELS", "").split(",") if c.strip()]
for c in MY_CHANNELS:
    if c not in cfg["channels"]:
        raise SystemExit(f"MACHINE_CHANNELS has unknown channel '{c}'")
if not MY_CHANNELS:
    raise SystemExit("Set MACHINE_CHANNELS in .env (e.g. mindset_minute)")

# Story mode (Candlelit Tales) runs on the computer with MACHINE_STORIES=yes
STORY_MODE = os.environ.get("MACHINE_STORIES", "").strip().lower() in ("1", "yes", "true", "on")
STORY_KEYS = list(dict.fromkeys(cfg.get("story", {}).get("channels", {}).values())) if STORY_MODE else []
# News recaps (สรุปให้ฟัง) run on the computer with MACHINE_NEWS=yes
NEWS_MODE = os.environ.get("MACHINE_NEWS", "").strip().lower() in ("1", "yes", "true", "on")
NEWS_KEYS = [cfg["news"]["channel"]] if NEWS_MODE and cfg.get("news") else []
# "Popular ideas" (channels with trend_queries) run on the computer with MACHINE_IDEAS=yes
IDEAS_MODE = os.environ.get("MACHINE_IDEAS", "").strip().lower() in ("1", "yes", "true", "on")
IDEA_KEYS = [k for k, c in cfg["channels"].items() if c.get("trend_queries")] if IDEAS_MODE else []
REVIEW_KEYS = list(dict.fromkeys(MY_CHANNELS + STORY_KEYS + NEWS_KEYS + IDEA_KEYS))

STATE = ROOT / "data" / f"bot_state_{MACHINE}.json"
AUTO = cfg.get("automation", {})
MAX_PREVIEW = 9 * 1024 * 1024


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"paused": False, "last_daily": "", "daily_time": AUTO.get("daily_time", "01:30"),
            "last_result": "never run"}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


state = load_state()
job_lock = asyncio.Lock()


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD)
        await self.tree.sync(guild=GUILD)
        self.loop.create_task(daily_loop())


bot = Bot()


async def control():
    return bot.get_channel(CONTROL) or await bot.fetch_channel(CONTROL)


async def say(text):
    ch = await control()
    await ch.send(text[:1990])


def upload_note(slot):
    if cfg["youtube"].get("api_verified", False):
        return f"Uploaded - publishes {slot:%d %b %H:%M}."
    return f"Uploaded as private - schedule it in YouTube Studio for {slot:%d %b %H:%M}."


def only_control(inter):
    return inter.channel_id == CONTROL


# ---------------- review cards ----------------

def make_preview(folder):
    src, out = Path(folder) / "video.mp4", Path(folder) / "preview.mp4"
    if not out.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=540:-2",
                        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "96k", str(out)], check=False)
    return out if out.exists() and out.stat().st_size < MAX_PREVIEW else None


def caption_buttons(view, key, name, row=1):
    view.add_item(discord.ui.Button(label="Facebook caption", emoji="📘", row=row,
                                    style=discord.ButtonStyle.secondary, custom_id=f"fp:fb:{key}/{name}"))
    view.add_item(discord.ui.Button(label="Instagram caption", emoji="📸", row=row,
                                    style=discord.ButtonStyle.secondary, custom_id=f"fp:ig:{key}/{name}"))


class ReviewView(discord.ui.View):
    def __init__(self, key, name):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success,
                                        custom_id=f"fp:a:{key}/{name}"))
        self.add_item(discord.ui.Button(label="Reject", style=discord.ButtonStyle.danger,
                                        custom_id=f"fp:r:{key}/{name}"))
        caption_buttons(self, key, name)


class CaptionView(discord.ui.View):
    """What stays on a card after Approve: the caption buttons for Facebook / Instagram."""

    def __init__(self, key, name):
        super().__init__(timeout=None)
        caption_buttons(self, key, name, row=0)


def find_video(key, name):
    for state in ("review", "approved", "uploaded", "rejected"):
        p = Path(cfg["output_dir"]) / state / key / name
        if (p / "meta.json").exists():
            return p
    return None


async def send_caption(inter, platform, key, name):
    folder = find_video(key, name)
    if not folder:
        return await inter.response.send_message("Can't find that video any more.", ephemeral=True)
    await inter.response.defer(ephemeral=True, thinking=True)
    from pipeline.social import ensure_social
    try:
        social = await asyncio.to_thread(ensure_social, cfg, key, folder)
    except Exception as e:
        return await inter.followup.send(f"Couldn't write captions: {str(e)[:300]}", ephemeral=True)
    # just the caption, so long-press -> Copy Text copies exactly what to paste
    await inter.followup.send(social["facebook" if platform == "fb" else "instagram"][:1990],
                              ephemeral=True)


def make_story_preview(folder):
    from pipeline.longvideo import preview_clip
    out = Path(folder) / "preview.mp4"
    if not out.exists():
        try:
            preview_clip(Path(folder) / "video.mp4", out, 60)
        except Exception:
            return None
    return out if out.exists() and out.stat().st_size < MAX_PREVIEW else None


def mmss(sec):
    m, s = divmod(int(sec), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m}:{s:02d}"


async def post_story_card(folder, meta, key):
    emb = discord.Embed(title=meta["title"][:250], description=meta["description"][:1800],
                        color=0xF2A541)
    emb.set_author(name=f"{meta['channel_name']}  ·  long video  ·  {MACHINE}")
    emb.set_footer(text=f"{mmss(meta['length_sec'])} long  ·  {meta.get('lang', '')}  ·  "
                        f"{folder.name}  ·  preview = first 60 s")
    files = []
    thumb = folder / meta.get("thumbnail", "thumbnail.jpg")
    if thumb.exists():
        files.append(discord.File(str(thumb), filename="thumbnail.jpg"))
        emb.set_image(url="attachment://thumbnail.jpg")
    preview = await asyncio.to_thread(make_story_preview, folder)
    if preview:
        files.append(discord.File(str(preview), filename="preview_first_60s.mp4"))
    else:
        emb.add_field(name="Preview", value="Could not make a preview - check the review folder.")
    ch = await control()
    await ch.send(embed=emb, files=files, view=ReviewView(key, folder.name))


async def post_card(folder):
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    key = folder.parent.name
    if meta.get("kind") == "story":
        return await post_story_card(folder, meta, key)
    emb = discord.Embed(title=meta["title"][:250], description=meta["script"][:3900],
                        color=0x3D1E6D)
    emb.set_author(name=f"{meta['channel_name']}  ·  {MACHINE}")
    emb.set_footer(text=f"{meta['length_sec']}s  ·  {folder.name}")
    preview = await asyncio.to_thread(make_preview, folder)
    ch = await control()
    kwargs = {"embed": emb, "view": ReviewView(key, folder.name)}
    if preview:
        kwargs["file"] = discord.File(str(preview), filename="preview.mp4")
    else:
        emb.add_field(name="Preview", value="Too large for Discord - check the review folder.")
    await ch.send(**kwargs)


@bot.event
async def on_interaction(inter: discord.Interaction):
    cid = (inter.data or {}).get("custom_id", "") if inter.type == discord.InteractionType.component else ""
    if cid.startswith("cp:"):
        return await handle_panel(inter, cid)
    if cid.startswith("nf:"):
        return await handle_news_card(inter, cid)
    if cid.startswith("id:"):
        return await handle_idea(inter, cid)
    if not cid.startswith("fp:"):
        return
    _, action, rest = cid.split(":", 2)
    key, name = rest.split("/", 1)
    if action in ("fb", "ig"):
        return await send_caption(inter, action, key, name)
    folder = Path(cfg["output_dir"]) / "review" / key / name
    if not folder.exists():
        await inter.response.send_message("Already handled.", ephemeral=True)
        return
    await inter.response.defer()
    approve = action == "a"
    dest = await asyncio.to_thread(core.decide, cfg, folder, approve)
    note = f"Approved by {inter.user.display_name}" if approve else f"Rejected by {inter.user.display_name}"
    msg = inter.message  # edited directly: a long video's upload can outlast the interaction
    keep = CaptionView(key, dest.name) if approve else None  # captions stay for posting elsewhere
    if approve and AUTO.get("upload_after_approve", True):
        await msg.edit(view=keep, content=note + " - uploading...")
        try:
            url, slot = await asyncio.to_thread(core.upload_one, cfg, key, dest)
            note += f"\n{upload_note(slot)}\n{url}"
            note += upload_warnings(key, dest.name)
        except BaseException as e:  # SystemExit from missing auth too
            note += f"\nUpload failed: {e}\nFix it, then tap Upload approved."
    await msg.edit(view=keep, content=note[:1990])


def upload_warnings(key, name):
    meta_f = Path(cfg["output_dir"]) / "uploaded" / key / name / "meta.json"
    try:
        warns = json.loads(meta_f.read_text(encoding="utf-8")).get("upload_warnings", [])
    except (OSError, ValueError):
        return ""
    return "".join(f"\n⚠️ {w}" for w in warns)


# ---------------- jobs ----------------

async def run_generate(keys, count, reason, wait=False, idea=None):
    if job_lock.locked():
        if not wait:
            await say("A job is already running - try again when it finishes.")
            return
        await say(f"**{MACHINE}**: {reason} is waiting for the running job to finish...")
    async with job_lock:
        await say(f"**{MACHINE}**: {reason} - making {count} video(s) for {', '.join(keys)}...")
        made, failed = 0, 0
        for key in keys:
            for _ in range(count):
                try:
                    folder = await asyncio.to_thread(core.generate_one, cfg, key, idea=idea)
                    made += 1
                    if cfg["channels"][key].get("auto_upload"):  # no review for this channel
                        try:
                            url, slot = await asyncio.to_thread(core.auto_upload, cfg, key, folder)
                            m = json.loads((find_video(key, folder.name) / "meta.json").read_text(
                                encoding="utf-8"))
                            await (await control()).send(
                                f"**{cfg['channels'][key]['name']}** auto-uploaded: **{m['title']}**\n"
                                f"{upload_note(slot)}\n{url}", view=CaptionView(key, folder.name))
                        except BaseException as e:
                            await say(f"[{key}] made but upload failed: `{str(e)[:300]}` - "
                                      "it is in approved/, tap Upload approved to retry.")
                    else:
                        await post_card(folder)
                except BaseException as e:
                    failed += 1
                    await say(f"[{key}] failed: `{str(e)[:400]}`")
                    with open(ROOT / "data" / "errors.log", "a", encoding="utf-8") as f:
                        f.write(f"\n--- {datetime.now()} {key}\n{traceback.format_exc()}")
        state["last_result"] = f"{datetime.now():%d %b %H:%M}: {made} made, {failed} failed"
        save_state(state)
        await say(f"**{MACHINE}** done: {made} ready for review, {failed} failed.")
        await post_panel()  # keep the buttons at the bottom of the channel


async def daily_loop():
    await bot.wait_until_ready()
    await say(f"**{MACHINE}** online. Channels: {', '.join(MY_CHANNELS)}. "
              f"Daily run at {state['daily_time']}{' (PAUSED)' if state['paused'] else ''}.")
    await post_panel()
    while not bot.is_closed():
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if (not state["paused"] and state["last_daily"] != today
                and now.strftime("%H:%M") >= state["daily_time"]):
            state["last_daily"] = today
            save_state(state)
            await run_generate(MY_CHANNELS, int(cfg.get("daily_count", 1)), "daily run", wait=True)
        await asyncio.sleep(30)


def counts():
    base = Path(cfg["output_dir"])
    out = []
    for k in REVIEW_KEYS:
        n = {s: len([p for p in (base / s / k).glob("*") if p.is_dir()])
             for s in ("review", "approved", "uploaded")}
        out.append(f"{cfg['channels'][k]['name']}: {n['review']} to review, "
                   f"{n['approved']} approved, {n['uploaded']} uploaded")
    return "\n".join(out)


# ---------------- actions (used by buttons and slash commands) ----------------

def status_text():
    busy = "working on a job" if job_lock.locked() else "idle"
    return (f"**{MACHINE}** is {busy}{' · PAUSED' if state['paused'] else ''}\n"
            f"Daily run: {state['daily_time']} · Last: {state['last_result']}\n{counts()}")


async def do_generate(inter, keys, count=1):
    count = max(1, min(count, 5))
    if job_lock.locked():
        return await inter.response.send_message("A job is already running - wait for it to finish.",
                                                 ephemeral=True)
    await inter.response.send_message(f"Starting {count} per channel...")
    bot.loop.create_task(run_generate(keys, count, f"manual run by {inter.user.display_name}"))


# ---------------- popular ideas ----------------

def ideas_view(key, n):
    """Persistent buttons under an ideas list: one per idea + 'New ideas'."""
    v = discord.ui.View(timeout=None)
    for i in range(1, n + 1):
        v.add_item(discord.ui.Button(label=f"Make {i}", custom_id=f"id:{key}:{i}",
                                     style=discord.ButtonStyle.primary, row=(i - 1) // 5))
    v.add_item(discord.ui.Button(label="New ideas", custom_id=f"id:{key}:new",
                                 style=discord.ButtonStyle.secondary, row=(n - 1) // 5 + 1))
    return v


async def do_ideas(inter, key):
    """Search the most-viewed recent Shorts and post numbered original ideas (costs one small
    Claude call + YouTube search quota; nothing is made until a 'Make' button is tapped)."""
    from pipeline.trends import find_ideas, ideas_text
    if job_lock.locked():
        return await inter.response.send_message("A job is already running - wait for it to finish.",
                                                 ephemeral=True)
    await inter.response.send_message(f"Looking at popular {cfg['channels'][key]['name']} Shorts...")
    async with job_lock:
        try:
            data = await asyncio.to_thread(find_ideas, cfg, key, 8, lambda *_: None)
        except BaseException as e:
            return await say(f"Popular ideas failed: `{str(e)[:400]}`")
    ch = await control()
    await ch.send(f"**{cfg['channels'][key]['name']}** - " + ideas_text(data),
                  view=ideas_view(key, len(data["ideas"])))
    await post_panel()


async def handle_idea(inter, cid):
    from pipeline.trends import load_ideas
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    _, key, what = cid.split(":", 2)
    if key not in IDEA_KEYS:
        return await inter.response.send_message("Popular ideas are off on this computer.",
                                                 ephemeral=True)
    if what == "new":
        return await do_ideas(inter, key)
    data = load_ideas(key)
    n = int(what)
    if not data or n > len(data["ideas"]):
        return await inter.response.send_message("That list is gone - tap New ideas.", ephemeral=True)
    if job_lock.locked():
        return await inter.response.send_message("A job is already running - wait for it to finish.",
                                                 ephemeral=True)
    idea = data["ideas"][n - 1]
    await inter.response.send_message(f"Making idea {n}: {idea['idea'][:150]}")
    bot.loop.create_task(run_generate([key], 1, f"idea {n} by {inter.user.display_name}", idea=idea))


async def do_pending(inter):
    base = Path(cfg["output_dir"]) / "review"
    items = [p for k in REVIEW_KEYS for p in sorted((base / k).glob("*")) if (p / "video.mp4").exists()]
    await inter.response.send_message(f"{len(items)} waiting for review.")
    for p in items:
        await post_card(p)


async def do_upload(inter):
    await inter.response.defer()
    base = Path(cfg["output_dir"]) / "approved"
    lines = []
    for k in REVIEW_KEYS:
        for p in sorted((base / k).glob("*")):
            if not (p / "video.mp4").exists():
                continue
            try:
                url, slot = await asyncio.to_thread(core.upload_one, cfg, k, p)
                lines.append(f"✅ {upload_note(slot)} {url}" + upload_warnings(k, p.name))
            except BaseException as e:
                lines.append(f"❌ {p.name}: {str(e)[:200]}")
    await inter.followup.send("\n".join(lines) or "Nothing approved to upload.")


async def do_pause(inter, paused):
    state["paused"] = paused
    save_state(state)
    await inter.response.send_message(
        f"**{MACHINE}** paused." if paused
        else f"**{MACHINE}** resumed. Next run at {state['daily_time']}.")


async def do_settime(inter, time):
    try:
        t = datetime.strptime(time.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        return await inter.response.send_message("Use HH:MM, e.g. 01:30", ephemeral=True)
    state["daily_time"] = t
    save_state(state)
    await inter.response.send_message(f"Daily run on **{MACHINE}** set to {t}.")


# ---------------- control panel (buttons) ----------------

def panel_view():
    v = discord.ui.View(timeout=None)
    for label, cid, style, row in [
        ("Status", "cp:status", discord.ButtonStyle.secondary, 0),
        ("Make video", "cp:gen", discord.ButtonStyle.primary, 0),
        ("Show pending", "cp:pending", discord.ButtonStyle.secondary, 0),
        ("Upload approved", "cp:upload", discord.ButtonStyle.success, 0),
        ("Pause daily run", "cp:pause", discord.ButtonStyle.secondary, 1),
        ("Resume daily run", "cp:resume", discord.ButtonStyle.secondary, 1),
        ("Set daily time", "cp:settime", discord.ButtonStyle.secondary, 1),
    ] + ([("Stories", "cp:stories", discord.ButtonStyle.primary, 1)] if STORY_MODE else []) \
      + ([("News", "cp:news", discord.ButtonStyle.primary, 1)] if NEWS_MODE else []) \
      + ([("Popular ideas", "cp:ideas", discord.ButtonStyle.primary, 2)] if IDEA_KEYS else []):
        v.add_item(discord.ui.Button(label=label, custom_id=cid, style=style, row=row))
    return v


def channel_picker_view():
    v = discord.ui.View(timeout=300)
    for k in MY_CHANNELS:
        v.add_item(discord.ui.Button(label=cfg["channels"][k]["name"], custom_id=f"cp:gen:{k}",
                                     style=discord.ButtonStyle.primary))
    if len(MY_CHANNELS) > 1:
        v.add_item(discord.ui.Button(label="All my channels", custom_id="cp:gen:all",
                                     style=discord.ButtonStyle.secondary))
    return v


class TimeModal(discord.ui.Modal, title="Daily run time"):
    def __init__(self):
        super().__init__()
        self.time = discord.ui.TextInput(label="Time on this computer (24h, HH:MM)",
                                         default=state["daily_time"], max_length=5)
        self.add_item(self.time)

    async def on_submit(self, inter: discord.Interaction):
        await do_settime(inter, self.time.value)


async def post_panel():
    """Post the button panel at the bottom of the control channel (replacing the old one)."""
    ch = await control()
    old = state.get("panel_msg")
    if old:
        try:
            await (await ch.fetch_message(old)).delete()
        except discord.HTTPException:
            pass
    msg = await ch.send(f"**{MACHINE} control panel**", view=panel_view())
    state["panel_msg"] = msg.id
    save_state(state)


async def handle_panel(inter, cid):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    if cid == "cp:status":
        await inter.response.send_message(status_text())
    elif cid == "cp:gen":
        await inter.response.send_message("Make one video for:", view=channel_picker_view(),
                                          ephemeral=True)
    elif cid.startswith("cp:gen:"):
        key = cid.split(":", 2)[2]
        if key != "all" and key not in MY_CHANNELS:
            return await inter.response.send_message("Unknown channel.", ephemeral=True)
        await do_generate(inter, MY_CHANNELS if key == "all" else [key])
    elif cid == "cp:pending":
        await do_pending(inter)
    elif cid == "cp:upload":
        await do_upload(inter)
    elif cid in ("cp:pause", "cp:resume"):
        await do_pause(inter, cid == "cp:pause")
    elif cid == "cp:settime":
        await inter.response.send_modal(TimeModal())
    elif cid == "cp:stories":
        await open_stories(inter)
    elif cid == "cp:ideas" or cid.startswith("cp:ideas:"):
        if not IDEA_KEYS:
            return await inter.response.send_message("Popular ideas are off on this computer.",
                                                     ephemeral=True)
        if cid == "cp:ideas" and len(IDEA_KEYS) > 1:
            v = discord.ui.View(timeout=300)
            for k in IDEA_KEYS:
                v.add_item(discord.ui.Button(label=cfg["channels"][k]["name"], custom_id=f"cp:ideas:{k}",
                                             style=discord.ButtonStyle.primary))
            return await inter.response.send_message("Popular ideas for:", view=v, ephemeral=True)
        key = cid.split(":", 2)[2] if cid.count(":") == 2 else IDEA_KEYS[0]
        if key not in IDEA_KEYS:
            return await inter.response.send_message("Unknown channel.", ephemeral=True)
        await do_ideas(inter, key)
    elif cid == "cp:news":
        if not NEWS_MODE:
            return await inter.response.send_message("News mode is off on this computer.", ephemeral=True)
        await inter.response.send_modal(NewsTopicModal())


# ---------------- story mode (Candlelit Tales) ----------------

def story_slugs():
    from pipeline.story import stories_root
    root = stories_root(cfg)
    return sorted(p.name for p in root.iterdir() if (p / "story.yaml").exists()) if root.exists() else []


class Progress:
    """Collects log lines from a worker thread and posts them to Discord every ~15 s,
    so long jobs show progress without flooding the channel."""

    STAGE = ("[", "Footage", "Narration", "WARNING", "Rights", "Source", "Prep done", "Cancelled")

    def __init__(self, loop):
        self.loop, self.buf, self.lock, self.last = loop, [], threading.Lock(), 0.0

    def log(self, *args):
        line = " ".join(str(a) for a in args)
        print(line)
        with self.lock:
            self.buf.append(line)
        now = time.time()
        if now - self.last > 15 or (line.startswith(self.STAGE) and now - self.last > 4):
            self.last = now
            asyncio.run_coroutine_threadsafe(self.flush(), self.loop)

    async def flush(self):
        with self.lock:
            lines, self.buf = self.buf, []
        if lines:
            await say("```\n" + "\n".join(lines)[-1900:] + "\n```")


class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=3600)
        self.ok = None

    @discord.ui.button(label="Record narration", style=discord.ButtonStyle.success)
    async def yes(self, inter: discord.Interaction, button: discord.ui.Button):
        self.ok = True
        await inter.response.edit_message(view=None, content=f"Recording - confirmed by {inter.user.display_name}.")
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, inter: discord.Interaction, button: discord.ui.Button):
        self.ok = False
        await inter.response.edit_message(view=None, content=f"Cancelled by {inter.user.display_name} - nothing recorded.")
        self.stop()


async def ask_confirm(summary):
    view = ConfirmView()
    msg = await (await control()).send(f"```\n{summary[-1700:]}\n```\nRecord this narration?", view=view)
    await view.wait()
    if view.ok is None:
        await msg.edit(view=None, content="No answer within an hour - cancelled, nothing recorded.")
    return bool(view.ok)


def confirm_from_thread(summary):
    return asyncio.run_coroutine_threadsafe(ask_confirm(summary), bot.loop).result(timeout=3700)


async def run_story(action, slug, langs, mode, user):
    """action: check | prep | build.  mode (build): full | trailers | long."""
    from pipeline import story, storybuild
    if job_lock.locked():
        return await say("A job is already running - try again when it finishes.")
    async with job_lock:
        what = {"check": "check", "prep": "prep (Claude only, no audio cost)",
                "build": {"full": "build", "trailers": "build trailers", "long": "build long video"}[mode]}[action]
        await say(f"**{MACHINE}**: story {what} - `{slug}` ({langs or 'all languages'}) - started by {user}")
        prog = Progress(bot.loop)
        try:
            if action == "check":
                await asyncio.to_thread(story.check, cfg, slug, prog.log)
            elif action == "prep":
                await asyncio.to_thread(story.prep, cfg, slug, langs, False, prog.log)
            else:
                made = await asyncio.to_thread(
                    storybuild.build, cfg, slug, langs, False, confirm_from_thread, prog.log, False,
                    mode != "long", mode != "trailers")
                await prog.flush()
                for group in made or []:
                    for folder in group:
                        await post_card(folder)
                if made:
                    await say(f"**{MACHINE}**: story build done - review the cards above.")
        except SystemExit as e:  # rights refusal, missing prep, missing voice id ...
            await prog.flush()
            await say(f"❌ {e}")
        except BaseException as e:
            await prog.flush()
            await say(f"❌ story {action} failed: `{str(e)[:400]}`")
            with open(ROOT / "data" / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now()} story {action} {slug}\n{traceback.format_exc()}")
        else:
            await prog.flush()
    await post_panel()


class StoryView(discord.ui.View):
    """Stories button: pick a story + languages, then Check / Prep / Build."""

    def __init__(self):
        super().__init__(timeout=900)
        self.slug, self.langs = None, None
        slugs = story_slugs()[:25]
        pick = discord.ui.Select(placeholder="Choose a story", options=[
            discord.SelectOption(label=s, value=s) for s in slugs] or [
            discord.SelectOption(label="(no stories yet)", value="-")])
        pick.callback = self.on_story
        self.add_item(pick)
        langs = list(cfg["story"]["channels"])
        lang = discord.ui.Select(placeholder="Languages: all", min_values=1, max_values=len(langs),
                                 options=[discord.SelectOption(label=l, value=l) for l in langs])
        lang.callback = self.on_lang
        self.add_item(lang)
        for label, action, mode, style in [
            ("Check", "check", None, discord.ButtonStyle.secondary),
            ("Prep (no audio cost)", "prep", None, discord.ButtonStyle.secondary),
            ("Build trailers", "build", "trailers", discord.ButtonStyle.primary),
            ("Build long video", "build", "long", discord.ButtonStyle.primary),
            ("Build everything", "build", "full", discord.ButtonStyle.success),
        ]:
            b = discord.ui.Button(label=label, style=style, row=2 if action == "build" else 3)
            b.callback = self.make_action(action, mode)
            self.add_item(b)

    async def on_story(self, inter):
        self.slug = inter.data["values"][0]
        await inter.response.defer()

    async def on_lang(self, inter):
        self.langs = ",".join(inter.data["values"])
        await inter.response.defer()

    def make_action(self, action, mode):
        async def go(inter):
            if not self.slug or self.slug == "-":
                return await inter.response.send_message("Choose a story first.", ephemeral=True)
            await inter.response.send_message(f"Starting {action} for `{self.slug}`...", ephemeral=True)
            bot.loop.create_task(run_story(action, self.slug, self.langs, mode or "full",
                                           inter.user.display_name))
        return go


async def open_stories(inter):
    if not STORY_MODE:
        return await inter.response.send_message("Story mode is off on this computer.", ephemeral=True)
    await inter.response.send_message("**Stories** - choose a story and languages (default: all), "
                                      "then an action. Builds ask before paying for narration.",
                                      view=StoryView(), ephemeral=True)


async def slug_autocomplete(inter: discord.Interaction, current: str):
    return [app_commands.Choice(name=s, value=s) for s in story_slugs() if current.lower() in s][:25]


LANG_CHOICES = [app_commands.Choice(name="all languages", value="all")] + [
    app_commands.Choice(name=l, value=l) for l in cfg.get("story", {}).get("channels", {})]


def story_langs_arg(languages):
    return None if not languages or languages.value == "all" else languages.value


# ---------------- news recaps (สรุปให้ฟัง) ----------------

def news_link(url, outlet):
    return f"[{outlet}]({url})"


def fact_embeds(data):
    """The researched timeline as Discord embeds (split to stay under Discord's size limits)."""
    from pipeline.newslib import domain_of, outlet_for, thai_date
    credible = cfg["news"]["credible_outlets"]
    sc = data.get("safety_check") or {}
    problems = data.get("safety_problems") or []
    head = discord.Embed(
        title=("⛔ BLOCKED - " if problems else "ตรวจข้อเท็จจริง: ") + str(data.get("topic", ""))[:200],
        color=0xE63946 if problems else 0xFFD60A,
        description=(
            f"**Safety check** - monarchy: {'YES' if sc.get('monarchy') else 'no'} · "
            f"private person: {'YES' if sc.get('private_person') else 'no'} · "
            f"minor / victim: {'YES' if sc.get('minor_or_victim') else 'no'}\n"
            f"{str(sc.get('notes', ''))[:600]}\n"
            + ("".join(f"\n⛔ **{x}**" for x in problems))
            + f"\n\n✅ confirmed = 2+ different outlets (only these can be used) · ⚠️ = unconfirmed, left out")[:4000])
    embeds, cur, size = [head], None, 0
    for e in data.get("timeline", []):
        when = thai_date(e.get("date"), True) + (f" {e['time_if_known']}" if e.get("time_if_known") else "")
        name = f"{'✅' if e.get('confirmed') else '⚠️'} [{e.get('id')}] {when}"
        urls = e.get("source_urls") or []
        srcs = " · ".join(news_link(u, outlet_for(u, credible) or domain_of(u)) for u in urls) or "-"
        value = (str(e.get("event_th", ""))[:700] + "\n" + srcs)[:1024]
        if cur is None or len(cur.fields) >= 8 or size + len(name) + len(value) > 5000:
            cur, size = discord.Embed(color=0x0E2A47), 0
            embeds.append(cur)
        cur.add_field(name=name, value=value, inline=False)
        size += len(name) + len(value)
    tail = discord.Embed(color=0x0E2A47, title="Sides & where it stands")
    for sd in data.get("sides", [])[:8]:
        tail.add_field(name=f"{'✅' if sd.get('confirmed') else '⚠️'} {str(sd.get('party', ''))[:200]}",
                       value=str(sd.get("position_th", ""))[:1000] or "-", inline=False)
    tail.add_field(name=("✅" if data.get("status_confirmed") else "⚠️") + " ล่าสุด",
                   value=str(data.get("status_now_th", ""))[:1000] or "-", inline=False)
    embeds.append(tail)
    return embeds


class FactView(discord.ui.View):
    def __init__(self, slug, blocked=False):
        super().__init__(timeout=None)
        if not blocked:
            self.add_item(discord.ui.Button(label="Approve facts", style=discord.ButtonStyle.success,
                                            custom_id=f"nf:a:{slug}"))
        self.add_item(discord.ui.Button(label="Reject", style=discord.ButtonStyle.danger,
                                        custom_id=f"nf:r:{slug}"))
        if not blocked:
            self.add_item(discord.ui.Button(label="Edit timeline", style=discord.ButtonStyle.secondary,
                                            custom_id=f"nf:e:{slug}"))


async def post_fact_card(slug, data):
    """Timeline review: every event with date, confirmed / unconfirmed and source links, the
    safety check, and the full research.json attached (download it to edit)."""
    from pipeline import news
    ch = await control()
    embeds = fact_embeds(data)
    for emb in embeds[:-1]:  # one embed per message: Discord caps a message at 6,000 embed characters
        await ch.send(embed=emb)
    path = news.news_dir(cfg, slug) / "research.json"
    await ch.send(content=f"**{slug}** - check the facts above. Nothing is recorded until you tap "
                          f"**Approve facts**.", embed=embeds[-1],
                  file=discord.File(str(path), filename=f"{slug}-research.json"),
                  view=FactView(slug, blocked=bool(data.get("safety_problems"))))


class NewsTopicModal(discord.ui.Modal, title="New สรุปให้ฟัง topic"):
    topic = discord.ui.TextInput(label="Topic (Thai or English)", max_length=200,
                                 placeholder="e.g. ข้อถกเถียงร่างแก้ไขกฎหมายไทยพีบีเอส")

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.send_message(f"Researching: {self.topic.value}", ephemeral=True)
        bot.loop.create_task(run_news_research(self.topic.value, inter.user.display_name))


class TimelineEditModal(discord.ui.Modal, title="Edit the timeline"):
    def __init__(self, slug, current):
        super().__init__()
        self.slug = slug
        self.box = discord.ui.TextInput(
            label="Timeline JSON (date, time_if_known, event_th, source_urls)",
            style=discord.TextStyle.paragraph, max_length=4000, required=True,
            default=current if len(current) <= 4000 else None,
            placeholder="Too long to show here - download the research.json above, edit "
                        "'timeline', paste it here")
        self.add_item(self.box)

    async def on_submit(self, inter: discord.Interaction):
        from pipeline import news
        try:
            new = json.loads(self.box.value)
        except json.JSONDecodeError as e:
            return await inter.response.send_message(f"That isn't valid JSON: {e}", ephemeral=True)
        await inter.response.send_message("Timeline updated - re-checking sources...", ephemeral=True)
        data = await asyncio.to_thread(news.replace_timeline, cfg, self.slug, new)
        await say(f"**{self.slug}**: timeline edited by {inter.user.display_name} - confirmation "
                  f"re-checked against the searched sources. Approve the facts again:")
        await post_fact_card(self.slug, data)


async def run_news_research(topic, user):
    from pipeline import news
    if job_lock.locked():
        return await say("A job is already running - try again when it finishes.")
    async with job_lock:
        await say(f"**{MACHINE}**: researching news topic for {user}: `{topic}` (web search only, no voice)")
        prog = Progress(bot.loop)
        try:
            slug, data = await asyncio.to_thread(news.research, cfg, topic, prog.log)
            await prog.flush()
            await post_fact_card(slug, data)
        except SystemExit as e:  # monarchy refusal etc.
            await prog.flush()
            await say(f"⛔ {e}")
        except BaseException as e:
            await prog.flush()
            await say(f"❌ news research failed: `{str(e)[:400]}`")
            with open(ROOT / "data" / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now()} news research {topic}\n{traceback.format_exc()}")
    await post_panel()


async def run_news_build(slug, user):
    from pipeline import news
    async with job_lock:  # waits if another job runs
        await say(f"**{MACHINE}**: facts approved by {user} - writing the script (+ safety review), "
                  f"recording the voice and building `{slug}`...")
        prog = Progress(bot.loop)
        try:
            out = await asyncio.to_thread(news.build, cfg, slug, False, prog.log)
            await prog.flush()
            await post_card(out)
        except SystemExit as e:
            await prog.flush()
            await say(f"⛔ {slug}: {e}")
        except BaseException as e:
            await prog.flush()
            await say(f"❌ {slug} build failed: `{str(e)[:400]}`")
            with open(ROOT / "data" / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now()} news build {slug}\n{traceback.format_exc()}")
    await post_panel()


async def handle_news_card(inter, cid):
    from pipeline import news
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    _, action, slug = cid.split(":", 2)
    try:
        path = news.news_dir(cfg, slug) / "research.json"
    except SystemExit:
        return await inter.response.send_message("That research no longer exists.", ephemeral=True)
    data = news.read_json(path)
    who = inter.user.display_name
    if action == "e":
        timeline = [{k: e.get(k) for k in ("date", "time_if_known", "event_th", "source_urls")}
                    for e in data.get("timeline", [])]
        return await inter.response.send_modal(
            TimelineEditModal(slug, json.dumps(timeline, ensure_ascii=False, indent=1)))
    if action == "r":
        data["rejected_by"] = who
        news.write_json(path, data)
        return await inter.response.edit_message(view=None, content=f"**{slug}** - facts rejected by {who}.")
    # approve facts
    try:
        await asyncio.to_thread(news.approve_facts, cfg, slug, who)
    except SystemExit as e:
        return await inter.response.send_message(f"⛔ Can't approve: {e}", ephemeral=True)
    await inter.response.edit_message(view=None, content=f"**{slug}** - facts approved by {who}. Building the video...")
    bot.loop.create_task(run_news_build(slug, who))


# ---------------- slash commands (backup for the buttons) ----------------

CHOICES = [app_commands.Choice(name="all my channels", value="all")] + [
    app_commands.Choice(name=cfg["channels"][k]["name"], value=k) for k in MY_CHANNELS]


async def guarded(inter, fn, *args):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    await fn(inter, *args)


@bot.tree.command(description="Show this computer's status")
async def status(inter: discord.Interaction):
    await guarded(inter, lambda i: i.response.send_message(status_text()))


@bot.tree.command(description="Make videos now")
@app_commands.choices(channel=CHOICES)
@app_commands.describe(count="videos per channel (1-5)")
async def generate(inter: discord.Interaction, channel: app_commands.Choice[str], count: int = 1):
    keys = MY_CHANNELS if channel.value == "all" else [channel.value]
    await guarded(inter, do_generate, keys, count)


@bot.tree.command(description="Re-post videos waiting for review")
async def pending(inter: discord.Interaction):
    await guarded(inter, do_pending)


@bot.tree.command(description="Upload all approved videos now")
async def upload(inter: discord.Interaction):
    await guarded(inter, do_upload)


@bot.tree.command(description="Pause the daily run")
async def pause(inter: discord.Interaction):
    await guarded(inter, do_pause, True)


@bot.tree.command(description="Resume the daily run")
async def resume(inter: discord.Interaction):
    await guarded(inter, do_pause, False)


@bot.tree.command(description="Change the daily run time (24h, e.g. 01:30)")
async def settime(inter: discord.Interaction, time: str):
    await guarded(inter, do_settime, time)


async def _post_panel_reply(inter):
    await inter.response.send_message("Panel posted below.", ephemeral=True)
    await post_panel()


@bot.tree.command(description="Show the button control panel")
async def panel(inter: discord.Interaction):
    await guarded(inter, _post_panel_reply)


if STORY_MODE:
    async def _story_cmd(inter, action, slug, languages, mode="full"):
        if not only_control(inter):
            return await inter.response.send_message("Use the control channel.", ephemeral=True)
        await inter.response.send_message(f"Starting story {action} for `{slug}`...")
        bot.loop.create_task(run_story(action, slug, story_langs_arg(languages), mode,
                                       inter.user.display_name))

    @bot.tree.command(name="story-check", description="Check a story's rights and length / credit estimate")
    @app_commands.autocomplete(slug=slug_autocomplete)
    async def story_check_cmd(inter: discord.Interaction, slug: str):
        await _story_cmd(inter, "check", slug, None)

    @bot.tree.command(name="story-prep", description="Clean + translate + titles/trailer scripts (no audio cost)")
    @app_commands.autocomplete(slug=slug_autocomplete)
    @app_commands.choices(languages=LANG_CHOICES)
    async def story_prep_cmd(inter: discord.Interaction, slug: str,
                             languages: app_commands.Choice[str] = None):
        await _story_cmd(inter, "prep", slug, languages)

    @bot.tree.command(name="story-build", description="Narrate + render (asks before paying)")
    @app_commands.autocomplete(slug=slug_autocomplete)
    @app_commands.choices(languages=LANG_CHOICES, what=[
        app_commands.Choice(name="everything (long video + trailers)", value="full"),
        app_commands.Choice(name="trailers only", value="trailers"),
        app_commands.Choice(name="long video only", value="long")])
    async def story_build_cmd(inter: discord.Interaction, slug: str,
                              languages: app_commands.Choice[str] = None,
                              what: app_commands.Choice[str] = None):
        await _story_cmd(inter, "build", slug, languages, what.value if what else "full")


if NEWS_MODE:
    @bot.tree.command(name="news", description="Research a Thai news topic for สรุปให้ฟัง (facts review first)")
    async def news_cmd(inter: discord.Interaction, topic: str):
        if not only_control(inter):
            return await inter.response.send_message("Use the control channel.", ephemeral=True)
        await inter.response.send_message(f"Researching: {topic}")
        bot.loop.create_task(run_news_research(topic, inter.user.display_name))


if __name__ == "__main__":
    bot.run(TOKEN)
