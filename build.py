"""Build docs/events.json and docs/uo-na.ics from the official uo.com calendar.

Runs on a schedule in GitHub Actions (see .github/workflows/update.yml).
Can also be run locally: pip install requests && python build.py
"""
import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------- config
FEEDS = ["https://uo.com/events/?ical=1"]  # main list (used for discovery only)
SITE_TZ = "America/New_York"               # fallback for floating times
ALERT_MINUTES = 30                         # phone reminder before each event
DAYS_BACK, DAYS_AHEAD = 7, 45
OUT = Path(__file__).parent / "docs"
HEADERS = {"User-Agent": "Mozilla/5.0 (UO NA event calendar; personal use)"}

# Canonical shard -> extra names/abbreviations seen in event titles.
# Keep in sync with the SHARDS list in uo_event_bot.py.
SHARDS = {
    "Atlantic": ["Atl"],
    "Pacific": ["Pac"],
    "Great Lakes": ["GL"],
    "Lake Superior": ["LS"],
    "Baja": [],
    "Chesapeake": ["Chessy", "Ches"],
    "Napa Valley": ["Napa"],
    "Sonoma": [],
    "Catskills": ["Cats"],
    "Lake Austin": ["LA"],
    "Legends": [],
    "Origin": [],
}

# ---------------------------------------------------------------- iCal parsing
_PATTERNS = [
    (name, re.compile(r"(?<![A-Za-z])(" + "|".join(map(re.escape, [name, *alts])) + r")(?![A-Za-z])", re.I))
    for name, alts in SHARDS.items()
]


def match_shard(text):
    hits = [(m.start(), name) for name, rx in _PATTERNS if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def _unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ical(text):
    """Return list of VEVENT dicts: {PROP: (value, {param: val})}."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    events, cur = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            pdict = dict(p.split("=", 1) for p in params if "=" in p)
            cur.setdefault(name.upper(), (value, pdict))
    return events


def _zone(tzid):
    tzid = tzid.strip('"')
    try:
        return ZoneInfo(tzid)
    except Exception:
        m = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", tzid)
        if m:
            sign = 1 if m[1] == "+" else -1
            return dt.timezone(sign * dt.timedelta(hours=int(m[2]), minutes=int(m[3] or 0)))
        return ZoneInfo(SITE_TZ)


def parse_dt(prop):
    """-> (aware UTC datetime or date, all_day)"""
    value, params = prop
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value[:8], "%Y%m%d").date(), True
    if value.endswith("Z"):
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc), False
    local = dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    tz = _zone(params["TZID"]) if "TZID" in params else ZoneInfo(SITE_TZ)
    return local.replace(tzinfo=tz).astimezone(dt.timezone.utc), False


# ---------------------------------------------------------------- fetching
def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def precise(ev):
    """Re-read one event from its own iCal page, which keeps real TZIDs."""
    url = ev.get("URL", ("", {}))[0]
    if not url:
        return ev, True
    try:
        found = parse_ical(get(url.rstrip("/") + "/?ical=1"))
    except Exception as e:
        print(f"  ! {url}: {e}")
        return ev, True
    uid = ev.get("UID", ("", {}))[0]
    best = next((f for f in found if f.get("UID", ("",))[0] == uid), found[0] if found else None)
    return (best, False) if best else (ev, True)


def to_record(ev, approx):
    title = _unescape(ev["SUMMARY"][0]).strip()
    cats = [_unescape(c).strip() for c in ev.get("CATEGORIES", ("", {}))[0].split(",") if c.strip()]
    shard = match_shard(title) or match_shard(" ".join(cats))
    start, all_day = parse_dt(ev["DTSTART"])
    end = parse_dt(ev["DTEND"])[0] if "DTEND" in ev else start
    if all_day:
        s, e = start.isoformat(), end.isoformat()
    else:
        s, e = (x.strftime("%Y-%m-%dT%H:%M:%SZ") for x in (start, end))
    return {
        "uid": ev.get("UID", (title + s, {}))[0],
        "title": title,
        "shard": shard,
        "start": s,
        "end": e,
        "allDay": all_day,
        "url": ev.get("URL", ("", {}))[0],
        "location": _unescape(ev.get("LOCATION", ("", {}))[0]).strip(),
        "tags": cats,
        "approx": approx,
    }


# ---------------------------------------------------------------- output
def _fold(line):
    out, b = [], line.encode()
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(b[:cut].decode())
        b = b" " + b[cut:]
    out.append(b.decode())
    return "\r\n".join(out)


def _esc(v):
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def write_ics(records, path):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//UO NA Events//EN", "CALSCALE:GREGORIAN",
         "X-WR-CALNAME:UO NA Events", "REFRESH-INTERVAL;VALUE=DURATION:PT3H", "X-PUBLISHED-TTL:PT3H"]
    for r in records:
        if r["allDay"]:
            when = [f"DTSTART;VALUE=DATE:{r['start'].replace('-', '')}",
                    f"DTEND;VALUE=DATE:{(dt.date.fromisoformat(r['end']) + dt.timedelta(days=1)).strftime('%Y%m%d') if r['end'] == r['start'] else r['end'].replace('-', '')}"]
        else:
            f = lambda x: x.replace("-", "").replace(":", "")
            when = [f"DTSTART:{f(r['start'])}", f"DTEND:{f(r['end'])}"]
        L += ["BEGIN:VEVENT", f"UID:{_esc(r['uid'])}", f"DTSTAMP:{stamp}", *when,
              f"SUMMARY:{_esc(r['title'])}", f"LOCATION:{_esc(r['location'] or r['shard'] or '')}",
              f"URL:{r['url']}", f"DESCRIPTION:{_esc(r['url'])}",
              "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(r['title'])}",
              f"TRIGGER:-PT{ALERT_MINUTES}M", "END:VALARM", "END:VEVENT"]
    L.append("END:VCALENDAR")
    path.write_text("\r\n".join(_fold(x) for x in L) + "\r\n", encoding="utf-8")


def main():
    raw = {}
    for feed in FEEDS:
        for ev in parse_ical(get(feed)):
            if "SUMMARY" in ev and "DTSTART" in ev:
                raw.setdefault(ev.get("UID", (ev["SUMMARY"][0], {}))[0], ev)

    now = dt.datetime.now(dt.timezone.utc)
    lo, hi = now - dt.timedelta(days=DAYS_BACK), now + dt.timedelta(days=DAYS_AHEAD)
    candidates = []
    for ev in raw.values():
        cats = ev.get("CATEGORIES", ("", {}))[0]
        if not (match_shard(_unescape(ev["SUMMARY"][0])) or match_shard(cats)):
            continue
        start = parse_dt(ev["DTSTART"])[0]
        if isinstance(start, dt.date) and not isinstance(start, dt.datetime):
            start = dt.datetime.combine(start, dt.time(), dt.timezone.utc)
        if lo <= start <= hi:
            candidates.append(ev)
    print(f"{len(raw)} events in feed, {len(candidates)} on NA shards in window")

    with ThreadPoolExecutor(8) as pool:
        records = [to_record(ev, approx) for ev, approx in pool.map(precise, candidates)]
    records = [r for r in records if r["shard"]]
    records.sort(key=lambda r: (r["start"], r["shard"]))

    OUT.mkdir(exist_ok=True)
    (OUT / "events.json").write_text(json.dumps({
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shards": list(SHARDS),
        "events": records,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    write_ics(records, OUT / "uo-na.ics")
    print(f"wrote {len(records)} events")


if __name__ == "__main__":
    main()
