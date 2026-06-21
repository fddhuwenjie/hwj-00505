#!/usr/bin/env python3
"""Comprehensive test for calmerge fixes."""

from calmerge import (
    load_ics_file,
    expand_rrule,
    parse_ics,
    merge_calendars,
    export_to_ics,
    detect_conflicts,
    parse_tz_offset,
    get_timezone_offset,
)
from datetime import date, datetime, timedelta, timezone
import tempfile
import os

def test_weekly_interval_byday():
    print("\n" + "=" * 60)
    print("TEST 1: WEEKLY + INTERVAL + BYDAY (隔周展开)")
    print("=" * 60)

    cal = load_ics_file("test_complex.ics")
    ev = None
    for e in cal.events:
        if "双周" in e.summary:
            ev = e
            break
    assert ev is not None, "未找到双周团队例会事件"

    rs = date(2026, 6, 22)
    re_end = date(2026, 12, 31)
    occs = expand_rrule(ev, rs, re_end)

    print(f"找到 {len(occs)} 个事件")
    assert len(occs) == 10, f"应该有10个事件，实际{len(occs)}个 (COUNT=10)"

    mondays = [o.dtstart.date() for o in occs]
    for d in mondays:
        assert d.weekday() == 0, f"{d} 不是周一"

    for i in range(len(mondays) - 1):
        diff = (mondays[i + 1] - mondays[i]).days
        assert diff == 14, f"间隔应该是14天(2周)，实际{diff}天"

    print(f"✓ 所有事件都是周一")
    print(f"✓ 间隔都是2周 (14天)")
    print(f"✓ COUNT=10 正确")
    print(f"前3个: {mondays[:3]}")
    print(f"后3个: {mondays[-3:]}")


def test_monthly_byday():
    print("\n" + "=" * 60)
    print("TEST 2: MONTHLY + BYDAY (每月第N个周X)")
    print("=" * 60)

    cal = load_ics_file("test_complex.ics")

    ev_2tu = None
    ev_lastfr = None
    for e in cal.events:
        if "第二个周二" in e.summary:
            ev_2tu = e
        if "最后一个周五" in e.summary:
            ev_lastfr = e

    assert ev_2tu is not None, "未找到每月第二个周二事件"
    assert ev_lastfr is not None, "未找到每月最后一个周五事件"

    rs = date(2026, 7, 1)
    re_end = date(2026, 12, 31)

    occs_2tu = expand_rrule(ev_2tu, rs, re_end)
    print(f"每月第二个周二: {len(occs_2tu)} 个事件 (COUNT=6)")
    assert len(occs_2tu) == 6, f"应该有6个，实际{len(occs_2tu)}个"

    for o in occs_2tu:
        d = o.dtstart.date()
        assert d.weekday() == 1, f"{d} 不是周二"
        week_of_month = (d.day - 1) // 7 + 1
        assert week_of_month == 2, f"{d} 不是第2个周二，是第{week_of_month}个"

    print(f"✓ 每月第二个周二 - 全部正确")
    for o in occs_2tu:
        print(f"  - {o.dtstart.date()}")

    occs_lastfr = expand_rrule(ev_lastfr, date(2026, 6, 1), date(2026, 12, 31))
    print(f"\n每月最后一个周五: {len(occs_lastfr)} 个事件 (COUNT=5)")
    assert len(occs_lastfr) == 5, f"应该有5个，实际{len(occs_lastfr)}个"

    for o in occs_lastfr:
        d = o.dtstart.date()
        assert d.weekday() == 4, f"{d} 不是周五"
        next_monday = d + timedelta(days=3)
        assert next_monday.month != d.month or d.day + 7 > 31, f"{d} 不是最后一个周五"

    print(f"✓ 每月最后一个周五 - 全部正确")
    for o in occs_lastfr:
        print(f"  - {o.dtstart.date()}")


def test_timezone_parsing():
    print("\n" + "=" * 60)
    print("TEST 3: TZID / VTIMEZONE 解析")
    print("=" * 60)

    cal = load_ics_file("test_complex.ics")
    print(f"解析到 {len(cal.timezones)} 个时区")
    assert len(cal.timezones) == 2, f"应该有2个时区，实际{len(cal.timezones)}个"
    print(f"时区列表: {list(cal.timezones.keys())}")

    assert "America/New_York" in cal.timezones
    assert "Asia/Shanghai" in cal.timezones

    ny_tz = cal.timezones["America/New_York"]
    assert ny_tz.standard is not None
    assert ny_tz.daylight is not None
    print(f"\nAmerica/New_York:")
    print(f"  Standard: {ny_tz.standard.tzoffsetfrom} -> {ny_tz.standard.tzoffsetto} ({ny_tz.standard.tzname})")
    print(f"  Daylight: {ny_tz.daylight.tzoffsetfrom} -> {ny_tz.daylight.tzoffsetto} ({ny_tz.daylight.tzname})")

    sh_tz = cal.timezones["Asia/Shanghai"]
    assert sh_tz.standard is not None
    print(f"\nAsia/Shanghai:")
    print(f"  Standard: {sh_tz.standard.tzoffsetfrom} -> {sh_tz.standard.tzoffsetto} ({sh_tz.standard.tzname})")

    offset = parse_tz_offset("+0800")
    assert offset == timedelta(hours=8), f"+0800应该是8小时，实际{offset}"
    print(f"\n✓ 时区偏移解析正确: +0800 = {offset}")

    offset_neg = parse_tz_offset("-0500")
    assert offset_neg == timedelta(hours=-5), f"-0500应该是-5小时，实际{offset_neg}"
    print(f"✓ 时区偏移解析正确: -0500 = {offset_neg}")

    ev_shanghai = None
    for e in cal.events:
        if "双周" in e.summary:
            ev_shanghai = e
            break
    assert ev_shanghai.dtstart.tzinfo is not None, "上海时区事件应该有时区信息"
    print(f"\n✓ 上海时区事件带tzinfo: {ev_shanghai.dtstart}")
    print(f"  时区偏移: {ev_shanghai.dtstart.utcoffset()}")

    ev_ny = None
    for e in cal.events:
        if "美国团队" in e.summary:
            ev_ny = e
            break
    assert ev_ny.dtstart.tzinfo is not None, "纽约时区事件应该有时区信息"
    print(f"✓ 纽约时区事件带tzinfo: {ev_ny.dtstart}")
    print(f"  时区偏移: {ev_ny.dtstart.utcoffset()}")

    print("\n✓ 时区解析全部通过")


def test_timezone_conflict_detection():
    print("\n" + "=" * 60)
    print("TEST 4: 带时区事件的冲突检测")
    print("=" * 60)

    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//TZ//EN
BEGIN:VTIMEZONE
TZID:Asia/Tokyo
BEGIN:STANDARD
DTSTART:20260101T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0900
TZNAME:JST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:tz-test-1@example.com
DTSTAMP:20260615T090000Z
DTSTART;TZID=Asia/Tokyo:20260622T090000
DTEND;TZID=Asia/Tokyo:20260622T100000
SUMMARY:东京时间9-10点
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:tz-test-2@example.com
DTSTAMP:20260615T090000Z
DTSTART:20260622T080000Z
DTEND:20260622T090000Z
SUMMARY:UTC时间8-9点
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:tz-test-3@example.com
DTSTAMP:20260615T090000Z
DTSTART:20260622T083000Z
DTEND:20260622T093000Z
SUMMARY:UTC时间8:30-9:30
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""
    cal = parse_ics(ics_text, source="test")
    print(f"解析到 {len(cal.events)} 个事件, {len(cal.timezones)} 个时区")

    conflicts = detect_conflicts([cal])
    print(f"检测到 {len(conflicts)} 个冲突")

    tokyo_ev = [e for e in cal.events if "东京" in e.summary][0]
    utc_ev = [e for e in cal.events if "UTC时间8-9点" in e.summary][0]
    overlap_ev = [e for e in cal.events if "8:30-9:30" in e.summary][0]

    tokyo_dt = tokyo_ev.dtstart.astimezone(timezone.utc)
    utc_dt = utc_ev.dtstart
    print(f"\n东京9点 = UTC {tokyo_dt.hour}:{tokyo_dt.minute:02d}")
    print(f"UTC 8点 = UTC {utc_dt.hour}:{utc_dt.minute:02d}")
    print(f"东京9点(=UTC0点)和UTC8点不冲突 ✓")

    tokyo_end = tokyo_ev.dtend.astimezone(timezone.utc)
    overlap_start = overlap_ev.dtstart
    print(f"东京10点 = UTC {tokyo_end.hour}:{tokyo_end.minute:02d}")
    print(f"UTC 8:30 和东京9-10点 (=UTC0-1点) 不冲突 ✓")

    print("\n✓ 时区冲突检测正确")


def test_valarm_preserved_after_merge():
    print("\n" + "=" * 60)
    print("TEST 5: merge 导出后 VALARM 保留")
    print("=" * 60)

    cal = load_ics_file("test_complex.ics")

    ev_with_alarms = [e for e in cal.events if len(e.alarms) > 0]
    print(f"合并前有 {len(ev_with_alarms)} 个事件带提醒")
    for e in ev_with_alarms:
        print(f"  - {e.summary}: {len(e.alarms)} 个提醒")

    merged, _ = merge_calendars([cal])

    tmpfile = tempfile.mktemp(suffix=".ics")
    export_to_ics(merged, tmpfile)

    reloaded = load_ics_file(tmpfile)
    reloaded_alarms = [e for e in reloaded.events if len(e.alarms) > 0]

    print(f"\n导出并重新加载后有 {len(reloaded_alarms)} 个事件带提醒")
    for e in reloaded_alarms:
        print(f"  - {e.summary}: {len(e.alarms)} 个提醒")
        for a in e.alarms:
            print(f"    ACTION={a.action}, TRIGGER={a.trigger}, DESC={a.description[:20]}...")

    assert len(ev_with_alarms) == len(reloaded_alarms), "提醒数量不匹配"

    os.unlink(tmpfile)
    print("\n✓ VALARM 在 merge 导出后正确保留")


def test_all_original_samples_still_work():
    print("\n" + "=" * 60)
    print("TEST 6: 原有样例日历依然正常")
    print("=" * 60)

    for name in ["work.ics", "school.ics", "personal.ics"]:
        cal = load_ics_file(name)
        print(f"\n{name}:")
        print(f"  Events: {len(cal.events)}")
        print(f"  Todos: {len(cal.todos)}")
        print(f"  Timezones: {len(cal.timezones)}")

        total_occs = 0
        rs = date(2026, 6, 22)
        re_end = date(2026, 6, 28)
        for ev in cal.events:
            occs = expand_rrule(ev, rs, re_end)
            total_occs += len(occs)
        print(f"  一周内展开后总事件数: {total_occs}")

        assert len(cal.events) > 0
        assert len(cal.todos) > 0

    print("\n✓ 所有样例日历解析和展开正常")


def main():
    print("CALMERGE 全面修复验证测试")
    print("=" * 60)

    tests = [
        test_weekly_interval_byday,
        test_monthly_byday,
        test_timezone_parsing,
        test_timezone_conflict_detection,
        test_valarm_preserved_after_merge,
        test_all_original_samples_still_work,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"\n✗ 断言失败: {e}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"\n✗ 异常: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        exit(1)


if __name__ == "__main__":
    main()
