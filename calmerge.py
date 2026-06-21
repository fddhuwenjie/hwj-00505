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


@dataclass
class VTodo:
    uid: str = ""
    summary: str = ""
    description: str = ""
    due: Optional[datetime] = None
    dtstamp: Optional[datetime] = None
    status: str = "NEEDS-ACTION"
    percent_complete: int = 0


@dataclass
class VAlarm:
    action: str = ""
    trigger: str = ""
    description: str = ""
    summary: str = ""


@dataclass
class VCalendar:
    events: list = field(default_factory=list)
    todos: list = field(default_factory=list)
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


def parse_datetime(value: str, params: dict = None) -> Optional[datetime]:
    params = params or {}
    value = value.strip()
    if "VALUE" in params and params["VALUE"] == "DATE":
        try:
            d = datetime.strptime(value, "%Y%m%d")
            return d.replace(hour=0, minute=0, second=0)
        except ValueError:
            return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if fmt == "%Y%m%dT%H%M%SZ":
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
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


def parse_rrule(value: str) -> dict:
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
                dt = parse_datetime(v)
                if dt:
                    result[k] = dt
                else:
                    result[k] = v
            else:
                result[k] = v
    return result


def parse_ics(text: str, source: str = "") -> VCalendar:
    cal = VCalendar(source=source)
    lines = unfold_lines(text.splitlines())
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
                current_event.dtstart = parse_datetime(value, params)
                if "VALUE" in params and params["VALUE"] == "DATE":
                    current_event.all_day = True
        elif name == "DTEND":
            if current_event is not None:
                current_event.dtend = parse_datetime(value, params)
                if "VALUE" in params and params["VALUE"] == "DATE":
                    current_event.all_day = True
        elif name == "DUE":
            if current_todo is not None:
                current_todo.due = parse_datetime(value, params)
        elif name == "DTSTAMP":
            dt = parse_datetime(value, params)
            if current_event is not None:
                current_event.dtstamp = dt
            elif current_todo is not None:
                current_todo.dtstamp = dt
        elif name == "RRULE":
            if current_event is not None:
                current_event.rrule = parse_rrule(value)
                current_event._raw_rrule = value
        elif name == "EXDATE":
            if current_event is not None:
                for v in value.split(","):
                    dt = parse_datetime(v.strip())
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
    byday = rrule.get("BYDAY", [])

    duration = timedelta(0)
    if event.dtend and event.dtstart:
        duration = event.dtend - event.dtstart

    current = event.dtstart.replace(tzinfo=None) if event.dtstart.tzinfo else event.dtstart
    generated = 0
    max_iterations = 2000
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        if count is not None and generated >= count:
            break
        if until is not None:
            until_naive = until.replace(tzinfo=None) if until.tzinfo else until
            if current > until_naive:
                break

        d = current.date()
        if d > range_end:
            break

        include = False
        if freq == "DAILY":
            include = True
        elif freq == "WEEKLY":
            if byday:
                wd = current.weekday()
                for day_code in byday:
                    if WEEKDAY_MAP.get(day_code) == wd:
                        include = True
                        break
            else:
                include = True
        elif freq == "MONTHLY":
            if byday:
                pass
            else:
                include = True
        else:
            include = True

        if include and d >= range_start:
            exdate_match = False
            for ex in event.exdate:
                ex_naive = ex.replace(tzinfo=None) if ex.tzinfo else ex
                if ex_naive.date() == d and ex_naive.time() == current.time():
                    exdate_match = True
                    break
            if not exdate_match:
                new_ev = VEvent(
                    uid=event.uid,
                    summary=event.summary,
                    location=event.location,
                    description=event.description,
                    dtstart=current,
                    dtend=current + duration if event.dtend else None,
                    dtstamp=event.dtstamp,
                    all_day=event.all_day,
                    transp=event.transp,
                    alarms=event.alarms,
                )
                occurrences.append(new_ev)
                generated += 1

        if freq == "DAILY":
            current += timedelta(days=interval)
        elif freq == "WEEKLY":
            if byday:
                found_next = False
                for _ in range(14):
                    current += timedelta(days=1)
                    wd = current.weekday()
                    for day_code in byday:
                        if WEEKDAY_MAP.get(day_code) == wd:
                            found_next = True
                            break
                    if found_next:
                        break
                if not found_next:
                    current += timedelta(days=7 * interval)
            else:
                current += timedelta(weeks=interval)
        elif freq == "MONTHLY":
            year = current.year
            month = current.month + interval
            while month > 12:
                month -= 12
                year += 1
            try:
                current = current.replace(year=year, month=month)
            except ValueError:
                while True:
                    try:
                        current = current.replace(year=year, month=month, day=current.day - 1)
                        break
                    except ValueError:
                        pass
        else:
            current += timedelta(days=1)

    return occurrences


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

    all_events.sort(key=lambda x: x[0].dtstart)
    conflicts = []

    for i in range(len(all_events)):
        ev1, src1 = all_events[i]
        for j in range(i + 1, len(all_events)):
            ev2, src2 = all_events[j]
            if ev2.dtstart >= ev1.dtend:
                break
            if src1 == src2 and ev1.uid == ev2.uid:
                continue
            overlap_start = max(ev1.dtstart, ev2.dtstart)
            overlap_end = min(ev1.dtend, ev2.dtend)
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


def to_ics_format(dt: datetime, all_day: bool = False) -> str:
    if all_day:
        return dt.strftime("%Y%m%d")
    if dt.tzinfo:
        return dt.strftime("%Y%m%dT%H%M%SZ")
    return dt.strftime("%Y%m%dT%H%M%S")


def escape_ics_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def export_to_ics(cal: VCalendar, output_path: str):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//calmerge//CLI//EN"]
    for ev in cal.events:
        lines.append("BEGIN:VEVENT")
        if ev.uid:
            lines.append(f"UID:{escape_ics_value(ev.uid)}")
        if ev.dtstart:
            if ev.all_day:
                lines.append(f"DTSTART;VALUE=DATE:{to_ics_format(ev.dtstart, True)}")
            else:
                lines.append(f"DTSTART:{to_ics_format(ev.dtstart)}")
        if ev.dtend:
            if ev.all_day:
                lines.append(f"DTEND;VALUE=DATE:{to_ics_format(ev.dtend, True)}")
            else:
                lines.append(f"DTEND:{to_ics_format(ev.dtend)}")
        if ev.dtstamp:
            lines.append(f"DTSTAMP:{to_ics_format(ev.dtstamp)}")
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
            lines.append(f"DUE:{to_ics_format(td.due)}")
        if td.dtstamp:
            lines.append(f"DTSTAMP:{to_ics_format(td.dtstamp)}")
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
    all_occurrences.sort(key=lambda x: x.dtstart or datetime.min)
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
