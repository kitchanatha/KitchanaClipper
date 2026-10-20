"""Post a plain message to the Discord control channel (used by the Claude Code daily task).
Usage: python scripts/notify.py "message text"
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.common import env, load_config  # noqa: E402

load_config()  # loads .env
r = requests.post(f"https://discord.com/api/v10/channels/{env('DISCORD_CONTROL_CHANNEL_ID')}/messages",
                  headers={"Authorization": f"Bot {env('DISCORD_BOT_TOKEN')}"},
                  json={"content": " ".join(sys.argv[1:])[:1900]}, timeout=30)
r.raise_for_status()
print("sent")
