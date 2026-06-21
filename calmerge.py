#!/usr/bin/env python3
"""iCalendar (.ics) Parser, Conflict Detector, and Merger CLI."""

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional


RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def red(text: str) -> str:
    return f"{RED}{text}{RESET}"


def green(text: str) -> str:
    return f"{GREEN}{text}{RESET}"


def yellow(text: str) -> str:
    return f"{YELLOW}{text}{RESET}"


def blue(text: str) -> str:
    return f"{BLUE}{text}{RESET}"


def bold(text: str) -> str:
    return f"{BOLD}{text}{RESET}"


@dataclass
class VEvent:
    uid: str = ""
    dtstart: Optional[datetime] = None
    dtend: Optional[datetime] = None
    summary: str = ""
    location: str = ""
    description: str = ""
    dtstamp: Optional[datetime] = None
    rrule: dict = field(default_factory=dict)
    exdate: list = field(default_factory=list)
    alarms: list = field(default_factory=list)
    transp: str = "OPAQUE"
    all_day: bool = False
    _raw_rrule: str = ""
    dtstart_tzid: str = ""
    dtend_tzid: str = ""
    dtstamp_tzid: str = ""


@dataclass
class VTodo:
    uid: str = ""
    summary: str = ""
    description: str = ""
    due: Optional[datetime] = None
    dtstamp: Optional[datetime] = None
    status: str = "NEEDS-ACTION"
    percent_complete: int = 0
    due_tzid: str = ""
    dtstamp_tzid: str = ""


@dataclass
class VAlarm:
    action: str = ""
    trigger: str = ""
    description: str = ""
    summary: str = ""


@dataclass
class TZTransition:
    dtstart: Optional[datetime] = None
    tzoffsetfrom: str = ""
    tzoffsetto: str = ""
    tzname: str = ""


@dataclass
class VTimezone:
    tzid: str = ""
    standard: Optional[TZTransition] = None
    daylight: Optional[TZTransition] = None


@dataclass
class VCalendar:
    events: list = field(default_factory=list)
    todos: list = field(default_factory=list)
    timezones: dict = field(default_factory=dict)
    source: str = ""


def parse_ics_value(value: str) -> str:
    result = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n":
                result.append("\n")
            elif nxt == "N":
                result.append("\n")
            elif nxt == ",":
                result.append(",")
            elif nxt == ";":
                result.append(";")
            elif nxt == "\\":
                result.append("\\")
            else:
                result.append(nxt)
            i += 2
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def unfold_lines(lines: list) -> list:
    unfolded = []
    for line in lines:
        line = line.rstrip("\r\n").rstrip("\n")
        if not line:
            continue
        if line.startswith(" ") or line.startswith("\t"):
            if unfolded:
                unfolded[-1] += line[1:]
            else:
                unfolded.append(line[1:])
        else:
            unfolded.append(line)
    return unfolded


def parse_tz_offset(offset_str: str) -> Optional[timedelta]:
    m = re.match(r"^([+-])(\d{2})(\d{2})(\d{2})?$", offset_str)
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3))
    seconds = int(m.group(4) or "00")
    return sign * timedelta(hours=hours, minutes=minutes, seconds=seconds)


def get_timezone_offset(tzid: str, dt: datetime, timezones: dict) -> Optional[timedelta]:
    if not tzid or not timezones:
        return None
    tz = timezones.get(tzid)
    if not tz:
        return None
    if not tz.daylight or not tz.standard:
        if tz.standard and tz.standard.tzoffsetto:
            return parse_tz_offset(tz.standard.tzoffsetto)
        if tz.daylight and tz.daylight.tzoffsetto:
            return parse_tz_offset(tz.daylight.tzoffsetto)
        return None
    dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    std_start = tz.standard.dtstart.replace(tzinfo=None) if tz.standard.dtstart else None
    dst_start = tz.daylight.dtstart.replace(tzinfo=None) if tz.daylight.dtstart else None
    if std_start and dst_start:
        dt_year = dt_naive.year
        std_month = std_start.month
        dst_month = dst_start.month
        if std_month < dst_month:
            is_dst = (dt_naive.month, dt_naive.day) >= (dst_start.month, dst_start.day) and \
                     (dt_naive.month, dt_naive.day) < (std_start.month, std_start.day)
        else:
            is_dst = not ((dt_naive.month, dt_naive.day) >= (std_start.month, std_start.day) and
                          (dt_naive.month, dt_naive.day) < (dst_start.month, dst_start.day))
    else:
        is_dst = False
    if is_dst:
        offset_str = tz.daylight.tzoffsetto
    else:
        offset_str = tz.standard.tzoffsetto
    return parse_tz_offset(offset_str)


def parse_datetime(value: str, params: dict = None, timezones: dict = None) -> Optional[datetime]:
    params = params or {}
    timezones = timezones or {}
    value = value.strip()
    if "VALUE" in params and params["VALUE"] == "DATE":
        try:
            d = datetime.strptime(value, "%Y%m%d")
            return d.replace(hour=0, minute=0, second=0)
        except ValueError:
            return None
    is_utc = value.endswith("Z")
    fmt_without_z = "%Y%m%dT%H%M%S"
    try:
        dt = datetime.strptime(value.rstrip("Z"), fmt_without_z)
        if is_utc:
            dt = dt.replace(tzinfo=timezone.utc)
        elif "TZID" in params:
            tzid = params["TZID"]
            offset = get_timezone_offset(tzid, dt, timezones)
            if offset is not None:
                tz = timezone(offset)
                dt = dt.replace(tzinfo=tz)
        return dt
    except ValueError:
        pass
    try:
        dt = datetime.strptime(value, "%Y%m%d")
        return dt.replace(hour=0, minute=0, second=0)
    except ValueError:
        pass
    return None


def parse_content_line(line: str) -> tuple:
    if ":" not in line:
        return None, {}, ""
    colon_idx = line.find(":")
    left = line[:colon_idx]
    value = line[colon_idx + 1:]
    parts = left.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v
    return name, params, value


def parse_rrule(value: str, timezones: dict = None) -> dict:
    timezones = timezones or {}
    result = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.upper()
            if k == "BYDAY":
                result[k] = v.split(",")
            elif k in ("COUNT", "INTERVAL"):
                try:
                    result[k] = int(v)
                except ValueError:
                    result[k] = v
            elif k == "UNTIL":
                dt = parse_datetime(v, timezones=timezones)
                if dt:
                    result[k] = dt
                else:
                    result[k] = v
            else:
                result[k] = v
    return result


def _parse_timezones(lines: list) -> dict:
    timezones = {}
    i = 0
    current_tz = None
    current_transition = None
    in_standard = False
    in_daylight = False

    while i < len(lines):
        line = lines[i]
        name, params, value = parse_content_line(line)
        if name is None:
            i += 1
            continue
        if name == "BEGIN":
            comp = value.upper()
            if comp == "VTIMEZONE":
                current_tz = VTimezone()
            elif comp == "STANDARD" and current_tz is not None:
                current_transition = TZTransition()
                in_standard = True
            elif comp == "DAYLIGHT" and current_tz is not None:
                current_transition = TZTransition()
                in_daylight = True
        elif name == "END":
            comp = value.upper()
            if comp == "VTIMEZONE" and current_tz is not None:
                if current_tz.tzid:
                    timezones[current_tz.tzid] = current_tz
                current_tz = None
            elif comp == "STANDARD" and current_tz is not None:
                if current_transition:
                    current_tz.standard = current_transition
                current_transition = None
                in_standard = False
            elif comp == "DAYLIGHT" and current_tz is not None:
                if current_transition:
                    current_tz.daylight = current_transition
                current_transition = None
                in_daylight = False
        elif name == "TZID":
            if current_tz is not None:
                current_tz.tzid = value
        elif name == "TZOFFSETFROM":
            if current_transition is not None:
                current_transition.tzoffsetfrom = value
        elif name == "TZOFFSETTO":
            if current_transition is not None:
                current_transition.tzoffsetto = value
        elif name == "TZNAME":
            if current_transition is not None:
                current_transition.tzname = value
        elif name == "DTSTART":
            if current_transition is not None:
                current_transition.dtstart = parse_datetime(value, params, timezones)
        i += 1
    return timezones


def parse_ics(text: str, source: str = "") -> VCalendar:
    cal = VCalendar(source=source)
    lines = unfold_lines(text.splitlines())

    cal.timezones = _parse_timezones(lines)

    i = 0
    current_event = None
    current_todo = None
    current_alarm = None
    stack = []
    while i < len(lines):
        line = lines[i]
        name, params, value = parse_content_line(line)
        if name is None:
            i += 1
            continue
        if name == "BEGIN":
            stack.append(value.upper())
            if value.upper() == "VEVENT":
                current_event = VEvent()
            elif value.upper() == "VTODO":
                current_todo = VTodo()
            elif value.upper() == "VALARM":
                current_alarm = VAlarm()
        elif name == "END":
            ended = value.upper()
            if stack and stack[-1] == ended:
                stack.pop()
            if ended == "VEVENT" and current_event:
                cal.events.append(current_event)
                current_event = None
            elif ended == "VTODO" and current_todo:
                cal.todos.append(current_todo)
                current_todo = None
            elif ended == "VALARM" and current_alarm:
                if current_event is not None:
                    current_event.alarms.append(current_alarm)
                current_alarm = None
        elif name == "UID":
            if current_event is not None:
                current_event.uid = parse_ics_value(value)
            elif current_todo is not None:
                current_todo.uid = parse_ics_value(value)
        elif name == "SUMMARY":
            if current_alarm is not None:
                current_alarm.summary = parse_ics_value(value)
            elif current_todo is not None:
                current_todo.summary = parse_ics_value(value)
            elif current_event is not None:
                current_event.summary = parse_ics_value(value)
        elif name == "LOCATION":
            if current_event is not None and current_alarm is None:
                current_event.location = parse_ics_value(value)
        elif name == "DESCRIPTION":
            if current_alarm is not None:
                current_alarm.description = parse_ics_value(value)
            elif current_todo is not None:
                current_todo.description = parse_ics_value(value)
            elif current_event is not None:
                current_event.description = parse_ics_value(value)
        elif name == "DTSTART":
            if current_event is not None:
                current_event.dtstart = parse_datetime(value, params, cal.timezones)
                if "TZID" in params:
                    current_event.dtstart_tzid = params["TZID"]
                if "VALUE" in params and params["VALUE"] == "DATE":
                    current_event.all_day = True
        elif name == "DTEND":
            if current_event is not None:
                current_event.dtend = parse_datetime(value, params, cal.timezones)
                if "TZID" in params:
                    current_event.dtend_tzid = params["TZID"]
                if "VALUE" in params and params["VALUE"] == "DATE":
                    current_event.all_day = True
        elif name == "DUE":
            if current_todo is not None:
                current_todo.due = parse_datetime(value, params, cal.timezones)
                if "TZID" in params:
                    current_todo.due_tzid = params["TZID"]
        elif name == "DTSTAMP":
            dt = parse_datetime(value, params, cal.timezones)
            if current_event is not None:
                current_event.dtstamp = dt
                if "TZID" in params:
                    current_event.dtstamp_tzid = params["TZID"]
            elif current_todo is not None:
                current_todo.dtstamp = dt
                if "TZID" in params:
                    current_todo.dtstamp_tzid = params["TZID"]
        elif name == "RRULE":
            if current_event is not None:
                current_event.rrule = parse_rrule(value, cal.timezones)
                current_event._raw_rrule = value
        elif name == "EXDATE":
            if current_event is not None:
                for v in value.split(","):
                    dt = parse_datetime(v.strip(), params, cal.timezones)
                    if dt:
                        current_event.exdate.append(dt)
        elif name == "TRANSP":
            if current_event is not None:
                current_event.transp = value.upper()
        elif name == "STATUS":
            if current_todo is not None:
                current_todo.status = value.upper()
        elif name == "PERCENT-COMPLETE":
            if current_todo is not None:
                try:
                    current_todo.percent_complete = int(value)
                except ValueError:
                    pass
        elif name == "ACTION":
            if current_alarm is not None:
                current_alarm.action = value.upper()
        elif name == "TRIGGER":
            if current_alarm is not None:
                current_alarm.trigger = value
        i += 1
    return cal


def load_ics_file(path: str) -> VCalendar:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse_ics(text, source=path)


WEEKDAY_MAP = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _parse_byday_parts(byday_list: list) -> list:
    result = []
    for item in byday_list:
        match = re.match(r"^(-?\d+)?(MO|TU|WE|TH|FR|SA|SU)$", item.upper())
        if match:
            n = int(match.group(1)) if match.group(1) else None
            wd = WEEKDAY_MAP[match.group(2)]
            result.append((n, wd))
    return result


def _weekday_of_month(d: date) -> int:
    return (d.day - 1) // 7 + 1


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    diff = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=diff)


def expand_rrule(event: VEvent, range_start: date, range_end: date) -> list:
    occurrences = []
    if not event.dtstart:
        return occurrences
    if not event.rrule:
        dt = event.dtstart
        d = dt.date() if isinstance(dt, datetime) else dt
        if range_start <= d <= range_end:
            occurrences.append(event)
        return occurrences

    rrule = event.rrule
    freq = rrule.get("FREQ", "DAILY")
    interval = rrule.get("INTERVAL", 1)
    count = rrule.get("COUNT")
    until = rrule.get("UNTIL")
    byday_raw = rrule.get("BYDAY", [])
    byday_parts = _parse_byday_parts(byday_raw)

    duration = timedelta(0)
    if event.dtend and event.dtstart:
        duration = event.dtend - event.dtstart

    start_tz = event.dtstart.tzinfo if event.dtstart and event.dtstart.tzinfo else None
    start_dt = event.dtstart.replace(tzinfo=None) if event.dtstart.tzinfo else event.dtstart
    start_time = start_dt.time()
    start_date = start_dt.date()
    start_weekday = start_date.weekday()

    until_date = None
    if until is not None:
        until_naive = until.replace(tzinfo=None) if until.tzinfo else until
        until_date = until_naive.date()

    generated = 0
    max_occurrences = count if count else 2000
    max_iterations = 10000
    iterations = 0

    def make_occurrence(d: date) -> Optional[VEvent]:
        nonlocal generated
        if count is not None and generated >= count:
            return None
        if until_date is not None and d > until_date:
            return None
        if d < range_start or d > range_end:
            return None
        dt = datetime.combine(d, start_time)
        if start_tz is not None:
            dt = dt.replace(tzinfo=start_tz)
        exdate_match = False
        for ex in event.exdate:
            ex_naive = ex.replace(tzinfo=None) if ex.tzinfo else ex
            if ex_naive.date() == d and ex_naive.time() == start_time:
                exdate_match = True
                break
        if exdate_match:
            return None
        dt_end = dt + duration if event.dtend else None
        new_ev = VEvent(
            uid=event.uid,
            summary=event.summary,
            location=event.location,
            description=event.description,
            dtstart=dt,
            dtend=dt_end,
            dtstamp=event.dtstamp,
            all_day=event.all_day,
            transp=event.transp,
            alarms=event.alarms,
            rrule=event.rrule,
            _raw_rrule=event._raw_rrule,
            exdate=event.exdate,
            dtstart_tzid=event.dtstart_tzid,
            dtend_tzid=event.dtend_tzid,
            dtstamp_tzid=event.dtstamp_tzid,
        )
        generated += 1
        return new_ev

    if freq == "DAILY":
        current = start_date
        while iterations < max_iterations and generated < max_occurrences:
            iterations += 1
            if current > range_end:
                break
            if until_date and current > until_date:
                break
            ev = make_occurrence(current)
            if ev:
                occurrences.append(ev)
            current += timedelta(days=interval)

    elif freq == "WEEKLY":
        week_start = start_date - timedelta(days=start_weekday)
        week_counter = 0

        def get_week_dates(week_base: date) -> list:
            dates = []
            if byday_parts:
                for _, wd in sorted(byday_parts, key=lambda x: x[1]):
                    d = week_base + timedelta(days=wd)
                    if d >= start_date:
                        dates.append(d)
            else:
                if week_base + timedelta(days=start_weekday) >= start_date:
                    dates.append(week_base + timedelta(days=start_weekday))
            return sorted(dates)

        while iterations < max_iterations and generated < max_occurrences:
            iterations += 1
            week_dates = get_week_dates(week_start)
            if not week_dates and week_start < start_date:
                week_start += timedelta(weeks=interval)
                week_counter += interval
                continue

            has_after = False
            for d in week_dates:
                if d > range_end:
                    has_after = True
                    break
                if until_date and d > until_date:
                    has_after = True
                    break
                ev = make_occurrence(d)
                if ev:
                    occurrences.append(ev)
            if has_after:
                break

            week_start += timedelta(weeks=interval)
            week_counter += interval

            if count and generated >= count:
                break

    elif freq == "MONTHLY":
        current_year = start_date.year
        current_month = start_date.month
        month_counter = 0

        def get_month_dates(year: int, month: int) -> list:
            dates = []
            if byday_parts:
                for nth, wd in byday_parts:
                    if nth is not None:
                        if nth > 0:
                            first_of_month = date(year, month, 1)
                            first_wd = first_of_month.weekday()
                            days_until = (wd - first_wd) % 7
                            target = first_of_month + timedelta(days=days_until + (nth - 1) * 7)
                            if target.month == month:
                                dates.append(target)
                        else:
                            last_date = _last_weekday_of_month(year, month, wd)
                            abs_nth = abs(nth)
                            target = last_date - timedelta(days=(abs_nth - 1) * 7)
                            if target.month == month and target >= date(year, month, 1):
                                dates.append(target)
                    else:
                        first_of_month = date(year, month, 1)
                        first_wd = first_of_month.weekday()
                        days_until = (wd - first_wd) % 7
                        d = first_of_month + timedelta(days=days_until)
                        while d.month == month:
                            dates.append(d)
                            d += timedelta(days=7)
            else:
                try:
                    d = date(year, month, start_date.day)
                    dates.append(d)
                except ValueError:
                    pass
            return sorted([d for d in dates if d >= start_date or (d.year, d.month) != (start_date.year, start_date.month)])

        while iterations < max_iterations and generated < max_occurrences:
            iterations += 1
            month_dates = get_month_dates(current_year, current_month)

            has_after = False
            for d in sorted(month_dates):
                if d > range_end:
                    has_after = True
                    break
                if until_date and d > until_date:
                    has_after = True
                    break
                if d >= start_date:
                    ev = make_occurrence(d)
                    if ev:
                        occurrences.append(ev)
            if has_after:
                break

            for _ in range(interval):
                current_month += 1
                if current_month > 12:
                    current_month = 1
                    current_year += 1
            month_counter += interval

            if count and generated >= count:
                break

            if current_year > range_end.year + 5:
                break

    else:
        current = start_date
        while iterations < max_iterations and generated < max_occurrences:
            iterations += 1
            if current > range_end:
                break
            ev = make_occurrence(current)
            if ev:
                occurrences.append(ev)
            current += timedelta(days=1)

    return occurrences


def to_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def detect_conflicts(calendars: list, ignore_all_day: bool = False, only_busy: bool = False) -> list:
    all_events = []
    for cal in calendars:
        for ev in cal.events:
            if ev.dtstart and ev.dtend:
                if ignore_all_day and ev.all_day:
                    continue
                if only_busy and ev.transp == "TRANSPARENT":
                    continue
                all_events.append((ev, cal.source))

    all_events.sort(key=lambda x: to_utc_naive(x[0].dtstart) or datetime.min)
    conflicts = []

    for i in range(len(all_events)):
        ev1, src1 = all_events[i]
        ev1_start = to_utc_naive(ev1.dtstart)
        ev1_end = to_utc_naive(ev1.dtend)
        for j in range(i + 1, len(all_events)):
            ev2, src2 = all_events[j]
            ev2_start = to_utc_naive(ev2.dtstart)
            ev2_end = to_utc_naive(ev2.dtend)
            if ev2_start >= ev1_end:
                break
            if src1 == src2 and ev1.uid == ev2.uid:
                continue
            overlap_start = max(ev1_start, ev2_start)
            overlap_end = min(ev1_end, ev2_end)
            overlap_duration = overlap_end - overlap_start
            if overlap_duration.total_seconds() > 0:
                conflicts.append({
                    "event1": ev1,
                    "source1": src1,
                    "event2": ev2,
                    "source2": src2,
                    "overlap_start": overlap_start,
                    "overlap_end": overlap_end,
                    "overlap_duration": overlap_duration,
                })
    return conflicts


def merge_calendars(calendars: list) -> tuple:
    merged = VCalendar(source="merged")
    conflicts_log = []

    for cal in calendars:
        for tzid, tz in cal.timezones.items():
            if tzid not in merged.timezones:
                merged.timezones[tzid] = tz

    event_map = {}
    for cal in calendars:
        for ev in cal.events:
            if not ev.uid:
                merged.events.append(ev)
                continue
            if ev.uid not in event_map:
                event_map[ev.uid] = (ev, cal.source)
            else:
                existing_ev, existing_src = event_map[ev.uid]
                existing_ts = existing_ev.dtstamp or datetime.min
                new_ts = ev.dtstamp or datetime.min
                if new_ts > existing_ts:
                    conflicts_log.append({
                        "uid": ev.uid,
                        "summary": ev.summary,
                        "kept": cal.source,
                        "discarded": existing_src,
                        "reason": "newer DTSTAMP",
                    })
                    event_map[ev.uid] = (ev, cal.source)
                elif new_ts == existing_ts and id(existing_ev) != id(ev):
                    conflicts_log.append({
                        "uid": ev.uid,
                        "summary": ev.summary,
                        "kept": existing_src,
                        "discarded": cal.source,
                        "reason": "same DTSTAMP, kept first",
                    })

    for ev, _ in event_map.values():
        merged.events.append(ev)

    todo_map = {}
    for cal in calendars:
        for td in cal.todos:
            if not td.uid:
                merged.todos.append(td)
                continue
            if td.uid not in todo_map:
                todo_map[td.uid] = td
            else:
                existing_td = todo_map[td.uid]
                existing_ts = existing_td.dtstamp or datetime.min
                new_ts = td.dtstamp or datetime.min
                if new_ts > existing_ts:
                    todo_map[td.uid] = td

    for td in todo_map.values():
        merged.todos.append(td)

    return merged, conflicts_log


def to_ics_format(dt: datetime, all_day: bool = False, tzid: str = "") -> str:
    if all_day:
        return dt.strftime("%Y%m%d")
    if tzid:
        return dt.strftime("%Y%m%dT%H%M%S")
    if dt.tzinfo:
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


def _datetime_to_local_str(dt: datetime, tzid: str, timezones: dict) -> str:
    if tzid and tzid in timezones:
        offset = get_timezone_offset(tzid, dt, timezones)
        if offset is not None:
            local_dt = dt.astimezone(timezone(offset))
            return local_dt.strftime("%Y%m%dT%H%M%S")
    if dt.tzinfo:
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


def _format_dt_field(field_name: str, dt: Optional[datetime], tzid: str = "",
                     all_day: bool = False, timezones: dict = None) -> str:
    if dt is None:
        return ""
    timezones = timezones or {}
    if all_day:
        return f"{field_name};VALUE=DATE:{to_ics_format(dt, True)}"
    if tzid and tzid in timezones:
        local_str = _datetime_to_local_str(dt, tzid, timezones)
        return f"{field_name};TZID={tzid}:{local_str}"
    return f"{field_name}:{to_ics_format(dt)}"


def _export_timezone(tzid: str, tz: VTimezone) -> list:
    lines = [f"BEGIN:VTIMEZONE", f"TZID:{tzid}"]
    if tz.standard:
        lines.append("BEGIN:STANDARD")
        if tz.standard.dtstart:
            lines.append(f"DTSTART:{to_ics_format(tz.standard.dtstart)}")
        if tz.standard.tzoffsetfrom:
            lines.append(f"TZOFFSETFROM:{tz.standard.tzoffsetfrom}")
        if tz.standard.tzoffsetto:
            lines.append(f"TZOFFSETTO:{tz.standard.tzoffsetto}")
        if tz.standard.tzname:
            lines.append(f"TZNAME:{tz.standard.tzname}")
        lines.append("END:STANDARD")
    if tz.daylight:
        lines.append("BEGIN:DAYLIGHT")
        if tz.daylight.dtstart:
            lines.append(f"DTSTART:{to_ics_format(tz.daylight.dtstart)}")
        if tz.daylight.tzoffsetfrom:
            lines.append(f"TZOFFSETFROM:{tz.daylight.tzoffsetfrom}")
        if tz.daylight.tzoffsetto:
            lines.append(f"TZOFFSETTO:{tz.daylight.tzoffsetto}")
        if tz.daylight.tzname:
            lines.append(f"TZNAME:{tz.daylight.tzname}")
        lines.append("END:DAYLIGHT")
    lines.append("END:VTIMEZONE")
    return lines


def escape_ics_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def export_to_ics(cal: VCalendar, output_path: str):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//calmerge//CLI//EN"]

    for tzid, tz in cal.timezones.items():
        lines.extend(_export_timezone(tzid, tz))

    for ev in cal.events:
        lines.append("BEGIN:VEVENT")
        if ev.uid:
            lines.append(f"UID:{escape_ics_value(ev.uid)}")
        if ev.dtstart:
            lines.append(_format_dt_field("DTSTART", ev.dtstart, ev.dtstart_tzid,
                                          ev.all_day, cal.timezones))
        if ev.dtend:
            lines.append(_format_dt_field("DTEND", ev.dtend, ev.dtend_tzid,
                                          ev.all_day, cal.timezones))
        if ev.dtstamp:
            lines.append(_format_dt_field("DTSTAMP", ev.dtstamp, ev.dtstamp_tzid,
                                          False, cal.timezones))
        if ev.summary:
            lines.append(f"SUMMARY:{escape_ics_value(ev.summary)}")
        if ev.location:
            lines.append(f"LOCATION:{escape_ics_value(ev.location)}")
        if ev.description:
            lines.append(f"DESCRIPTION:{escape_ics_value(ev.description)}")
        if ev._raw_rrule:
            lines.append(f"RRULE:{ev._raw_rrule}")
        if ev.transp:
            lines.append(f"TRANSP:{ev.transp}")
        for ex in ev.exdate:
            lines.append(f"EXDATE:{to_ics_format(ex)}")
        for alarm in ev.alarms:
            lines.append("BEGIN:VALARM")
            if alarm.action:
                lines.append(f"ACTION:{alarm.action}")
            if alarm.trigger:
                lines.append(f"TRIGGER:{alarm.trigger}")
            if alarm.description:
                lines.append(f"DESCRIPTION:{escape_ics_value(alarm.description)}")
            if alarm.summary:
                lines.append(f"SUMMARY:{escape_ics_value(alarm.summary)}")
            lines.append("END:VALARM")
        lines.append("END:VEVENT")
    for td in cal.todos:
        lines.append("BEGIN:VTODO")
        if td.uid:
            lines.append(f"UID:{escape_ics_value(td.uid)}")
        if td.summary:
            lines.append(f"SUMMARY:{escape_ics_value(td.summary)}")
        if td.description:
            lines.append(f"DESCRIPTION:{escape_ics_value(td.description)}")
        if td.due:
            lines.append(_format_dt_field("DUE", td.due, td.due_tzid,
                                          False, cal.timezones))
        if td.dtstamp:
            lines.append(_format_dt_field("DTSTAMP", td.dtstamp, td.dtstamp_tzid,
                                          False, cal.timezones))
        if td.status:
            lines.append(f"STATUS:{td.status}")
        lines.append(f"PERCENT-COMPLETE:{td.percent_complete}")
        lines.append("END:VTODO")
    lines.append("END:VCALENDAR")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")


def list_reminders(calendars: list, days: int = 7) -> list:
    now = datetime.now()
    end_date = now + timedelta(days=days)
    reminders = []
    for cal in calendars:
        for ev in cal.events:
            for alarm in ev.alarms:
                trigger_offset = parse_trigger(alarm.trigger)
                if ev.dtstart and trigger_offset is not None:
                    alarm_time = ev.dtstart + trigger_offset
                    if now <= alarm_time <= end_date:
                        reminders.append({
                            "time": alarm_time,
                            "summary": ev.summary,
                            "action": alarm.action,
                            "description": alarm.description or alarm.summary,
                            "source": cal.source,
                        })
    reminders.sort(key=lambda x: x["time"])
    return reminders


def parse_trigger(trigger: str) -> Optional[timedelta]:
    if not trigger:
        return None
    m = re.match(r"^(-?)P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", trigger)
    if not m:
        return None
    sign = -1 if m.group(1) == "-" else 1
    days = int(m.group(2) or 0)
    hours = int(m.group(3) or 0)
    minutes = int(m.group(4) or 0)
    seconds = int(m.group(5) or 0)
    return sign * timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def sort_todos(calendars: list) -> list:
    now = datetime.now()
    all_todos = []
    for cal in calendars:
        for td in cal.todos:
            overdue = False
            if td.due and td.status not in ("COMPLETED", "CANCELLED"):
                due_naive = td.due.replace(tzinfo=None) if td.due.tzinfo else td.due
                if due_naive < now:
                    overdue = True
            all_todos.append({
                "todo": td,
                "source": cal.source,
                "overdue": overdue,
            })
    def sort_key(item):
        td = item["todo"]
        if td.due:
            due_naive = td.due.replace(tzinfo=None) if td.due.tzinfo else td.due
            return (0, due_naive)
        return (1, datetime.max)
    all_todos.sort(key=sort_key)
    return all_todos


def filter_events(events: list, keyword: str = None, location: str = None,
                  from_date: date = None, to_date: date = None) -> list:
    result = []
    for ev in events:
        if keyword:
            kw = keyword.lower()
            if kw not in (ev.summary or "").lower() and kw not in (ev.description or "").lower():
                continue
        if location:
            if location.lower() not in (ev.location or "").lower():
                continue
        if from_date and ev.dtstart:
            d = ev.dtstart.date()
            if d < from_date:
                continue
        if to_date and ev.dtstart:
            d = ev.dtstart.date()
            if d > to_date:
                continue
        result.append(ev)
    return result


def render_week_view(events: list, start_date: date) -> str:
    lines = []
    while start_date.weekday() != 0:
        start_date -= timedelta(days=1)
    end_date = start_date + timedelta(days=6)
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    header = ""
    for i in range(7):
        d = start_date + timedelta(days=i)
        header += f" {d.month:>2}/{d.day:<2} {weekday_names[i]} │"
    lines.append("┌" + "─" * 13 + "┬" + ("─" * 14 + "┬") * 6 + "─" * 14 + "┐")
    lines.append("│" + header)
    lines.append("├" + "─" * 13 + "┼" + ("─" * 14 + "┼") * 6 + "─" * 14 + "┤")
    events_by_day = defaultdict(list)
    for ev in events:
        if ev.dtstart:
            d = ev.dtstart.date()
            if start_date <= d <= end_date:
                events_by_day[d].append(ev)
    max_events = max([len(v) for v in events_by_day.values()] or [0])
    for row in range(max(4, max_events)):
        row_line = "│"
        for i in range(7):
            d = start_date + timedelta(days=i)
            day_events = sorted(events_by_day.get(d, []), key=lambda x: x.dtstart or datetime.min)
            if row < len(day_events):
                ev = day_events[row]
                time_str = ev.dtstart.strftime("%H:%M") if ev.dtstart and not ev.all_day else "全天"
                title = ev.summary[:6] if ev.summary else "(无标题)"
                cell = f" {time_str} {title:<5}"
            else:
                cell = " " * 13
            row_line += f"{cell}│"
        lines.append(row_line)
    lines.append("└" + "─" * 13 + "┴" + ("─" * 14 + "┴") * 6 + "─" * 14 + "┘")
    return "\n".join(lines)


def render_month_view(events: list, year: int, month: int) -> str:
    lines = []
    lines.append(bold(f"          {year}年 {month}月"))
    weekday_names = [" 一 ", " 二 ", " 三 ", " 四 ", " 五 ", " 六 ", " 日 "]
    lines.append("┌" + "────┬" * 6 + "────┐")
    lines.append("│" + "│".join(weekday_names) + "│")
    lines.append("├" + "────┼" * 6 + "────┤")
    first_day = date(year, month, 1)
    start_weekday = first_day.weekday()
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    days_in_month = (next_month - timedelta(days=1)).day
    events_by_day = defaultdict(list)
    for ev in events:
        if ev.dtstart and ev.dtstart.year == year and ev.dtstart.month == month:
            events_by_day[ev.dtstart.day].append(ev)
    today = date.today()
    day = 1
    week_row = 0
    while day <= days_in_month or week_row == 0 and start_weekday > 0:
        row = "│"
        for col in range(7):
            if week_row == 0 and col < start_weekday:
                row += "    │"
            elif day > days_in_month:
                row += "    │"
            else:
                is_today = (today.year == year and today.month == month and today.day == day)
                ev_count = len(events_by_day.get(day, []))
                day_str = f"{day:>3}"
                if is_today:
                    day_str = bold(green(f"{day:>3}"))
                if ev_count > 0:
                    marker = blue(f"*{ev_count}")
                    cell = f"{day_str}{marker}"
                else:
                    cell = f"{day_str} "
                row += f"{cell}│"
                day += 1
        lines.append(row)
        if day <= days_in_month:
            lines.append("├" + "────┼" * 6 + "────┤")
        week_row += 1
    lines.append("└" + "────┴" * 6 + "────┘")
    return "\n".join(lines)


def export_to_json(events: list, todos: list) -> str:
    def ev_to_dict(ev):
        return {
            "uid": ev.uid,
            "summary": ev.summary,
            "location": ev.location,
            "description": ev.description,
            "dtstart": ev.dtstart.isoformat() if ev.dtstart else None,
            "dtend": ev.dtend.isoformat() if ev.dtend else None,
            "all_day": ev.all_day,
            "transp": ev.transp,
        }
    def td_to_dict(td):
        return {
            "uid": td.uid,
            "summary": td.summary,
            "description": td.description,
            "due": td.due.isoformat() if td.due else None,
            "status": td.status,
            "percent_complete": td.percent_complete,
        }
    return json.dumps({
        "events": [ev_to_dict(e) for e in events],
        "todos": [td_to_dict(t) for t in todos],
    }, ensure_ascii=False, indent=2)


def export_to_markdown(events: list, title: str = "日程表") -> str:
    lines = [f"# {title}", "", "| 日期 | 时间 | 标题 | 地点 | 描述 |", "| --- | --- | --- | --- | --- |"]
    for ev in sorted(events, key=lambda x: x.dtstart or datetime.min):
        date_str = ev.dtstart.strftime("%Y-%m-%d") if ev.dtstart else "-"
        if ev.all_day:
            time_str = "全天"
        elif ev.dtstart and ev.dtend:
            time_str = f"{ev.dtstart.strftime('%H:%M')} - {ev.dtend.strftime('%H:%M')}"
        elif ev.dtstart:
            time_str = ev.dtstart.strftime("%H:%M")
        else:
            time_str = "-"
        lines.append(f"| {date_str} | {time_str} | {ev.summary or '-'} | {ev.location or '-'} | {(ev.description or '-').replace(chr(10), ' ')} |")
    return "\n".join(lines)


def parse_date_arg(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def format_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds and not hours:
        parts.append(f"{seconds}秒")
    return "".join(parts) if parts else "0秒"


def cmd_list(args):
    cals = [load_ics_file(p) for p in args.calendar]
    range_start = parse_date_arg(args.from_date) if args.from_date else date.min
    range_end = parse_date_arg(args.to_date) if args.to_date else date.max
    all_occurrences = []
    for cal in cals:
        for ev in cal.events:
            occs = expand_rrule(ev, range_start, range_end)
            all_occurrences.extend(occs)
    all_occurrences = filter_events(
        all_occurrences,
        keyword=args.keyword,
        location=args.location,
        from_date=range_start if args.from_date else None,
        to_date=range_end if args.to_date else None,
    )
    all_occurrences.sort(key=lambda x: to_utc_naive(x.dtstart) or datetime.min)
    if not all_occurrences:
        print("未找到匹配的日程。")
        return
    print(bold(f"共找到 {len(all_occurrences)} 个日程:"))
    print()
    for ev in all_occurrences:
        dt_str = ev.dtstart.strftime("%Y-%m-%d %H:%M") if ev.dtstart else "?"
        if ev.all_day:
            dt_str = ev.dtstart.strftime("%Y-%m-%d") + " 全天"
        print(f"  {blue(dt_str)}  {bold(ev.summary or '(无标题)')}")
        if ev.location:
            print(f"    📍 {ev.location}")
        if ev.description:
            desc = ev.description[:100]
            if len(ev.description) > 100:
                desc += "..."
            print(f"    📝 {desc}")
        print()


def cmd_conflicts(args):
    cals = [load_ics_file(p) for p in args.calendars]
    range_start = parse_date_arg(args.from_date) if args.from_date else date.min
    range_end = parse_date_arg(args.to_date) if args.to_date else date.max
    expanded_cals = []
    for cal in cals:
        new_cal = VCalendar(source=cal.source)
        for ev in cal.events:
            occs = expand_rrule(ev, range_start, range_end)
            new_cal.events.extend(occs)
        expanded_cals.append(new_cal)
    conflicts = detect_conflicts(
        expanded_cals,
        ignore_all_day=args.ignore_all_day,
        only_busy=args.only_busy,
    )
    if not conflicts:
        print(green("✓ 没有检测到冲突。"))
        return
    print(red(bold(f"⚠ 检测到 {len(conflicts)} 个时间冲突:")))
    print()
    for idx, cf in enumerate(conflicts, 1):
        e1, s1 = cf["event1"], cf["source1"]
        e2, s2 = cf["event2"], cf["source2"]
        print(f"  冲突 #{idx}: {red(format_duration(cf['overlap_duration']))} 重叠")
        print(f"    重叠时段: {cf['overlap_start'].strftime('%Y-%m-%d %H:%M')} - {cf['overlap_end'].strftime('%H:%M')}")
        print(f"    1. [{Path(s1).name}] {bold(e1.summary or '(无标题)')}")
        print(f"       {e1.dtstart.strftime('%H:%M')} - {e1.dtend.strftime('%H:%M')}")
        print(f"    2. [{Path(s2).name}] {bold(e2.summary or '(无标题)')}")
        print(f"       {e2.dtstart.strftime('%H:%M')} - {e2.dtend.strftime('%H:%M')}")
        print()


def cmd_merge(args):
    cals = [load_ics_file(p) for p in args.calendars]
    merged, conflicts_log = merge_calendars(cals)
    export_to_ics(merged, args.output)
    print(green(f"✓ 合并完成，已导出到 {args.output}"))
    print(f"  - 日程: {len(merged.events)} 个")
    print(f"  - 待办: {len(merged.todos)} 个")
    if conflicts_log:
        print()
        print(yellow(f"⚠ 存在 {len(conflicts_log)} 个UID冲突（已自动解决）:"))
        for c in conflicts_log:
            print(f"  - {c['summary']} ({c['uid'][:8]}...): 保留 {Path(c['kept']).name}, 丢弃 {Path(c['discarded']).name} [{c['reason']}]")


def cmd_reminders(args):
    cals = [load_ics_file(p) for p in args.calendar]
    reminders = list_reminders(cals, days=args.days)
    if not reminders:
        print("未来没有提醒。")
        return
    print(bold(f"未来 {args.days} 天的提醒 ({len(reminders)} 个):"))
    print()
    for r in reminders:
        time_str = r["time"].strftime("%Y-%m-%d %H:%M")
        print(f"  ⏰ {blue(time_str)}  {bold(r['summary'])}")
        if r["description"]:
            print(f"     {r['description']}")
        print(f"     [来自 {Path(r['source']).name}, 动作: {r['action']}]")
        print()


def cmd_todos(args):
    cals = [load_ics_file(p) for p in args.calendar]
    sorted_todos = sort_todos(cals)
    if not sorted_todos:
        print("没有待办事项。")
        return
    print(bold(f"待办事项清单 ({len(sorted_todos)} 个):"))
    print()
    for item in sorted_todos:
        td = item["todo"]
        prefix = "🔴 " if item["overdue"] else "📋 "
        status_color = red if item["overdue"] else (lambda x: x)
        title = status_color(bold(td.summary or "(无标题)"))
        due_str = ""
        if td.due:
            due_str = td.due.strftime(" (%Y-%m-%d %H:%M 截止)")
        status_str = f" [{td.status}]"
        if td.percent_complete > 0:
            status_str += f" {td.percent_complete}%"
        print(f"  {prefix}{title}{status_color(due_str)}{status_str}")
        if td.description:
            desc = td.description[:100]
            if len(td.description) > 100:
                desc += "..."
            print(f"     {desc}")
        print(f"     [来自 {Path(item['source']).name}]")
        print()


def cmd_view(args):
    cals = [load_ics_file(p) for p in args.calendar]
    range_start = parse_date_arg(args.from_date) if args.from_date else date.min
    range_end = parse_date_arg(args.to_date) if args.to_date else date.max
    all_occurrences = []
    all_todos = []
    for cal in cals:
        for ev in cal.events:
            occs = expand_rrule(ev, range_start, range_end)
            all_occurrences.extend(occs)
        all_todos.extend(cal.todos)
    all_occurrences = filter_events(
        all_occurrences,
        keyword=args.keyword,
        location=args.location,
        from_date=range_start if args.from_date else None,
        to_date=range_end if args.to_date else None,
    )
    if args.format == "week":
        base = parse_date_arg(args.from_date) if args.from_date else date.today()
        print(render_week_view(all_occurrences, base))
    elif args.format == "month":
        base = parse_date_arg(args.from_date) if args.from_date else date.today()
        print(render_month_view(all_occurrences, base.year, base.month))
    elif args.format == "json":
        print(export_to_json(all_occurrences, all_todos))
    elif args.format == "markdown":
        print(export_to_markdown(all_occurrences, title=args.title or "日程表"))


@dataclass
class Participant:
    name: str
    calendar: VCalendar
    weight: float = 1.0


@dataclass
class BusyInterval:
    start: datetime
    end: datetime
    summary: str
    source: str
    transp: str


@dataclass
class FreeSlot:
    start: datetime
    end: datetime


@dataclass
class Suggestion:
    start: datetime
    end: datetime
    score: float
    available_participants: list
    busy_participants: list
    conflicts: list
    reasons: list


def parse_time_str(s: str) -> tuple:
    parts = s.split(":")
    return int(parts[0]), int(parts[1])


def get_target_timezone(timezone_name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(timezone_name)
    except Exception:
        pass
    try:
        from backports.zoneinfo import ZoneInfo
        return ZoneInfo(timezone_name)
    except Exception:
        pass
    try:
        from datetime import timezone as dt_timezone
        offset_map = {
            "Asia/Shanghai": timedelta(hours=8),
            "Asia/Tokyo": timedelta(hours=9),
            "Asia/Hong_Kong": timedelta(hours=8),
            "Asia/Singapore": timedelta(hours=8),
            "Asia/Seoul": timedelta(hours=9),
            "Asia/Bangkok": timedelta(hours=7),
            "Asia/Kolkata": timedelta(hours=5, minutes=30),
            "Asia/Dubai": timedelta(hours=4),
            "Europe/London": timedelta(hours=0),
            "Europe/Paris": timedelta(hours=1),
            "Europe/Berlin": timedelta(hours=1),
            "Europe/Moscow": timedelta(hours=3),
            "America/New_York": timedelta(hours=-5),
            "America/Chicago": timedelta(hours=-6),
            "America/Denver": timedelta(hours=-7),
            "America/Los_Angeles": timedelta(hours=-8),
            "America/Toronto": timedelta(hours=-5),
            "America/Sao_Paulo": timedelta(hours=-3),
            "Australia/Sydney": timedelta(hours=10),
            "Pacific/Auckland": timedelta(hours=12),
            "UTC": timedelta(hours=0),
        }
        if timezone_name in offset_map:
            return dt_timezone(offset_map[timezone_name])
        return None
    except Exception:
        return None


def convert_to_timezone(dt: datetime, target_tz) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(target_tz)


def get_timezone_offset_hours(tz_name: str, dt: datetime) -> Optional[float]:
    tz = get_target_timezone(tz_name)
    if tz is None:
        return None
    try:
        aware_dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        offset = aware_dt.astimezone(tz).utcoffset()
        if offset is not None:
            return offset.total_seconds() / 3600.0
    except Exception:
        pass
    return None


def get_busy_intervals(cal: VCalendar, range_start: date, range_end: date,
                       target_tz, ignore_all_day: bool = False) -> list:
    busy = []
    for ev in cal.events:
        if ev.transp == "TRANSPARENT":
            continue
        if ignore_all_day and ev.all_day:
            continue
        occs = expand_rrule(ev, range_start, range_end)
        for occ in occs:
            if not occ.dtstart or not occ.dtend:
                continue
            start_utc = to_utc_naive(occ.dtstart)
            end_utc = to_utc_naive(occ.dtend)
            if start_utc is None or end_utc is None:
                continue
            start_dt = datetime.combine(start_utc.date(), start_utc.time(), tzinfo=timezone.utc)
            end_dt = datetime.combine(end_utc.date(), end_utc.time(), tzinfo=timezone.utc)
            start_local = convert_to_timezone(start_dt, target_tz)
            end_local = convert_to_timezone(end_dt, target_tz)
            if occ.all_day:
                start_local = start_local.replace(hour=0, minute=0, second=0)
                end_local = end_local.replace(hour=23, minute=59, second=59)
            busy.append(BusyInterval(
                start=start_local,
                end=end_local,
                summary=occ.summary or "(无标题)",
                source=cal.source,
                transp=occ.transp,
            ))
    busy.sort(key=lambda x: x.start)
    return busy


def merge_intervals(intervals: list) -> list:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda x: x.start)
    merged = [sorted_intervals[0]]
    for interval in sorted_intervals[1:]:
        last = merged[-1]
        if interval.start <= last.end:
            new_end = max(last.end, interval.end)
            merged[-1] = BusyInterval(
                start=last.start,
                end=new_end,
                summary=last.summary + "; " + interval.summary,
                source=last.source,
                transp=last.transp,
            )
        else:
            merged.append(interval)
    return merged


def get_free_slots(busy_intervals: list, day: date, work_start_hour: int,
                   work_start_min: int, work_end_hour: int, work_end_min: int,
                   lunch_start_hour: int = 0, lunch_start_min: int = 0,
                   lunch_end_hour: int = 0, lunch_end_min: int = 0) -> list:
    work_start = datetime.combine(day, datetime.min.time()).replace(
        hour=work_start_hour, minute=work_start_min
    )
    work_end = datetime.combine(day, datetime.min.time()).replace(
        hour=work_end_hour, minute=work_end_min
    )

    day_busy = []
    for bi in busy_intervals:
        bi_start = bi.start.replace(tzinfo=None)
        bi_end = bi.end.replace(tzinfo=None)
        if bi_end <= work_start or bi_start >= work_end:
            continue
        overlap_start = max(bi_start, work_start)
        overlap_end = min(bi_end, work_end)
        day_busy.append((overlap_start, overlap_end, bi.summary))

    day_busy.sort(key=lambda x: x[0])

    if lunch_start_hour > 0 or lunch_end_hour > 0:
        lunch_start = datetime.combine(day, datetime.min.time()).replace(
            hour=lunch_start_hour, minute=lunch_start_min
        )
        lunch_end = datetime.combine(day, datetime.min.time()).replace(
            hour=lunch_end_hour, minute=lunch_end_min
        )
        if lunch_start < lunch_end:
            day_busy.append((lunch_start, lunch_end, "午休时间"))
            day_busy.sort(key=lambda x: x[0])

    free_slots = []
    current = work_start
    for busy_start, busy_end, _ in day_busy:
        if current < busy_start:
            free_slots.append(FreeSlot(start=current, end=busy_start))
        current = max(current, busy_end)
    if current < work_end:
        free_slots.append(FreeSlot(start=current, end=work_end))
    return free_slots


def intersect_free_slots(all_free: list, duration_minutes: int,
                         earliest_hour: int = 0, earliest_min: int = 0,
                         latest_hour: int = 23, latest_min: int = 59) -> list:
    if not all_free:
        return []
    common = all_free[0][:]
    for participant_free in all_free[1:]:
        new_common = []
        i = j = 0
        while i < len(common) and j < len(participant_free):
            s1, e1 = common[i].start, common[i].end
            s2, e2 = participant_free[j].start, participant_free[j].end
            overlap_start = max(s1, s2)
            overlap_end = min(e1, e2)
            if overlap_start < overlap_end:
                new_common.append(FreeSlot(start=overlap_start, end=overlap_end))
            if e1 < e2:
                i += 1
            else:
                j += 1
        common = new_common
        if not common:
            break

    result = []
    delta = timedelta(minutes=duration_minutes)
    for slot in common:
        slot_start = slot.start
        slot_end = slot.end
        earliest_dt = slot_start.replace(hour=earliest_hour, minute=earliest_min)
        latest_dt = slot_start.replace(hour=latest_hour, minute=latest_min)
        effective_start = max(slot_start, earliest_dt)
        effective_end = min(slot_end, latest_dt)
        if effective_end - effective_start >= delta:
            result.append(FreeSlot(start=effective_start, end=effective_end))
    return result


def get_participant_availability(participant: Participant, slot_start: datetime,
                                 slot_end: datetime, range_start: date,
                                 range_end: date, target_tz,
                                 ignore_all_day: bool = False) -> tuple:
    busy = get_busy_intervals(participant.calendar, range_start, range_end,
                              target_tz, ignore_all_day)
    slot_start_naive = slot_start.replace(tzinfo=None)
    slot_end_naive = slot_end.replace(tzinfo=None)

    conflicts = []
    for bi in busy:
        bi_start = bi.start.replace(tzinfo=None)
        bi_end = bi.end.replace(tzinfo=None)
        if bi_start < slot_end_naive and bi_end > slot_start_naive:
            conflicts.append({
                "summary": bi.summary,
                "start": bi.start,
                "end": bi.end,
                "source": bi.source,
            })

    is_available = len(conflicts) == 0
    return is_available, conflicts


def score_slot(slot_start: datetime, slot_end: datetime,
               participants: list, range_start: date, range_end: date,
               target_tz, ignore_all_day: bool = False,
               participant_busy_cache: dict = None) -> tuple:
    available = []
    busy = []
    all_conflicts = []
    total_weight = 0
    available_weight = 0

    slot_start_naive = slot_start.replace(tzinfo=None)
    slot_end_naive = slot_end.replace(tzinfo=None)

    for p in participants:
        total_weight += p.weight

        if participant_busy_cache is not None and p.name in participant_busy_cache:
            busy_intervals = participant_busy_cache[p.name]
        else:
            busy_intervals = get_busy_intervals(
                p.calendar, range_start, range_end, target_tz, ignore_all_day
            )

        conflicts = []
        for bi in busy_intervals:
            bi_start = bi.start.replace(tzinfo=None)
            bi_end = bi.end.replace(tzinfo=None)
            if bi_start < slot_end_naive and bi_end > slot_start_naive:
                conflicts.append({
                    "summary": bi.summary,
                    "start": bi.start,
                    "end": bi.end,
                    "source": bi.source,
                })

        is_available = len(conflicts) == 0
        if is_available:
            available.append(p.name)
            available_weight += p.weight
        else:
            busy.append(p.name)
            all_conflicts.append({
                "participant": p.name,
                "conflicts": conflicts,
            })

    score = available_weight / total_weight if total_weight > 0 else 0

    reasons = []
    if score == 1.0:
        reasons.append("所有参与者均可用")
    else:
        reasons.append(f"{len(available)}/{len(participants)} 位参与者可用 (加权 {int(score*100)}%)")

    hour = slot_start.hour
    if 10 <= hour <= 11:
        reasons.append("上午中段黄金时段")
    elif 14 <= hour <= 16:
        reasons.append("下午工作效率时段")
    elif hour == 9:
        reasons.append("早间时段，会议安排灵活")
    elif hour >= 17:
        reasons.append("临近下班时段")

    return score, available, busy, all_conflicts, reasons


def find_suggestions(participants: list, range_start: date, range_end: date,
                     duration_minutes: int = 60,
                     work_hours: str = "09:00-18:00",
                     timezone_name: str = "Asia/Shanghai",
                     lunch_break: str = "12:00-13:00",
                     earliest_start: str = "09:00",
                     latest_start: str = "17:00",
                     top_n: int = 5,
                     ignore_all_day: bool = False,
                     include_all_day_free: bool = False) -> list:
    target_tz = get_target_timezone(timezone_name)
    if target_tz is None:
        target_tz = timezone(timedelta(hours=8))

    wh_start, wh_end = work_hours.split("-")
    work_start_h, work_start_m = parse_time_str(wh_start)
    work_end_h, work_end_m = parse_time_str(wh_end)

    lunch_start_h, lunch_start_m = 0, 0
    lunch_end_h, lunch_end_m = 0, 0
    if lunch_break and lunch_break != "none":
        ls, le = lunch_break.split("-")
        lunch_start_h, lunch_start_m = parse_time_str(ls)
        lunch_end_h, lunch_end_m = parse_time_str(le)

    es_h, es_m = parse_time_str(earliest_start)
    ls_h, ls_m = parse_time_str(latest_start)

    participant_busy = {}
    for p in participants:
        busy = get_busy_intervals(p.calendar, range_start, range_end,
                                  target_tz, ignore_all_day)
        participant_busy[p.name] = merge_intervals(busy)

    earliest_dt = datetime.combine(range_start, datetime.min.time()).replace(
        hour=es_h, minute=es_m
    )
    latest_dt = datetime.combine(range_end, datetime.min.time()).replace(
        hour=ls_h, minute=ls_m
    )

    lunch_start_min = lunch_start_h * 60 + lunch_start_m if (lunch_start_h or lunch_start_m) else None
    lunch_end_min = lunch_end_h * 60 + lunch_end_m if (lunch_end_h or lunch_end_m) else None
    work_start_min = work_start_h * 60 + work_start_m
    work_end_min = work_end_h * 60 + work_end_m
    earliest_min = es_h * 60 + es_m
    latest_min = ls_h * 60 + ls_m

    all_suggestions = []
    delta = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=30)

    current_day = range_start
    while current_day <= range_end:
        day_start = datetime.combine(current_day, datetime.min.time())

        candidate_start = day_start.replace(hour=work_start_h, minute=work_start_m)
        candidate_end_limit = day_start.replace(hour=work_end_h, minute=work_end_m) - delta

        earliest_today = day_start.replace(hour=es_h, minute=es_m)
        latest_today = day_start.replace(hour=ls_h, minute=ls_m)

        effective_start = max(candidate_start, earliest_today)
        effective_end_limit = min(candidate_end_limit, latest_today)

        if effective_end_limit <= effective_start:
            current_day += timedelta(days=1)
            continue

        current_time = effective_start
        while current_time <= effective_end_limit + timedelta(seconds=1):
            slot_start = current_time
            slot_end = current_time + delta

            slot_start_min = slot_start.hour * 60 + slot_start.minute
            slot_end_min = slot_end.hour * 60 + slot_end.minute

            if lunch_start_min is not None and lunch_end_min is not None:
                if not (slot_end_min <= lunch_start_min or slot_start_min >= lunch_end_min):
                    current_time += step
                    continue

            score, available, busy_p, conflicts, reasons = score_slot(
                slot_start, slot_end, participants,
                range_start, range_end, target_tz, ignore_all_day,
                participant_busy_cache=participant_busy
            )

            all_suggestions.append(Suggestion(
                start=slot_start,
                end=slot_end,
                score=score,
                available_participants=available,
                busy_participants=busy_p,
                conflicts=conflicts,
                reasons=reasons,
            ))
            current_time += step

        current_day += timedelta(days=1)

    meaningful_suggestions = [s for s in all_suggestions if s.score > 0]
    meaningful_suggestions.sort(key=lambda s: (-s.score, s.start))

    seen = set()
    unique_suggestions = []
    for s in meaningful_suggestions:
        key = (s.start, s.end)
        if key not in seen:
            seen.add(key)
            unique_suggestions.append(s)
            if len(unique_suggestions) >= top_n:
                break

    return unique_suggestions


def export_suggestions_to_markdown(suggestions: list, participants: list,
                                   params: dict) -> str:
    lines = ["# 团队可用时间推荐报告", ""]
    lines.append("## 参数信息")
    lines.append(f"- **日期范围**: {params['range_start']} 至 {params['range_end']}")
    lines.append(f"- **会议时长**: {params['duration']} 分钟")
    lines.append(f"- **工作时间**: {params['work_hours']}")
    lines.append(f"- **时区**: {params['timezone']}")
    if params.get('lunch_break'):
        lines.append(f"- **午休时间**: {params['lunch_break']}")
    lines.append(f"- **最早开始**: {params['earliest_start']}")
    lines.append(f"- **最晚开始**: {params['latest_start']}")
    lines.append("")

    lines.append("## 参与者")
    for p in participants:
        lines.append(f"- **{p.name}** (权重: {p.weight})")
    lines.append("")

    if not suggestions:
        lines.append("## 推荐结果")
        lines.append("")
        lines.append("⚠️ **未找到符合条件的共同空闲时间**")
        lines.append("")
        lines.append("建议尝试以下方案：")
        lines.append("1. 扩大日期范围")
        lines.append("2. 缩短会议时长")
        lines.append("3. 放宽工作时间限制")
        lines.append("4. 考虑部分参与者可用的时段")
        return "\n".join(lines)

    lines.append(f"## 推荐 Top {len(suggestions)}")
    lines.append("")

    for idx, s in enumerate(suggestions, 1):
        score_pct = int(s.score * 100)
        lines.append(f"### 推荐 #{idx}: {score_pct}% 匹配度")
        lines.append("")
        lines.append(f"- **时间**: {s.start.strftime('%Y-%m-%d %H:%M')} - {s.end.strftime('%H:%M')}")
        lines.append(f"- **可用参与者**: {', '.join(s.available_participants) if s.available_participants else '无'}")
        if s.busy_participants:
            lines.append(f"- **忙碌参与者**: {', '.join(s.busy_participants)}")
        lines.append("")

        if s.conflicts:
            lines.append("#### 冲突详情")
            lines.append("")
            for cf in s.conflicts:
                lines.append(f"**{cf['participant']}**:")
                for c in cf['conflicts']:
                    lines.append(f"- {c['start'].strftime('%H:%M')} - {c['end'].strftime('%H:%M')}: {c['summary']}")
            lines.append("")

        lines.append("#### 推荐理由")
        lines.append("")
        for reason in s.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    return "\n".join(lines)


def export_suggestions_to_json(suggestions: list, participants: list,
                               params: dict) -> str:
    sugg_list = []
    for s in suggestions:
        sugg_list.append({
            "start": s.start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": s.end.strftime("%Y-%m-%dT%H:%M:%S"),
            "score": round(s.score, 4),
            "available_participants": s.available_participants,
            "busy_participants": s.busy_participants,
            "conflicts": [
                {
                    "participant": c["participant"],
                    "conflicts": [
                        {
                            "summary": cc["summary"],
                            "start": cc["start"].strftime("%Y-%m-%dT%H:%M:%S"),
                            "end": cc["end"].strftime("%Y-%m-%dT%H:%M:%S"),
                            "source": cc["source"],
                        }
                        for cc in c["conflicts"]
                    ]
                }
                for c in s.conflicts
            ],
            "reasons": s.reasons,
        })

    result = {
        "params": {
            "range_start": str(params["range_start"]),
            "range_end": str(params["range_end"]),
            "duration_minutes": params["duration"],
            "work_hours": params["work_hours"],
            "timezone": params["timezone"],
            "lunch_break": params.get("lunch_break", ""),
            "earliest_start": params["earliest_start"],
            "latest_start": params["latest_start"],
        },
        "participants": [
            {"name": p.name, "weight": p.weight, "source": p.calendar.source}
            for p in participants
        ],
        "suggestions_count": len(suggestions),
        "suggestions": sugg_list,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def cmd_suggest(args):
    calendars = [load_ics_file(p) for p in args.calendars]

    participants = []
    if args.names:
        names = args.names.split(",")
    else:
        names = [Path(p).stem for p in args.calendars]

    if args.weights:
        weights = [float(w) for w in args.weights.split(",")]
    else:
        weights = [1.0] * len(calendars)

    for i, cal in enumerate(calendars):
        name = names[i] if i < len(names) else f"participant_{i+1}"
        weight = weights[i] if i < len(weights) else 1.0
        participants.append(Participant(name=name, calendar=cal, weight=weight))

    range_start = parse_date_arg(args.from_date) if args.from_date else date.today()
    range_end = parse_date_arg(args.to_date) if args.to_date else range_start + timedelta(days=7)

    duration = args.duration if args.duration else 60
    work_hours = args.work_hours if args.work_hours else "09:00-18:00"
    tz_name = args.timezone if args.timezone else "Asia/Shanghai"
    lunch = args.lunch if args.lunch else "12:00-13:00"
    earliest = args.earliest if args.earliest else "09:00"
    latest = args.latest if args.latest else "17:00"
    top_n = args.top if args.top else 5
    ignore_all_day = args.ignore_all_day

    suggestions = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=duration,
        work_hours=work_hours,
        timezone_name=tz_name,
        lunch_break=lunch,
        earliest_start=earliest,
        latest_start=latest,
        top_n=top_n,
        ignore_all_day=ignore_all_day,
    )

    params = {
        "range_start": range_start,
        "range_end": range_end,
        "duration": duration,
        "work_hours": work_hours,
        "timezone": tz_name,
        "lunch_break": lunch if lunch != "none" else "",
        "earliest_start": earliest,
        "latest_start": latest,
    }

    if args.report:
        report_path = args.report
        if report_path.endswith(".json"):
            content = export_suggestions_to_json(suggestions, participants, params)
        else:
            content = export_suggestions_to_markdown(suggestions, participants, params)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(green(f"✓ 推荐报告已导出到 {report_path}"))
        print()

    print(bold(f"团队可用时间推荐 (Top {top_n})"))
    print(f"日期范围: {range_start} 至 {range_end}")
    print(f"会议时长: {duration} 分钟")
    print(f"时区: {tz_name}")
    print(f"参与者: {', '.join([p.name for p in participants])}")
    print()

    if not suggestions:
        print(yellow("⚠ 未找到所有参与者都可用的时间段。"))
        print()
        print("建议:")
        print("  1. 扩大日期范围")
        print("  2. 缩短会议时长")
        print("  3. 放宽工作时间限制")
        print("  4. 使用 --ignore-all-day 忽略全天事件")
        return

    for idx, s in enumerate(suggestions, 1):
        score_pct = int(s.score * 100)
        score_color = green if s.score == 1.0 else (yellow if s.score >= 0.6 else red)
        print(f"  {bold(f'#{idx}')} {score_color(f'{score_pct}%')}  {blue(s.start.strftime('%Y-%m-%d %H:%M'))} - {s.end.strftime('%H:%M')}")
        print(f"      可用: {', '.join(s.available_participants)}")
        if s.busy_participants:
            print(f"      忙碌: {red(', '.join(s.busy_participants))}")
        if s.reasons:
            print(f"      理由: {'; '.join(s.reasons[:2])}")
        print()


def main():
    parser = argparse.ArgumentParser(
        prog="calmerge",
        description="iCalendar (.ics) 日程解析、冲突检测与合并工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    p_list = subparsers.add_parser("list", help="列出日程")
    p_list.add_argument("calendar", nargs="+", help="ICS文件路径")
    p_list.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_list.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p_list.add_argument("--keyword", help="按关键词过滤标题和描述")
    p_list.add_argument("--location", help="按地点过滤")
    p_list.set_defaults(func=cmd_list)

    p_conf = subparsers.add_parser("conflicts", help="检测日历冲突")
    p_conf.add_argument("calendars", nargs="+", help="多个ICS文件路径")
    p_conf.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_conf.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p_conf.add_argument("--ignore-all-day", action="store_true", help="忽略全天事件")
    p_conf.add_argument("--only-busy", action="store_true", help="仅检测标记为busy的事件")
    p_conf.set_defaults(func=cmd_conflicts)

    p_merge = subparsers.add_parser("merge", help="合并多个日历")
    p_merge.add_argument("calendars", nargs="+", help="多个ICS文件路径")
    p_merge.add_argument("-o", "--output", required=True, help="输出ICS文件路径")
    p_merge.set_defaults(func=cmd_merge)

    p_rem = subparsers.add_parser("reminders", help="查看未来提醒")
    p_rem.add_argument("calendar", nargs="+", help="ICS文件路径")
    p_rem.add_argument("--days", type=int, default=7, help="未来N天 (默认7)")
    p_rem.set_defaults(func=cmd_reminders)

    p_todo = subparsers.add_parser("todos", help="查看待办事项")
    p_todo.add_argument("calendar", nargs="+", help="ICS文件路径")
    p_todo.set_defaults(func=cmd_todos)

    p_view = subparsers.add_parser("view", help="查看/导出视图")
    p_view.add_argument("calendar", nargs="+", help="ICS文件路径")
    p_view.add_argument("--format", choices=["week", "month", "json", "markdown"], default="week", help="视图格式")
    p_view.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_view.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD (仅用于json/markdown)")
    p_view.add_argument("--keyword", help="按关键词过滤")
    p_view.add_argument("--location", help="按地点过滤")
    p_view.add_argument("--title", help="Markdown标题")
    p_view.set_defaults(func=cmd_view)

    p_suggest = subparsers.add_parser("suggest", help="团队可用时间智能推荐")
    p_suggest.add_argument("calendars", nargs="+", help="多个ICS文件路径")
    p_suggest.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_suggest.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p_suggest.add_argument("--duration", type=int, help="会议时长(分钟)，默认60")
    p_suggest.add_argument("--work-hours", help="工作时间窗口，如 09:00-18:00")
    p_suggest.add_argument("--timezone", help="时区偏好，如 Asia/Shanghai")
    p_suggest.add_argument("--lunch", help="午休时间，如 12:00-13:00，none表示无午休")
    p_suggest.add_argument("--earliest", help="最早开始时间，如 09:00")
    p_suggest.add_argument("--latest", help="最晚开始时间，如 17:00")
    p_suggest.add_argument("--top", type=int, help="推荐Top N，默认5")
    p_suggest.add_argument("--names", help="参与者名称，逗号分隔")
    p_suggest.add_argument("--weights", help="参与者权重，逗号分隔")
    p_suggest.add_argument("--ignore-all-day", action="store_true", help="忽略全天事件")
    p_suggest.add_argument("--report", help="导出推荐报告路径 (.md 或 .json)")
    p_suggest.set_defaults(func=cmd_suggest)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
