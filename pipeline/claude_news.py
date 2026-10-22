"""สรุปให้ฟัง without the Anthropic API: Claude Code (scheduled tasks on this PC) does the web
research and writes the scripts; this module gives it the SAME prompts and runs the SAME code
checks (credible outlets, 2+ outlets per fact, monarchy / private people / minors, script rules,
length) before anything is saved. Discord approval steps are unchanged.

  python -m pipeline.claude_news topics-prompt
  python -m pipeline.claude_news topics-import <file.json>          -> topic cards in Discord
  python -m pipeline.claude_news research-prompt "<topic>" [--long]
  python -m pipeline.claude_news research-import "<topic>" <file.json> [--long]   -> facts card
  python -m pipeline.claude_news script-prompt <slug> [--long]
  python -m pipeline.claude_news script-check <slug> <file.json> [--long]  -> saves script(_long).json
  python -m pipeline.claude_news review-prompt <slug> [--long]

File shapes: topics-import {"topics": [...as the prompt asks], "urls": [...every URL you opened]};
research-import {"research": {...as the prompt asks}, "urls": [...every URL you opened]}.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from .common import load_config
from .claude_queue import card_later


def topics_prompt(cfg):
    from .topics import PROMPT, covered_topics
    covered = covered_topics(cfg)
    return PROMPT.format(today=datetime.now().strftime("%d %B %Y"), n=6,
                         covered="\n".join(f"- {c['date']}: {c.get('topic_en') or c.get('title')}"
                                           for c in covered) or "- (none)")


def topics_import(cfg, path):
    from .topics import TOPICS_DIR, covered_topics, filter_topics
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    credible = cfg["news"]["credible_outlets"]
    urls = [u for u in raw.get("urls", []) if isinstance(u, str)]
    kept, dropped = filter_topics(raw.get("topics", []), credible, urls, covered_topics(cfg), 3)
    data = {"created": datetime.now().isoformat(timespec="seconds"), "topics": kept,
            "dropped": [{"title": a, "reason": b} for a, b in dropped],
            "candidates": raw.get("topics", []), "by": "claude-code"}
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    day = f"{datetime.now():%Y%m%d}"
    (TOPICS_DIR / f"{day}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    card_later(f"topics:{day}")
    return data


def _script_settings(cfg, long):
    from .news import CHARS_PER_S
    nc = cfg["news"]
    if long:
        s_lo, s_hi = nc.get("long_seconds", [300, 540])
        return s_lo, s_hi, s_lo * (CHARS_PER_S - 1), s_hi * CHARS_PER_S
    s_lo, s_hi = nc.get("script_seconds", [60, 90])
    return s_lo, s_hi, s_lo * (CHARS_PER_S - 1), s_hi * CHARS_PER_S - 60


def script_prompt(cfg, slug, long=False):
    from .news import RULES, SCRIPT_PROMPT, _facts_text, news_dir, read_json
    from .newslib import HOOK_LINE, confirmed_facts
    nc = cfg["news"]
    data = read_json(news_dir(cfg, slug) / "research.json")
    s_lo, s_hi, lo, hi = _script_settings(cfg, long)
    rules = RULES + "".join(f"\n- Editor's note: {n}" for n in data.get("script_notes", []))
    if long:
        from .newslong import LONG_PROMPT
        n_events = len(confirmed_facts(data)["events"])
        b_lo = int(nc.get("long_min_beats", 8))
        b_hi = min(int(nc.get("long_max_beats", 15)), n_events)
        return LONG_PROMPT.format(s_lo=s_lo, s_hi=s_hi, c_lo=lo, c_hi=hi,
                                  channel=cfg["channels"][nc["channel"]]["name"], facts=_facts_text(data),
                                  b_lo=b_lo, b_hi=b_hi, closing=nc.get("long_closing"), fix="", rules=rules)
    return SCRIPT_PROMPT.format(channel=cfg["channels"][nc["channel"]]["name"], s_lo=s_lo, s_hi=s_hi,
                                c_lo=lo, c_hi=hi, facts=_facts_text(data), rules=rules,
                                b_lo=nc.get("min_beats", 4), b_hi=nc.get("max_beats", 7),
                                closing=nc["closing_line"], fix="", hook_line=HOOK_LINE)


def review_prompt(cfg, slug, long=False):
    """The safety review to do on your own finished script before script-check."""
    from .news import REVIEW_PROMPT, _facts_text, news_dir, read_json
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    notes = "".join(f"\nEditor's rule (also flag any breach): {n}" for n in data.get("script_notes", []))
    return REVIEW_PROMPT.format(facts=_facts_text(data) + notes, script="<your script JSON>")


def script_check(cfg, slug, path, long=False):
    """Same normalisation + checks as write_script / write_long_script. Saves only when clean."""
    from .news import news_dir, read_json, spoken_parts, strip_date, write_json
    from .newslib import confirmed_facts, script_problems
    nc = cfg["news"]
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    s_lo, s_hi, lo, hi = _script_settings(cfg, long)
    if long:
        from .newslong import long_parts, long_problems
        from .newslib import speakable
        closing = nc.get("long_closing")
        b_lo = int(nc.get("long_min_beats", 8))
        b_hi = min(int(nc.get("long_max_beats", 15)), len(confirmed_facts(data)["events"]))
        for part in (script.get("beats") or []) + [script.get("background") or {}, script.get("next") or {}]:
            part["card_text"] = strip_date(part.get("card_text", ""))
        problems = long_problems(script, data, b_lo, b_hi, closing)
        spoken = 0 if problems else sum(len(speakable(t, nc.get("pronounce") or {}))
                                        for _, _, t in long_parts(script))
    else:
        closing = nc["closing_line"]
        script["closing"] = closing if script.get("closing", "").strip() == closing else script.get("closing", "")
        for b in script.get("beats", []) + [script.get("latest") or {}]:
            b["card_text"] = strip_date(b.get("card_text", ""))
        problems = script_problems(script, data, nc.get("min_beats", 4), nc.get("max_beats", 7), closing)
        spoken = 0 if problems else sum(len(t) for _, t in spoken_parts(cfg, script))
    if spoken and not lo <= spoken <= hi:
        problems.append(f"narration is {spoken} characters - must be {lo}-{hi} ({s_lo}-{s_hi} s spoken)")
    if problems:
        return problems
    write_json(d / ("script_long.json" if long else "script.json"), script)
    return []


def main(argv):
    cfg = load_config()
    cmd, args = argv[0], [a for a in argv[1:] if a != "--long"]
    long = "--long" in argv
    if cmd == "topics-prompt":
        print(topics_prompt(cfg))
    elif cmd == "topics-import":
        data = topics_import(cfg, args[0])
        print(f"{len(data['topics'])} topics passed the filter (cards go to Discord within a minute).")
        for d in data["dropped"]:
            print(f"  dropped: {d['title']} - {d['reason']}")
    elif cmd == "research-prompt":
        from .news import research_prompt
        print(research_prompt(cfg, args[0], long))
    elif cmd == "research-import":
        from .news import import_research, print_table
        slug, data = import_research(cfg, args[0], args[1], long)
        print_table(data)
        card_later(f"facts:{slug}")
        print(f"SLUG {slug} - facts card goes to Discord within a minute.")
    elif cmd == "script-prompt":
        print(script_prompt(cfg, args[0], long))
    elif cmd == "review-prompt":
        print(review_prompt(cfg, args[0], long))
    elif cmd == "script-check":
        problems = script_check(cfg, args[0], args[1], long)
        print("OK - saved." if not problems else "PROBLEMS:\n- " + "\n- ".join(problems))
        sys.exit(1 if problems else 0)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
