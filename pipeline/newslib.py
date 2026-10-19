"""News recaps (สรุปให้ฟัง) - pure safety + timeline logic, unit-tested in tests/test_news.py.

Hard rules enforced here (the Claude prompts repeat them, but code has the last word):
  1. Anything touching the Thai monarchy / royal family / Section 112 is blocked. No override.
  2. A fact counts only if at least 2 different credible outlets reported it, through URLs the
     web search actually returned. Everything else is "unconfirmed" and stays out of the script.
  3. Stories centred on private individuals, minors or crime victims are refused.
"""
import re
import unicodedata
from urllib.parse import urlsplit

# ---------------------------------------------------------------- 1. monarchy screen

# Matched as substrings in Thai text (Thai has no spaces between words).
MONARCHY_TH = [
    "สถาบันพระมหากษัตริย์", "สถาบันกษัตริย์", "พระมหากษัตริย์", "กษัตริย์", "ในหลวง", "พระราชา",
    "พระราชินี", "ราชินี", "ราชวงศ์", "ราชสกุล", "พระบรมวงศานุวงศ์", "พระบรมวงศ์", "พระบรม",
    "เจ้าฟ้า", "พระองค์เจ้า", "หม่อมเจ้า", "พระเจ้าอยู่หัว", "ราชสำนัก", "พระราชวัง",
    "มกุฎราชกุมาร", "พระราชกรณียกิจ", "พระราชดำริ", "พระราชทาน", "เสด็จ", "ทรงพระ",
    "หมิ่นสถาบัน", "หมิ่นพระบรมเดชานุภาพ", "ปฏิรูปสถาบัน", "ราชูปถัมภ์", "พระราชานุเคราะห์",
    "พระบรมราชโองการ", "ทูลเกล้า", "ราชาภิเษก", "พระฉายาลักษณ์", "สมเด็จพระนาง", "รัชกาล",
    "วชิราลงกรณ", "ภูมิพล", "สิริกิติ์", "สุทิดา", "พัชรกิติยาภา", "สิริวัณณวรี", "ทีปังกร",
    "สิรินธร", "จุฬาภรณ์", "ราชนิกุล", "ฝ่ายในราชสำนัก",
]
# Matched as whole words in Latin text.
MONARCHY_EN = [
    "monarchy", "monarch", "monarchist", "king", "queen", "royal family", "royals", "royalist",
    "royalists", "lese majeste", "lese-majeste", "lèse majesté", "lèse-majesté", "crown prince",
    "princess", "prince", "his majesty", "her majesty", "majesty", "palace", "coronation",
    "king rama", "rama x", "rama ix", "rama 10", "rama 9", "vajiralongkorn", "bhumibol",
    "sirikit", "suthida", "bajrakitiyabha", "sirivannavari", "dipangkorn", "sirindhorn",
    "chulabhorn", "royal court",
]
# Section 112 in any spelling: ม.112, ม. 112, มาตรา 112, section/article 112
SECTION_112 = re.compile(r"(ม\s*\.\s*|มาตรา\s*|section\s*|article\s*|sec\.\s*)112", re.I)

# Everyday names that only look royal. Removed before matching, so they don't block normal news.
NOT_ROYAL = [
    "พระราชบัญญัติ", "ร่างพระราชบัญญัติ", "พระราชกำหนด", "พระราชกฤษฎีกา", "ราชกิจจานุเบกษา",
    "royal thai police", "royal thai army", "royal thai navy", "royal thai air force",
    "royal thai armed forces", "royal thai government", "royal gazette", "royal irrigation",
    "burger king", "king power", "stephen king", "prince of songkla", "king's college",
    "kingdom of thailand",
]


def _norm(text):
    t = unicodedata.normalize("NFC", str(text or "")).lower()
    t = t.replace("​", "").replace("﻿", "")
    t = re.sub(r"[̀-ͯ]", "", unicodedata.normalize("NFD", t))  # lèse -> lese
    t = unicodedata.normalize("NFC", t)
    for safe in NOT_ROYAL:
        t = t.replace(unicodedata.normalize("NFC", re.sub(r"[̀-ͯ]", "",
                      unicodedata.normalize("NFD", safe.lower()))), " ")
    return t


def monarchy_hits(text):
    """Every monarchy-related term found in text (empty list = nothing found)."""
    t = _norm(text)
    hits = [w for w in MONARCHY_TH if w in t]
    for w in MONARCHY_EN:
        w2 = _norm(w)
        if re.search(r"(?<![a-z])" + re.escape(w2) + r"(?![a-z])", t):
            hits.append(w)
    if SECTION_112.search(t):
        hits.append("section 112")
    return sorted(set(hits))


# ---------------------------------------------------------------- 2. sources & confirmation

def domain_of(url):
    host = urlsplit(str(url or "")).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def outlet_for(url, credible):
    """Outlet name if url belongs to a credible outlet (sub-domains count), else None."""
    host = domain_of(url)
    for dom, name in credible.items():
        if host == dom or host.endswith("." + dom):
            return name
    return None


def norm_url(url):
    p = urlsplit(str(url or "").strip())
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    path = p.path.rstrip("/")
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


def confirm(urls, credible, verified, min_outlets=2):
    """(confirmed, outlets, good_urls): good_urls are the urls that were really returned by the
    search (verified) AND belong to credible outlets; confirmed needs min_outlets different ones."""
    vset = {norm_url(u) for u in verified}
    good, outlets = [], []
    for u in urls or []:
        name = outlet_for(u, credible)
        if name and norm_url(u) in vset and u not in good:
            good.append(u)
            if name not in outlets:
                outlets.append(name)
    return len(outlets) >= min_outlets, outlets, good


def sort_timeline(events):
    """By date (YYYY-MM-DD), then time (HH:MM) if known; undated events last."""
    def key(e):
        d = str(e.get("date") or "")
        d = d if re.match(r"^\d{4}-\d{2}-\d{2}$", d) else "9999-99-99"
        t = str(e.get("time_if_known") or "")
        t = t if re.match(r"^\d{1,2}:\d{2}$", t) else "99:99"
        return (d, t.zfill(5))
    return sorted(events, key=key)


def apply_confirmation(research, credible, verified, min_outlets=2):
    """Recompute confirmed / outlets for every event, side and the current status from the
    sources - Claude's own 'confirmed' flags are ignored. Sorts the timeline, numbers events."""
    sources = []
    for s in research.get("sources", []):
        name = outlet_for(s.get("url"), credible)
        s["credible"] = bool(name)
        s["verified"] = norm_url(s.get("url")) in {norm_url(u) for u in verified}
        if name:
            s["outlet"] = name
        sources.append(s)
    research["sources"] = sources
    timeline = sort_timeline(research.get("timeline", []))
    for i, e in enumerate(timeline, 1):
        e["id"] = f"e{i}"
        e["confirmed"], e["outlets"], e["source_urls"] = confirm(
            e.get("source_urls"), credible, verified, min_outlets)
    research["timeline"] = timeline
    for side in research.get("sides", []):
        urls = side.get("source_urls") or ([side["source_url"]] if side.get("source_url") else [])
        side["confirmed"], side["outlets"], side["source_urls"] = confirm(urls, credible, verified,
                                                                          min_outlets)
    st_ok, st_outlets, st_urls = confirm(research.get("status_source_urls"), credible, verified,
                                         min_outlets)
    research["status_confirmed"], research["status_outlets"] = st_ok, st_outlets
    research["status_source_urls"] = st_urls
    return research


def confirmed_facts(research):
    """What the script may use: confirmed events (in order), confirmed sides, confirmed status."""
    return {
        "events": [e for e in research.get("timeline", []) if e.get("confirmed")],
        "sides": [s for s in research.get("sides", []) if s.get("confirmed")],
        "status_now_th": research.get("status_now_th") if research.get("status_confirmed") else None,
    }


# ---------------------------------------------------------------- 3. safety gate

def safety_problems(research, extra_texts=()):
    """Reasons to refuse this story (empty list = OK). No override."""
    problems = []
    sc = research.get("safety_check") or {}
    texts = [research.get("topic", ""), research.get("status_now_th", "")]
    texts += [s.get("title", "") for s in research.get("sources", [])]
    texts += [e.get("event_th", "") for e in research.get("timeline", [])]
    texts += [s.get("party", "") + " " + s.get("position_th", "") for s in research.get("sides", [])]
    texts += list(extra_texts)
    hits = sorted({h for t in texts for h in monarchy_hits(t)})
    if sc.get("monarchy"):
        problems.append("monarchy: the research found a link to the monarchy / royal family "
                        "(Section 112) - " + str(sc.get("notes", ""))[:200])
    if hits:
        problems.append("monarchy: monarchy-related words found: " + ", ".join(hits[:10]))
    if sc.get("private_person"):
        problems.append("private person: the story centres on a non-public individual")
    if sc.get("minor_or_victim"):
        problems.append("minor / victim: the story centres on a minor or a crime victim")
    return problems


def script_problems(script, research, min_beats=4, max_beats=7, closing=None):
    """Structural + factual checks on Claude's script (empty list = OK)."""
    problems = []
    ids = {e["id"] for e in confirmed_facts(research)["events"]}
    beats = script.get("beats") or []
    if not (min_beats <= len(beats) <= max_beats):
        problems.append(f"needs {min_beats}-{max_beats} timeline beats, got {len(beats)}")
    for b in beats:
        used = set(b.get("event_ids") or [])
        if not used:
            problems.append(f"beat '{b.get('card_text', '')[:20]}' cites no event")
        elif not used <= ids:
            problems.append(f"beat uses unconfirmed or unknown events: {sorted(used - ids)}")
        if not card_fits(b.get("card_text", "")):
            problems.append(f"card text too long for the card ({card_width(b.get('card_text', ''))} "
                            f"characters, keep it about 40): {b.get('card_text')}")
    hc = script.get("hook_card")
    if not (isinstance(hc, list) and 1 <= len(hc) <= 2 and all(str(x).strip() for x in hc)):
        problems.append("hook_card must be a list of 1-2 short lines (the big opening headline)")
    elif any(card_width(x) > HOOK_LINE for x in hc):
        problems.append(f"hook_card lines must be at most {HOOK_LINE} characters each: {hc}")
    title = script.get("title", "")
    if "สรุป" not in title or len(title) > 70:
        problems.append("title must contain 'สรุป' and be under 70 characters")
    if closing and script.get("closing", "").strip() != closing:
        problems.append("closing line must be exactly: " + closing)
    all_text = " ".join([script.get("hook", ""), " ".join(map(str, hc or [])), title, script.get("closing", ""),
                         (script.get("latest") or {}).get("narration", ""),
                         (script.get("latest") or {}).get("card_text", "")]
                        + [b.get("narration", "") + " " + b.get("card_text", "") for b in beats])
    hits = monarchy_hits(all_text)
    if hits:
        problems.append("monarchy words in the script: " + ", ".join(hits))
    return problems


# ---------------------------------------------------------------- card text / dates

def card_width(text):
    """Visible length of Thai card text (vowel/tone marks above or below don't count)."""
    return sum(0 if unicodedata.category(c) in ("Mn", "Me") else 1 for c in str(text or ""))


CARD_LINE = 22  # Thai characters per card line at the card font size
HOOK_LINE = 13  # Thai characters per line of the big opening headline (hook_card)


def card_fits(text, max_lines=2):
    """True if the card text fits the card: at most max_lines lines of CARD_LINE characters
    (the spec's '~40 characters'), broken between words."""
    from .storylib import wrap_text
    lines = wrap_text(str(text or "").strip(), "th", CARD_LINE)
    return len(lines) <= max_lines and all(card_width(x) <= CARD_LINE for x in lines)


def wrap_card(text, max_chars=20, max_lines=2):
    """Thai card text in at most max_lines lines, broken between words (pythainlp)."""
    from .storylib import wrap_text
    lines = wrap_text(str(text or "").strip(), "th", max_chars)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + ["".join(lines[max_lines - 1:])]
    return lines


TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.",
             "พ.ย.", "ธ.ค."]
_LEAD_DATE = re.compile(r"^\s*(วันที่\s*)?\d{1,2}(\s*[–-]\s*\d{1,2})?\s*(" + "|".join(
    re.escape(m) for m in TH_MONTHS) + r")(\s*\d{2,4})?\s*")


def strip_date(text):
    """'26 ก.ย. กทม. ประกาศ...' -> 'กทม. ประกาศ...' (the card's date pill already shows it)."""
    return _LEAD_DATE.sub("", str(text or ""), count=1).strip()


def thai_date(iso, with_year=False):
    """'2026-09-12' -> '12 ก.ย.' (or '12 ก.ย. 69' with the Buddhist-era year)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(iso or ""))
    if not m:
        return str(iso or "")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    out = f"{d} {TH_MONTHS[mo - 1]}"
    return out + f" {(y + 543) % 100:02d}" if with_year else out


# Thai news shorthand -> what a newsreader says. config.yaml news.pronounce adds / overrides.
SPOKEN = {
    "ม.ค.": "มกราคม", "ก.พ.": "กุมภาพันธ์", "มี.ค.": "มีนาคม", "เม.ย.": "เมษายน", "พ.ค.": "พฤษภาคม",
    "มิ.ย.": "มิถุนายน", "ก.ค.": "กรกฎาคม", "ส.ค.": "สิงหาคม", "ก.ย.": "กันยายน", "ต.ค.": "ตุลาคม",
    "พ.ย.": "พฤศจิกายน", "ธ.ค.": "ธันวาคม",
    "กทม.": "กรุงเทพมหานคร", "กรุงเทพฯ": "กรุงเทพ", "ครม.": "คณะรัฐมนตรี", "ปภ.": "ปอภอ",
    "สทนช.": "สอทอนอชอ", "สปสช.": "สอปอสอชอ", "ส.ส.": "สอสอ", "ส.ว.": "สอวอ", "สว.": "สอวอ",
    "พ.ร.บ.": "พอรอบอ", "รมว.": "รัฐมนตรีว่าการ", "รมช.": "รัฐมนตรีช่วยว่าการ", "ผู้ว่าฯ": "ผู้ว่า",
    "นายกฯ": "นายก", "อปท.": "องค์กรปกครองส่วนท้องถิ่น", "สภ.": "สถานีตำรวจภูธร",
    "ลบ.ม.": "ลูกบาศก์เมตร", "ตร.กม.": "ตารางกิโลเมตร", "ซม.": "เซนติเมตร", "มม.": "มิลลิเมตร",
    "กม.": "กิโลเมตร", "ชม.": "ชั่วโมง", "%": " เปอร์เซ็นต์",
    "Thai PBS": "ไทยพีบีเอส", "Thairath": "ไทยรัฐ", "Khaosod": "ข่าวสด", "Matichon": "มติชน",
    "Bangkok Post": "บางกอกโพสต์", "The Standard": "เดอะสแตนดาร์ด", "Prachachat": "ประชาชาติ",
    "PPTV": "พีพีทีวี", "JICC": "เจไอซีซี", "DGA": "ดีจีเอ",
}
_PLACE = re.compile(r"(?<![ก-๙])(อ|จ|ต)\.(?=\s*[ก-๙])")  # อ.บางบาล / จ.อยุธยา / ต.
_RANGE = re.compile(r"(\d)\s*[–-]\s*(\d)")


def speakable(text, extra=None):
    """News text as it should be spoken: expand abbreviations, month names, units, ranges.
    Longer keys first, so 'ลบ.ม.' wins over 'ม.'."""
    table = {**SPOKEN, **(extra or {})}
    t = str(text or "")
    for k in sorted(table, key=len, reverse=True):
        t = t.replace(k, table[k])
    t = _PLACE.sub(lambda m: {"อ": "อำเภอ", "จ": "จังหวัด", "ต": "ตำบล"}[m.group(1)], t)
    t = _RANGE.sub(r"\1 ถึง \2", t)
    return re.sub(r"[ \t]{2,}", " ", t)


def sources_block(research, used_urls=None):
    """Description source list: outlet - title - date - URL (credible, verified sources)."""
    lines = []
    for s in research.get("sources", []):
        if not (s.get("credible") and s.get("verified")):
            continue
        if used_urls is not None and s.get("url") not in used_urls:
            continue
        date = thai_date(s.get("date"), with_year=True) if s.get("date") else ""
        title = (s.get("title") or "").strip()
        lines.append(f"• {s.get('outlet', '')}: " + (f"{title} " if title else "")
                     + (f"({date}) " if date else "") + s.get("url", ""))
    return "\n".join(lines)
