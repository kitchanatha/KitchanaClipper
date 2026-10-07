"""News recaps (สรุปให้ฟัง): research -> (you approve the facts) -> script -> video.

  python run.py news-research "<topic>"     web research -> news/<slug>/research.json (+ table)
  python run.py news-show <slug>             print the research table again
  python run.py news-approve <slug>          you approve the facts (Discord: "Approve facts")
  python run.py news-build <slug>            script + safety review + voice + video -> review/

Safety rules live in pipeline/newslib.py and are enforced in code; the prompts repeat them.
"""
import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .common import ROOT, env, slugify
from .newslib import (apply_confirmation, confirmed_facts, monarchy_hits, safety_problems,
                      script_problems, sources_block, strip_date, thai_date)

API = "https://api.anthropic.com/v1/messages"
BKK = timezone(timedelta(hours=7))
CHARS_PER_S = 12  # Thai narration speed measured on the สรุปให้ฟัง voice (665 chars = 54 s)

RULES = """Hard rules - follow every one:
1. Nothing about the Thai monarchy, the royal family or Section 112, directly or indirectly. If the
   topic or ANY source links the story to the monarchy or a royal family member, set
   safety_check.monarchy = true and stop researching.
2. Facts only from credible news outlets. Each event must list every outlet URL that reported it.
   Allegations stay allegations (ถูกกล่าวหาว่า / อ้างว่า / ตามรายงานของ ...), never stated as fact.
   Record each side's response if one was reported. No opinions, insults, guesses or verdicts.
3. If the story centres on a private (non-public) person - a viral ordinary person, a minor, a victim
   - set safety_check.private_person or safety_check.minor_or_victim = true.
4. Never include names of minors or of crime victims, and no graphic details."""

RESEARCH_PROMPT = """Today is {today} (Bangkok). Research this Thai news story or drama for a neutral \
timeline recap video: {topic}

Use web search on credible news outlets ({outlets}). Find the recent coverage, then build a \
timeline of what happened, in order.

{rules}

Finish with ONLY this JSON inside <research></research> tags (Thai text in the *_th fields):
{{"topic": "...", "topic_en": "3-6 word English summary",
  "safety_check": {{"monarchy": false, "private_person": false, "minor_or_victim": false,
                   "notes": "why it is or isn't safe"}},
  "sources": [{{"outlet": "...", "url": "...", "date": "YYYY-MM-DD", "title": "..."}}],
  "timeline": [{{"date": "YYYY-MM-DD", "time_if_known": "HH:MM or empty", "event_th": "...",
                "source_urls": ["every URL that reported this event"]}}],
  "sides": [{{"party": "...", "position_th": "...", "source_urls": ["..."]}}],
  "status_now_th": "where things stand now, neutral",
  "status_source_urls": ["..."],
  "last_updated": "YYYY-MM-DD"}}
Only use URLs you actually found with the search tool."""

SCRIPT_PROMPT = """Write a Thai YouTube Shorts script for the channel "{channel}" (neutral news \
recaps). Spoken length {s_lo}-{s_hi} seconds (about {c_lo}-{c_hi} Thai characters of narration in \
total).

Use ONLY these confirmed facts (each event has an id; each fact was reported by 2+ outlets):
{facts}

{rules}
More rules for the script:
- hook: one neutral question that makes people curious. It must NOT imply anyone is guilty.
- beats: {b_lo}-{b_hi} beats in time order. Each beat: "event_ids" (ids it is based on), \
"card_text" (on-screen summary, max 40 Thai characters, NO date in it - the date is shown \
separately in a pill - and no names of minors/victims), "narration" (what the narrator says, \
2-3 sentences with the key numbers and who reported them, allegations phrased as allegations).
- latest: where it stands now. "card_text" (max 80 characters, no date) and "narration". If the \
status above is "none", describe only the most recent confirmed event.
- LENGTH BUDGET (Thai characters, spaces included): hook 50-70, each beat narration 90-120, \
latest narration 110-150. Total narration must land between {c_lo} and {c_hi} characters - \
scripts outside that range are rejected automatically, so keep every part inside its budget. \
Estimate lengths roughly; don't count characters one by one - the code measures them and will \
tell you the exact number if a draft is off.
- closing: exactly "{closing}"
- title: under 70 characters, must contain "สรุป", may include a date. No clickbait.
- description: 1-2 neutral sentences in Thai (sources are added automatically).
- hashtags: 4 Thai hashtags. keywords: 5 English search terms for NEUTRAL vertical stock video \
(rain, water, phone screen, documents, weather map, abstract data ...), never people, faces, logos, \
events, or recognisable cities, skylines or landmarks.
{fix}
Return ONLY JSON: {{"title": "...", "hook": "...", "beats": [{{"event_ids": ["e1"], \
"card_text": "...", "narration": "..."}}], "latest": {{"card_text": "...", "narration": "..."}}, \
"closing": "...", "description": "...", "hashtags": ["#..."], "keywords": ["..."]}}"""

REVIEW_PROMPT = """You are a careful Thai news-safety editor. Check this Shorts script against the \
confirmed facts. Reply ONLY JSON {{"ok": true/false, "issues": ["..."]}}.

Flag (ok=false) if ANY sentence: states something not supported by the facts; states an \
allegation as fact (must use ถูกกล่าวหาว่า / อ้างว่า / ตามรายงาน); contains an opinion, insult, \
guess or verdict-like wording; mentions the monarchy or royal family in any way; names a minor or \
a crime victim; contains graphic detail; or has a hook implying guilt.

Facts:
{facts}

Script:
{script}"""


# ---------------------------------------------------------------- helpers

def news_root(cfg):
    return ROOT / cfg["news"].get("dir", "news")


def news_dir(cfg, slug):
    d = news_root(cfg) / slug
    if not (d / "research.json").exists():
        raise SystemExit(f"No research for '{slug}' - run news-research first.")
    return d


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_in(text, tag=None):
    if tag:
        m = re.search(rf"<{tag}>\s*(\{{.*\}})\s*</{tag}>", text, re.S)
        if m:
            return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"Claude did not return JSON:\n{text[:400]}")
    return json.loads(m.group(0))


def claude_web_research(cfg, prompt, domains, max_uses=10, log=print, finish_tag="research"):
    """Claude + server-side web search, limited to the credible outlets. Returns
    (final text, verified result URLs, cited texts)."""
    headers = {"x-api-key": env("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses,
              "allowed_domains": list(domains)}]
    messages = [{"role": "user", "content": prompt}]
    texts, urls, cited, so_far, asked = [], [], [], [], False
    for turn in range(7):  # a long search can pause (pause_turn) - continue it
        for _ in range(3):
            r = requests.post(API, headers=headers, timeout=900, json={
                "model": cfg["news"].get("model") or cfg["claude"]["model"], "max_tokens": 16000,
                "tools": tools, "messages": messages})
            # some outlets (e.g. Reuters, AP) block Anthropic's crawler: drop them and retry
            blocked = re.search(r"domains are not accessible[^\[]*\[([^\]]*)\]", r.text) if r.status_code == 400 else None
            if not blocked:
                break
            gone = {d.strip(" '\"") for d in blocked.group(1).split(",")}
            log(f"  search can't reach {', '.join(sorted(gone))} - continuing without them")
            tools[0]["allowed_domains"] = [d for d in tools[0]["allowed_domains"] if d not in gone]
        if r.status_code != 200:
            raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        for b in data.get("content", []):
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                for res in b["content"]:
                    if res.get("url"):
                        urls.append(res["url"])
            elif b.get("type") == "text":
                texts.append(b.get("text", ""))
                for c in b.get("citations") or []:
                    if c.get("url"):
                        urls.append(c["url"])
                    if c.get("cited_text"):
                        cited.append(c["cited_text"])
        searches = sum(1 for b in data.get("content", []) if b.get("type") == "server_tool_use")
        log(f"  research pass {turn + 1}: {searches} searches, {len(set(urls))} pages found so far")
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("Claude declined to research this topic.")
        so_far += data.get("content", [])
        if data.get("stop_reason") == "pause_turn":  # resend everything said so far, it continues
            messages = [messages[0], {"role": "assistant", "content": so_far}]
            continue
        if finish_tag and f"<{finish_tag}>" not in "".join(texts) and not asked:
            # finished searching but didn't write the answer: ask for it once
            asked = True
            messages = [messages[0], {"role": "assistant", "content": so_far},
                        {"role": "user", "content": f"Now write the final answer: ONLY the JSON inside "
                                                    f"<{finish_tag}></{finish_tag}> tags. No more searches."}]
            so_far = []
            continue
        return "".join(texts), urls, cited
    raise RuntimeError("Research did not finish after several continuations.")


def print_table(research, log=print):
    sc = research.get("safety_check", {})
    log(f"TOPIC: {research.get('topic')}")
    log(f"SAFETY: monarchy={sc.get('monarchy')} private_person={sc.get('private_person')} "
        f"minor_or_victim={sc.get('minor_or_victim')} - {sc.get('notes', '')}")
    for p in research.get("safety_problems", []):
        log(f"  BLOCKED: {p}")
    log("TIMELINE:")
    for e in research.get("timeline", []):
        flag = "confirmed  " if e.get("confirmed") else "UNCONFIRMED"
        when = thai_date(e.get("date"), True) + (f" {e['time_if_known']}" if e.get("time_if_known") else "")
        log(f"  [{e['id']}] {when:16} {flag} {e.get('event_th', '')}")
        log(f"        sources: {', '.join(e.get('outlets') or []) or '-'}")
    log("SIDES:")
    for s in research.get("sides", []):
        log(f"  {'confirmed  ' if s.get('confirmed') else 'UNCONFIRMED'} {s.get('party')}: "
            f"{s.get('position_th')} ({', '.join(s.get('outlets') or [])})")
    log(f"NOW: {'confirmed  ' if research.get('status_confirmed') else 'UNCONFIRMED'} "
        f"{research.get('status_now_th')}")
    log("SOURCES:")
    for s in research.get("sources", []):
        ok = "ok" if s.get("credible") and s.get("verified") else (
            "not credible" if not s.get("credible") else "not found by search")
        log(f"  {s.get('outlet', '?'):16} {s.get('date', ''):10} {ok:20} {s.get('url')}")


# ---------------------------------------------------------------- research

def research(cfg, topic, log=print):
    nc = cfg["news"]
    hits = monarchy_hits(topic)
    if hits:  # rule 1, before spending anything. No override.
        raise SystemExit(f"Refused: this topic touches the monarchy / royal family "
                         f"({', '.join(hits)}). สรุปให้ฟัง does not cover these topics.")
    credible = nc["credible_outlets"]
    now = datetime.now(BKK)
    prompt = RESEARCH_PROMPT.format(today=now.strftime("%Y-%m-%d"), topic=topic,
                                    outlets=", ".join(sorted(set(credible.values()))), rules=RULES)
    log(f"Researching: {topic}")
    text, urls, cited = claude_web_research(cfg, prompt, credible.keys(),
                                            int(nc.get("max_searches", 10)), log)
    data = _json_in(text, "research")
    data["topic"] = data.get("topic") or topic
    data["requested_topic"] = topic
    data = apply_confirmation(data, credible, urls, int(nc.get("min_outlets", 2)))
    data["verified_urls"] = sorted(set(urls))
    data["safety_problems"] = safety_problems(data, cited)
    data["facts_approved"] = False
    data["researched_at"] = now.isoformat(timespec="seconds")
    base = slugify(re.sub(r"[^A-Za-z0-9 ]+", " ", data.get("topic_en") or ""), 30)
    base = base if base and base != "video" else hashlib.sha1(topic.encode()).hexdigest()[:8]
    slug = f"{now:%Y%m%d}-{base}"  # ASCII: used in folder names and Discord button ids
    d = news_root(cfg) / slug
    d.mkdir(parents=True, exist_ok=True)
    write_json(d / "research.json", data)
    log(f"Saved {d / 'research.json'}")
    return slug, data


def approve_facts(cfg, slug, who="you"):
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    problems = safety_problems(data)
    if problems:
        raise SystemExit("Can't approve - this story is blocked:\n  - " + "\n  - ".join(problems))
    if len(confirmed_facts(data)["events"]) < int(cfg["news"].get("min_beats", 4)):
        raise SystemExit("Can't approve - fewer than 4 confirmed events (2+ outlets each).")
    data["facts_approved"] = True
    data["facts_approved_by"] = who
    data["facts_approved_at"] = datetime.now(BKK).isoformat(timespec="seconds")
    write_json(d / "research.json", data)
    return data


def replace_timeline(cfg, slug, new_timeline):
    """'Edit' on Discord: use a corrected timeline. URLs must be among the searched sources;
    confirmation is recomputed and the facts need approving again."""
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    if isinstance(new_timeline, dict):
        new_timeline = new_timeline.get("timeline", [])
    data["timeline"] = [{k: e.get(k) for k in ("date", "time_if_known", "event_th", "source_urls")}
                        for e in new_timeline]
    data = apply_confirmation(data, cfg["news"]["credible_outlets"], data.get("verified_urls", []),
                              int(cfg["news"].get("min_outlets", 2)))
    data["safety_problems"] = safety_problems(data)
    data["facts_approved"] = False
    data["edited_at"] = datetime.now(BKK).isoformat(timespec="seconds")
    write_json(d / "research.json", data)
    return data


# ---------------------------------------------------------------- script

def _facts_text(data):
    f = confirmed_facts(data)
    return json.dumps({
        "events": [{"id": e["id"], "date": e.get("date"), "time": e.get("time_if_known"),
                    "event_th": e["event_th"], "reported_by": e["outlets"]} for e in f["events"]],
        "sides": [{"party": s.get("party"), "position_th": s.get("position_th"),
                   "reported_by": s["outlets"]} for s in f["sides"]],
        "status_now_th": f["status_now_th"] or "none"}, ensure_ascii=False, indent=1)


def write_script(cfg, data, log=print):
    from .script import ask_claude
    nc = cfg["news"]
    closing = nc["closing_line"]
    s_lo, s_hi = nc.get("script_seconds", [60, 90])
    fix, problems = "", []
    lo, hi = s_lo * (CHARS_PER_S - 1), s_hi * CHARS_PER_S - 60  # this voice: ~12 Thai chars / s
    for attempt in range(5):
        prompt = SCRIPT_PROMPT.format(channel=cfg["channels"][nc["channel"]]["name"], s_lo=s_lo,
                                      s_hi=s_hi, c_lo=lo, c_hi=hi, facts=_facts_text(data),
                                      rules=RULES + "".join(f"\n- Editor's note: {n}"
                                                            for n in data.get("script_notes", [])),
                                      b_lo=nc.get("min_beats", 4),
                                      b_hi=nc.get("max_beats", 7), closing=closing, fix=fix)
        script = _json_in(ask_claude(cfg, prompt, max_tokens=16000, timeout=600, effort="medium"))
        script["closing"] = closing if script.get("closing", "").strip() == closing else script.get("closing", "")
        for b in script.get("beats", []) + [script.get("latest") or {}]:
            b["card_text"] = strip_date(b.get("card_text", ""))  # the date pill shows it already
        problems = script_problems(script, data, nc.get("min_beats", 4), nc.get("max_beats", 7), closing)
        spoken = sum(len(t) for _, t in narration_parts(script)) if not problems else 0
        if spoken and not lo <= spoken <= hi:
            problems.append(f"narration is {spoken} characters - must be {lo}-{hi} "
                            f"({s_lo}-{s_hi} s spoken)")
        if not problems:
            notes = "".join(f"\nEditor's rule (also flag any breach): {n}" for n in data.get("script_notes", []))
            review = _json_in(ask_claude(cfg, REVIEW_PROMPT.format(
                facts=_facts_text(data) + notes, script=json.dumps(script, ensure_ascii=False, indent=1)),
                max_tokens=8000, timeout=600))
            if review.get("ok") and not review.get("issues"):
                return script
            problems = ["safety review: " + i for i in review.get("issues", [])] or ["safety review failed"]
        log(f"  script attempt {attempt + 1} rejected: {'; '.join(problems)[:300]}")
        fix = "Your previous draft was rejected for: " + "; ".join(problems) + ". Fix all of these."
        if spoken > hi:  # too long: show the measured parts and ask for a trim of this draft
            sizes = ", ".join(f"{pid} {len(t)}" for pid, t in narration_parts(script))
            fix += (f"\nMeasured characters per part: {sizes} (total {spoken}, maximum {hi}). "
                    f"Shorten THIS draft by at least {spoken - hi + 40} characters - drop the least "
                    f"important details or merge two beats; keep everything else as it is:\n"
                    + json.dumps(script, ensure_ascii=False))
        elif script.get("beats"):  # revise this draft instead of starting over (new slips each time)
            fix += ("\nRevise THIS draft with the smallest changes that fix the points above; keep "
                    "everything else (and the total length) as it is:\n" + json.dumps(script, ensure_ascii=False))
    raise SystemExit("Could not write a script that passes the safety checks:\n  - " + "\n  - ".join(problems))


def narration_parts(script):
    """[(part_id, text)] in speaking order: hook, b0..bn, latest, closing."""
    parts = [("hook", script["hook"].strip())]
    parts += [(f"b{i}", b["narration"].strip()) for i, b in enumerate(script["beats"])]
    parts.append(("latest", "ล่าสุด " + script["latest"]["narration"].strip()))
    parts.append(("closing", script["closing"].strip()))
    return parts


# ---------------------------------------------------------------- build

def build(cfg, slug, force_script=False, log=print):
    import run as core
    from .assemble import duration, finish_video, make_background
    from .footage import get_footage
    from .newsvideo import check_card_texts, write_news_ass
    from .social import add_social
    from .storylib import char_times
    from .voice import check_voice_id, tts_with_timestamps, word_timings

    nc = cfg["news"]
    key = nc["channel"]
    ch = cfg["channels"][key]
    d = news_dir(cfg, slug)
    data = read_json(d / "research.json")
    problems = safety_problems(data)
    if problems:
        raise SystemExit("Blocked:\n  - " + "\n  - ".join(problems))
    if not data.get("facts_approved"):
        raise SystemExit("The facts are not approved yet (Discord 'Approve facts' or news-approve).")
    voice = check_voice_id(ch)

    sf = d / "script.json"
    if force_script or not sf.exists():
        log("Writing the script + safety review...")
        write_json(sf, write_script(cfg, data, log))
    script = read_json(sf)
    if script_problems(script, data, nc.get("min_beats", 4), nc.get("max_beats", 7), nc["closing_line"]):
        raise SystemExit("script.json no longer passes the checks - rebuild with --force")

    # ---- voice (cached: same text + voice = no new credits)
    parts = narration_parts(script)
    text = " ".join(t for _, t in parts)
    model = cfg["elevenlabs"]["model_id"]
    settings = {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2}
    k = hashlib.sha1(json.dumps([voice, model, settings, text], ensure_ascii=False).encode()).hexdigest()[:12]
    mp3, alf = d / f"voice_{k}.mp3", d / f"voice_{k}.json"
    if not (mp3.exists() and alf.exists()):
        log(f"Recording voice ({len(text)} characters)...")
        al = tts_with_timestamps(voice, text, d / f"voice_{k}.part.mp3", model, settings)
        write_json(alf, al or {})
        (d / f"voice_{k}.part.mp3").replace(mp3)
    al = read_json(alf)
    words = word_timings(text, al, "th")
    times = char_times(text, al)
    starts, pos = {}, 0
    for pid, t in parts:
        i = text.find(t, pos)
        starts[pid] = times[i][0] if 0 <= i < len(times) else 0.0
        pos = max(i, pos) + len(t)

    events = {e["id"]: e for e in data["timeline"]}
    beats = []
    for i, b in enumerate(script["beats"]):
        evs = [events[x] for x in b["event_ids"] if x in events]
        outlets = sorted({o for e in evs for o in e.get("outlets", [])})
        beats.append({"start": starts[f"b{i}"], "date_label": thai_date(evs[0].get("date")) if evs else "",
                      "card_text": b["card_text"], "outlets": outlets})
    latest = {"start": starts["latest"], "card_text": script["latest"]["card_text"],
              "outlets": data.get("status_outlets") or (beats[-1]["outlets"] if beats else [])}
    bad = check_card_texts(beats, latest)
    if bad:
        raise SystemExit("Card text too long for the layout: " + " | ".join(bad))

    # ---- footage + render
    out_dir = core.dirs(cfg)["review"] / key / slug
    work = core.dirs(cfg)["work"] / f"news-{slug}"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        total = duration(mp3) + 0.8
        log("Footage...")
        clips, ids = get_footage(script.get("keywords", []), ch.get("footage_hint", ""), total,
                                 float(cfg["video"]["clip_seconds"]), work, [], strict=True,
                                 exclude=nc.get("footage_exclude", []))
        log("Rendering...")
        temp = make_background(cfg, clips, total, work)
        write_news_ass(work / "news.ass", ch, cfg, script["title"], beats, latest, words, total)
        finish_video(cfg, work, "news.ass", mp3, total, out_dir / "video.mp4", temp,
                     darken=float(nc.get("darken", 0.55)))
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    used = sorted({u for b in script["beats"] for x in b["event_ids"] if x in events
                   for u in events[x].get("source_urls", [])} | set(data.get("status_source_urls", [])))
    desc = "\n\n".join(x for x in [
        script.get("description", ""),
        "แหล่งข่าว:\n" + sources_block(data, set(used)),
        nc.get("disclaimer", ""), ch.get("description_footer", ""),
        " ".join(dict.fromkeys(ch.get("hashtags", []) + script.get("hashtags", [])))] if x)
    meta = {"kind": "news", "news": slug, "channel": key, "channel_name": ch["name"],
            "title": script["title"][:100], "script": " ".join(t for _, t in parts),
            "description": desc, "tags": [h.lstrip("#") for h in ch.get("hashtags", []) + script.get("hashtags", [])],
            "length_sec": round(total, 1), "sources": used, "pexels_ids": ids,
            "created": datetime.now(BKK).isoformat(timespec="seconds")}
    write_json(out_dir / "meta.json", meta)
    (out_dir / "script.txt").write_text(script["title"] + "\n\n" + meta["script"] + "\n", encoding="utf-8")
    add_social(cfg, key, out_dir, log)
    log(f"Video ready for review -> {out_dir}")
    return out_dir
