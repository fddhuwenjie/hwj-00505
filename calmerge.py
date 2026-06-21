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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
