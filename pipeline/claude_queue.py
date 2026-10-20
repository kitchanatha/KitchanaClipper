"""Requests from Discord for Claude Code (no API credit needed).

The bot's "Request" button adds a line here; a scheduled Claude Code task on this PC reads
the queued ones, makes the videos (they land in review/ like any other) and marks them done.
File: data/claude_queue.json  [{id, text, user, created, status, note}]
status: queued -> working -> done / failed / needs-session
"""
import json
from datetime import datetime

from pipeline.common import ROOT

QUEUE = ROOT / "data" / "claude_queue.json"
ICON = {"queued": "⏳", "working": "⚙️", "done": "✅", "failed": "❌", "needs-session": "💬"}


def load():
    return json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else []


def save(items):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(text, user):
    items = load()
    item = {"id": max((i["id"] for i in items), default=0) + 1, "text": text.strip(), "user": user,
            "created": datetime.now().isoformat(timespec="seconds"), "status": "queued", "note": ""}
    save(items + [item])
    return item


def cancel(item_id):
    items = load()
    for i in items:
        if i["id"] == item_id and i["status"] == "queued":
            i["status"], i["note"] = "failed", "cancelled"
            save(items)
            return True
    return False


def set_status(item_id, status, note=""):
    items = load()
    for i in items:
        if i["id"] == item_id:
            i["status"], i["note"] = status, note
            i["updated"] = datetime.now().isoformat(timespec="seconds")
    save(items)


# videos Claude Code made: the bot posts their review cards (Approve / Reject) within a minute
CARDS = ROOT / "data" / "claude_cards.json"


def card_later(folder):
    todo = json.loads(CARDS.read_text(encoding="utf-8")) if CARDS.exists() else []
    CARDS.write_text(json.dumps(todo + [str(folder)], ensure_ascii=False, indent=2), encoding="utf-8")


def take_cards():
    if not CARDS.exists():
        return []
    todo = json.loads(CARDS.read_text(encoding="utf-8"))
    CARDS.unlink()
    return todo


def queue_text(limit=10):
    items = load()
    if not items:
        return "The Claude Code queue is empty."
    lines = [f"{ICON.get(i['status'], '•')} #{i['id']} {i['text'][:120]}"
             + (f" — _{i['note'][:150]}_" if i.get("note") else "") for i in items[-limit:]]
    waiting = sum(i["status"] == "queued" for i in items)
    return f"**Claude Code queue** ({waiting} waiting)\n" + "\n".join(lines)


if __name__ == "__main__":  # used by the Claude Code tasks
    import sys
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "list":
        print(json.dumps([i for i in load() if i["status"] == "queued"], ensure_ascii=False, indent=2))
    elif cmd == "status":  # status <id> <working|done|failed|needs-session> [note]
        set_status(int(args[0]), args[1], " ".join(args[2:]))
    elif cmd == "card":  # card <review folder>  -> the bot posts its review card
        card_later(args[0])
    print("ok")
