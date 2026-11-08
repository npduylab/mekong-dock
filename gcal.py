# -*- coding: utf-8 -*-
"""Doc Google Calendar qua dia chi iCal (.ics) - khong can dang nhap/OAuth.

- Lich cong khai (vd Ngay le Viet Nam) co san dia chi.
- Lich rieng: Google Calendar > Cai dat lich > "Dia chi bi mat o dinh dang iCal".
Ho tro su kien lap lai (RRULE: DAILY/WEEKLY/MONTHLY/YEARLY, INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY,
BYMONTH), EXDATE, RECURRENCE-ID (lan bi sua rieng), mui gio TZID. Chi dung thu vien chuan.
"""
import datetime as dt
import re
import urllib.request
from dataclasses import dataclass
from zoneinfo import ZoneInfo

HOLIDAYS_VN = ("https://calendar.google.com/calendar/ical/"
               "vi.vietnamese%23holiday%40group.v.calendar.google.com/public/basic.ics")
LOCAL = dt.datetime.now().astimezone().tzinfo
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


@dataclass
class Event:
    start: dt.datetime        # gio dia phuong, khong tzinfo; ca ngay = 00:00
    end: dt.datetime
    all_day: bool
    title: str
    location: str = ""
    calendar: str = ""


def fetch(url: str, timeout: int = 20) -> str:
    url = re.sub(r"^webcal://", "https://", url.strip())
    req = urllib.request.Request(url, headers={"User-Agent": "MekongDock/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _prop(line: str) -> tuple[str, dict, str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    p = {}
    for x in params:
        k, _, v = x.partition("=")
        p[k.upper()] = v.strip('"')
    return name.upper(), p, value


def _text(v: str) -> str:
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _when(value: str, params: dict) -> tuple[dt.datetime, bool]:
    """-> (gio dia phuong naive, ca_ngay)"""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return dt.datetime.strptime(v[:8], "%Y%m%d"), True
    t = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        t = t.replace(tzinfo=dt.timezone.utc)
    elif params.get("TZID"):
        try:
            t = t.replace(tzinfo=ZoneInfo(params["TZID"]))
        except Exception:
            return t, False  # khong biet mui gio: coi la gio dia phuong
    else:
        return t, False      # "floating time"
    return t.astimezone(LOCAL).replace(tzinfo=None), False


def _duration(v: str) -> dt.timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return dt.timedelta()
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    td = dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -td if m.group(1) == "-" else td


def _nth_weekday(year: int, month: int, wd: int, n: int) -> dt.date | None:
    """n=1 thu (wd) dau tien, n=-1 cuoi cung trong thang."""
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    else:
        nxt = dt.date(year + month // 12, month % 12 + 1, 1)
        d = nxt - dt.timedelta(days=1)
        d -= dt.timedelta(days=(d.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == month else None


def _expand(start: dt.datetime, rule: str, until_window: dt.datetime) -> list[dt.datetime]:
    r = dict(x.split("=", 1) for x in rule.split(";") if "=" in x)
    freq, interval = r.get("FREQ", ""), int(r.get("INTERVAL", 1))
    count = int(r["COUNT"]) if "COUNT" in r else None
    until = _when(r["UNTIL"], {})[0] if "UNTIL" in r else None
    if until and "T" not in r["UNTIL"]:
        until += dt.timedelta(days=1) - dt.timedelta(seconds=1)
    byday = [(int(m.group(1) or 0), DAYS.index(m.group(2)))
             for m in (re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", x) for x in r.get("BYDAY", "").split(",") if x) if m]
    bymd = [int(x) for x in r.get("BYMONTHDAY", "").split(",") if x]
    bymonth = [int(x) for x in r.get("BYMONTH", "").split(",") if x] or [start.month]
    tod = start.time()
    out: list[dt.datetime] = []
    n = 0

    def take(cands) -> bool:
        nonlocal n
        for c in sorted(cands):
            if c < start:
                continue
            if (until and c > until) or (count is not None and n >= count) or c > until_window:
                return False
            n += 1
            out.append(c)
        return True

    for step in range(5000):
        if freq == "DAILY":
            cands = [start + dt.timedelta(days=step * interval)]
        elif freq == "WEEKLY":
            monday = start.date() - dt.timedelta(days=start.weekday()) + dt.timedelta(weeks=step * interval)
            wds = [wd for _, wd in byday] or [start.weekday()]
            cands = [dt.datetime.combine(monday + dt.timedelta(days=wd), tod) for wd in wds]
        elif freq == "MONTHLY":
            y, m = divmod(start.month - 1 + step * interval, 12)
            y, m = start.year + y, m + 1
            days: list[dt.date] = []
            if bymd:
                for md in bymd:
                    try:
                        days.append(dt.date(y, m, md) if md > 0 else
                                    dt.date(y + m // 12, m % 12 + 1, 1) + dt.timedelta(days=md))
                    except ValueError:
                        pass
            elif byday:
                for nth, wd in byday:
                    if nth:
                        d = _nth_weekday(y, m, wd, nth)
                        if d:
                            days.append(d)
                    else:  # moi thu (wd) trong thang
                        d = _nth_weekday(y, m, wd, 1)
                        while d and d.month == m:
                            days.append(d)
                            d += dt.timedelta(days=7)
            else:
                try:
                    days.append(dt.date(y, m, start.day))
                except ValueError:
                    pass
            cands = [dt.datetime.combine(d, tod) for d in days]
        elif freq == "YEARLY":
            y = start.year + step * interval
            cands = []
            for m in bymonth:
                if byday:
                    for nth, wd in byday:
                        d = _nth_weekday(y, m, wd, nth or 1)
                        if d:
                            cands.append(dt.datetime.combine(d, tod))
                else:
                    try:
                        cands.append(dt.datetime.combine(dt.date(y, m, start.day), tod))
                    except ValueError:
                        pass  # 29/2 nam khong nhuan
        else:
            return [start]
        if not take(cands):
            break
    return out


def parse(text: str, window_start: dt.datetime, window_end: dt.datetime, calendar: str = "") -> list[Event]:
    masters, overrides, cal_name = [], [], calendar
    cur: dict | None = None
    for line in _unfold(text):
        name, params, value = _prop(line)
        if name == "X-WR-CALNAME" and not calendar:
            cal_name = _text(value)
        elif name == "BEGIN" and value == "VEVENT":
            cur = {"exdate": set()}
        elif name == "END" and value == "VEVENT" and cur is not None:
            if cur.get("start") and cur.get("status") != "CANCELLED":
                (overrides if "recurrence_id" in cur else masters).append(cur)
            cur = None
        elif cur is not None:
            if name == "DTSTART":
                cur["start"], cur["all_day"] = _when(value, params)
            elif name == "DTEND":
                cur["end"] = _when(value, params)[0]
            elif name == "DURATION":
                cur["duration"] = _duration(value)
            elif name == "SUMMARY":
                cur["title"] = _text(value)
            elif name == "LOCATION":
                cur["location"] = _text(value)
            elif name == "RRULE":
                cur["rrule"] = value
            elif name == "EXDATE":
                for v in value.split(","):
                    cur["exdate"].add(_when(v, params)[0])
            elif name == "RECURRENCE-ID":
                cur["recurrence_id"] = _when(value, params)[0]
            elif name == "UID":
                cur["uid"] = value
            elif name == "STATUS":
                cur["status"] = value.upper()

    replaced = {(o.get("uid"), o["recurrence_id"]) for o in overrides}
    events: list[Event] = []

    def add(e: dict, start: dt.datetime):
        length = (e["end"] - e["start"]) if e.get("end") else e.get("duration") or \
            (dt.timedelta(days=1) if e.get("all_day") else dt.timedelta())
        end = start + length
        if end > window_start and start < window_end or (start == end and window_start <= start < window_end):
            events.append(Event(start, end, bool(e.get("all_day")), e.get("title") or "(Không tiêu đề)",
                                e.get("location", ""), cal_name))

    for e in masters:
        starts = _expand(e["start"], e["rrule"], window_end) if e.get("rrule") else [e["start"]]
        for s in starts:
            if s in e["exdate"] or (e.get("uid"), s) in replaced:
                continue
            add(e, s)
    for o in overrides:
        add(o, o["start"])
    return sorted(events, key=lambda ev: (ev.start, not ev.all_day, ev.title))


def upcoming(urls: list[str], days: int = 14) -> tuple[list[Event], list[str]]:
    """Su kien tu dau hom nay den `days` ngay toi tu nhieu lich. Tra ve (su kien, loi)."""
    t0 = dt.datetime.combine(dt.date.today(), dt.time())
    t1 = t0 + dt.timedelta(days=days)
    events, errors = [], []
    for url in urls:
        try:
            events += parse(fetch(url), t0, t1)
        except Exception as e:  # mot lich loi khong lam mat cac lich khac
            errors.append(f"{url[:60]}…: {e}")
    return sorted(events, key=lambda ev: (ev.start, not ev.all_day, ev.title)), errors
