"""Per-channel switches: enabled / daily_count, with Discord overrides.

config.yaml sets the defaults (`enabled: false`, `daily_count: 2` under a channel). Discord's
/channel-enable and /channel-disable write overrides to data/channel_state.json, so the bot
never has to rewrite config.yaml.
"""
import json

from .common import ROOT

STATE = ROOT / "data" / "channel_state.json"


def _overrides():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enabled(cfg, key):
    o = _overrides().get(key, {})
    return bool(o.get("enabled", cfg["channels"][key].get("enabled", True)))


def set_enabled(key, on):
    data = _overrides()
    data.setdefault(key, {})["enabled"] = bool(on)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def daily_count(cfg, key):
    """Videos per daily run for this channel (0 = manual only). Disabled channels make 0."""
    if not enabled(cfg, key):
        return 0
    return int(cfg["channels"][key].get("daily_count", cfg.get("daily_count", 1)))


def publish_times(cfg, key, fallback=None):
    """This channel's publish slots (local time), else the given / global ones."""
    return cfg["channels"][key].get("publish_times") or fallback or cfg["youtube"]["publish_times"]
