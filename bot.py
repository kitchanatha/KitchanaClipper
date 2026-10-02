"""Discord control bot - runs 24/7, makes videos daily, lets you approve from your phone.

Start:  python bot.py
Each computer runs its own bot (own token, own control channel, own MACHINE_CHANNELS).
"""
import asyncio
import json
import os
import subprocess
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


class ReviewView(discord.ui.View):
    def __init__(self, key, name):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Approve", style=discord.ButtonStyle.success,
                                        custom_id=f"fp:a:{key}/{name}"))
        self.add_item(discord.ui.Button(label="Reject", style=discord.ButtonStyle.danger,
                                        custom_id=f"fp:r:{key}/{name}"))


async def post_card(folder):
    folder = Path(folder)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    key = folder.parent.name
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
    if not cid.startswith("fp:"):
        return
    _, action, rest = cid.split(":", 2)
    key, name = rest.split("/", 1)
    folder = Path(cfg["output_dir"]) / "review" / key / name
    if not folder.exists():
        await inter.response.send_message("Already handled.", ephemeral=True)
        return
    await inter.response.defer()
    approve = action == "a"
    dest = await asyncio.to_thread(core.decide, cfg, folder, approve)
    note = f"Approved by {inter.user.display_name}" if approve else f"Rejected by {inter.user.display_name}"
    if approve and AUTO.get("upload_after_approve", True):
        try:
            url, slot = await asyncio.to_thread(core.upload_one, cfg, key, dest)
            note += f"\n{upload_note(slot)}\n{url}"
        except BaseException as e:  # SystemExit from missing auth too
            note += f"\nUpload failed: {e}\nFix it, then use /upload."
    await inter.edit_original_response(view=None, content=note)


# ---------------- jobs ----------------

async def run_generate(keys, count, reason):
    if job_lock.locked():
        await say("A job is already running - try again when it finishes.")
        return
    async with job_lock:
        await say(f"**{MACHINE}**: {reason} - making {count} video(s) for {', '.join(keys)}...")
        made, failed = 0, 0
        for key in keys:
            for _ in range(count):
                try:
                    folder = await asyncio.to_thread(core.generate_one, cfg, key)
                    made += 1
                    await post_card(folder)
                except BaseException as e:
                    failed += 1
                    await say(f"[{key}] failed: `{str(e)[:400]}`")
                    with open(ROOT / "data" / "errors.log", "a", encoding="utf-8") as f:
                        f.write(f"\n--- {datetime.now()} {key}\n{traceback.format_exc()}")
        state["last_result"] = f"{datetime.now():%d %b %H:%M}: {made} made, {failed} failed"
        save_state(state)
        await say(f"**{MACHINE}** done: {made} ready for review, {failed} failed.")


async def daily_loop():
    await bot.wait_until_ready()
    await say(f"**{MACHINE}** online. Channels: {', '.join(MY_CHANNELS)}. "
              f"Daily run at {state['daily_time']}{' (PAUSED)' if state['paused'] else ''}.")
    while not bot.is_closed():
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if (not state["paused"] and state["last_daily"] != today
                and now.strftime("%H:%M") >= state["daily_time"]):
            state["last_daily"] = today
            save_state(state)
            await run_generate(MY_CHANNELS, int(cfg.get("daily_count", 1)), "daily run")
        await asyncio.sleep(30)


def counts():
    base = Path(cfg["output_dir"])
    out = []
    for k in MY_CHANNELS:
        n = {s: len([p for p in (base / s / k).glob("*") if p.is_dir()])
             for s in ("review", "approved", "uploaded")}
        out.append(f"{cfg['channels'][k]['name']}: {n['review']} to review, "
                   f"{n['approved']} approved, {n['uploaded']} uploaded")
    return "\n".join(out)


# ---------------- slash commands ----------------

CHOICES = [app_commands.Choice(name="all my channels", value="all")] + [
    app_commands.Choice(name=cfg["channels"][k]["name"], value=k) for k in MY_CHANNELS]


@bot.tree.command(description="Show this computer's status")
async def status(inter: discord.Interaction):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    busy = "working on a job" if job_lock.locked() else "idle"
    await inter.response.send_message(
        f"**{MACHINE}** is {busy}{' · PAUSED' if state['paused'] else ''}\n"
        f"Daily run: {state['daily_time']} · Last: {state['last_result']}\n{counts()}")


@bot.tree.command(description="Make videos now")
@app_commands.choices(channel=CHOICES)
@app_commands.describe(count="videos per channel (1-5)")
async def generate(inter: discord.Interaction, channel: app_commands.Choice[str], count: int = 1):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    keys = MY_CHANNELS if channel.value == "all" else [channel.value]
    await inter.response.send_message(f"Starting {max(1, min(count, 5))} per channel...")
    bot.loop.create_task(run_generate(keys, max(1, min(count, 5)), "manual run"))


@bot.tree.command(description="Re-post videos waiting for review")
async def pending(inter: discord.Interaction):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    base = Path(cfg["output_dir"]) / "review"
    items = [p for k in MY_CHANNELS for p in sorted((base / k).glob("*")) if (p / "video.mp4").exists()]
    await inter.response.send_message(f"{len(items)} waiting for review.")
    for p in items:
        await post_card(p)


@bot.tree.command(description="Upload all approved videos now")
async def upload(inter: discord.Interaction):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    await inter.response.defer()
    base = Path(cfg["output_dir"]) / "approved"
    lines = []
    for k in MY_CHANNELS:
        for p in sorted((base / k).glob("*")):
            if not (p / "video.mp4").exists():
                continue
            try:
                url, slot = await asyncio.to_thread(core.upload_one, cfg, k, p)
                lines.append(f"✅ {upload_note(slot)} {url}")
            except BaseException as e:
                lines.append(f"❌ {p.name}: {str(e)[:200]}")
    await inter.followup.send("\n".join(lines) or "Nothing approved to upload.")


@bot.tree.command(description="Pause the daily run")
async def pause(inter: discord.Interaction):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    state["paused"] = True
    save_state(state)
    await inter.response.send_message(f"**{MACHINE}** paused.")


@bot.tree.command(description="Resume the daily run")
async def resume(inter: discord.Interaction):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    state["paused"] = False
    save_state(state)
    await inter.response.send_message(f"**{MACHINE}** resumed. Next run at {state['daily_time']}.")


@bot.tree.command(description="Change the daily run time (24h, e.g. 01:30)")
async def settime(inter: discord.Interaction, time: str):
    if not only_control(inter):
        return await inter.response.send_message("Use the control channel.", ephemeral=True)
    try:
        t = datetime.strptime(time.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        return await inter.response.send_message("Use HH:MM, e.g. 01:30", ephemeral=True)
    state["daily_time"] = t
    save_state(state)
    await inter.response.send_message(f"Daily run on **{MACHINE}** set to {t}.")


if __name__ == "__main__":
    bot.run(TOKEN)
