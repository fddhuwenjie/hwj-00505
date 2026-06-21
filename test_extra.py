#!/usr/bin/env python3
"""补充测试：导出时区、merge时区保留、更多RRULE场景"""

import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from calmerge import (
    parse_ics, load_ics_file, expand_rrule, export_to_ics,
    merge_calendars, detect_conflicts,
)

passed = 0
failed = 0

def run_test(name, test_func):
    global passed, failed
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        test_func()
        print(f"✓ {name} 通过")
        passed += 1
    except Exception as e:
        print(f"✗ {name} 失败: {e}")
        import traceback
        traceback.print_exc()
        failed += 1


def test_export_vtimezone():
    """测试导出的ICS包含VTIMEZONE定义"""
    cal = load_ics_file("test_complex.ics")
    
    assert len(cal.timezones) > 0, "应该有时区定义"
    
    export_to_ics(cal, "test_export_tz.ics")
    
    with open("test_export_tz.ics", "r") as f:
        content = f.read()
    
    assert "BEGIN:VTIMEZONE" in content, "导出应包含BEGIN:VTIMEZONE"
    assert "TZID:Asia/Shanghai" in content, "导出应包含Asia/Shanghai时区"
    assert "TZID:America/New_York" in content, "导出应包含America/New_York时区"
    assert "BEGIN:STANDARD" in content, "导出应包含STANDARD部分"
    assert "BEGIN:DAYLIGHT" in content, "导出应包含DAYLIGHT部分"
    
    assert "DTSTART;TZID=Asia/Shanghai:" in content, "导出事件应带TZID=Asia/Shanghai"
    assert "DTSTART;TZID=America/New_York:" in content, "导出事件应带TZID=America/New_York"
    
    print("  ✓ 导出包含VTIMEZONE定义")
    print("  ✓ 导出包含STANDARD和DAYLIGHT")
    print("  ✓ 导出事件带TZID参数")


def test_reimport_exported():
    """测试导出后重新导入，时区和事件信息一致"""
    cal_orig = load_ics_file("test_complex.ics")
    
    export_to_ics(cal_orig, "test_reimport.ics")
    cal_reimported = load_ics_file("test_reimport.ics")
    
    assert len(cal_reimported.timezones) == len(cal_orig.timezones), "时区数量应一致"
    
    for tzid in cal_orig.timezones:
        assert tzid in cal_reimported.timezones, f"重新导入应包含时区{tzid}"
    
    orig_with_tz = [e for e in cal_orig.events if e.dtstart_tzid]
    reimp_with_tz = [e for e in cal_reimported.events if e.dtstart_tzid]
    assert len(orig_with_tz) == len(reimp_with_tz), "带TZID的事件数量应一致"
    
    orig_ev = [e for e in cal_orig.events if e.summary == "美国团队早会(纽约时区)"]
    reimp_ev = [e for e in cal_reimported.events if e.summary == "美国团队早会(纽约时区)"]
    assert len(orig_ev) == 1 and len(reimp_ev) == 1, "应找到对应事件"
    
    assert orig_ev[0].dtstart_tzid == reimp_ev[0].dtstart_tzid, "TZID应一致"
    assert orig_ev[0].summary == reimp_ev[0].summary, "SUMMARY应一致"
    assert orig_ev[0].dtstart_tzid == "America/New_York", "应该是纽约时区"
    
    print("  ✓ 重新导入后时区数量一致")
    print("  ✓ 重新导入后带TZID事件数量一致")
    print("  ✓ 事件关键信息一致")


def test_merge_timezones():
    """测试merge时合并不同日历的时区"""
    ics1 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Tokyo
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0900
TZNAME:JST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:test-tokyo-1
DTSTAMP:20260601T000000Z
DTSTART;TZID=Asia/Tokyo:20260615T100000
DTEND;TZID=Asia/Tokyo:20260615T110000
SUMMARY:东京事件
END:VEVENT
END:VCALENDAR
"""
    
    ics2 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Europe/London
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0000
TZOFFSETTO:+0000
TZNAME:GMT
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:test-london-1
DTSTAMP:20260601T000000Z
DTSTART;TZID=Europe/London:20260615T100000
DTEND;TZID=Europe/London:20260615T110000
SUMMARY:伦敦事件
END:VEVENT
END:VCALENDAR
"""
    
    cal1 = parse_ics(ics1, source="tokyo.ics")
    cal2 = parse_ics(ics2, source="london.ics")
    
    assert "Asia/Tokyo" in cal1.timezones
    assert "Europe/London" in cal2.timezones
    
    merged, conflicts = merge_calendars([cal1, cal2])
    
    assert "Asia/Tokyo" in merged.timezones, "合并后应包含Asia/Tokyo"
    assert "Europe/London" in merged.timezones, "合并后应包含Europe/London"
    assert len(merged.timezones) == 2, "合并后应有2个时区"
    
    tokyo_events = [e for e in merged.events if e.dtstart_tzid == "Asia/Tokyo"]
    london_events = [e for e in merged.events if e.dtstart_tzid == "Europe/London"]
    assert len(tokyo_events) == 1, "应有1个东京时区事件"
    assert len(london_events) == 1, "应有1个伦敦时区事件"
    
    print("  ✓ Merge后时区正确合并")
    print("  ✓ 各事件保留各自的TZID")


def test_weekly_byday_multiple():
    """测试WEEKLY+BYDAY多日（周一、周三、周五）+INTERVAL"""
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:weekly-multi-1
DTSTAMP:20260601T000000Z
DTSTART:20260602T090000
DTEND:20260602T100000
SUMMARY:周一周三周五课程
RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE,FR;COUNT=10
END:VEVENT
END:VCALENDAR
"""
    
    cal = parse_ics(ics_text, source="test")
    event = cal.events[0]
    
    range_start = date(2026, 6, 1)
    range_end = date(2026, 7, 31)
    occurrences = expand_rrule(event, range_start, range_end)
    
    assert len(occurrences) == 10, f"应该有10个事件，实际{len(occurrences)}"
    
    weekdays = [ev.dtstart.weekday() for ev in occurrences]
    for wd in weekdays:
        assert wd in [0, 2, 4], f"应该是周一(0)、周三(2)、周五(4)，实际{wd}"
    
    assert occurrences[0].dtstart.date() == date(2026, 6, 3), f"第一个应该是6月3日(周三)，实际{occurrences[0].dtstart.date()}"
    assert occurrences[0].dtstart.weekday() == 2, "第一个事件应该是周三"
    
    for i in range(len(occurrences) - 1):
        diff = (occurrences[i+1].dtstart.date() - occurrences[i].dtstart.date()).days
        assert diff in [2, 3], f"间隔应该是2天(周三到周五)或3天(周五到下周一)，实际{diff}天"
    
    print(f"  ✓ 共{len(occurrences)}个事件")
    print(f"  ✓ 都在周一/周三/周五")
    print(f"  第1个: {occurrences[0].dtstart.date()} (周三)")
    print(f"  第2个: {occurrences[1].dtstart.date()} (周五)")
    print(f"  第3个: {occurrences[2].dtstart.date()} (周一)")
    print(f"  最后1个: {occurrences[-1].dtstart.date()} (周{occurrences[-1].dtstart.weekday()+1})")


def test_monthly_byday_negative():
    """测试MONTHLY+BYDAY=-1SU（每月最后一个周日）"""
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:monthly-last-sun-1
DTSTAMP:20260601T000000Z
DTSTART:20260629T100000
DTEND:20260629T120000
SUMMARY:每月最后一个周日总结
RRULE:FREQ=MONTHLY;BYDAY=-1SU;COUNT=6
END:VEVENT
END:VCALENDAR
"""
    
    cal = parse_ics(ics_text, source="test")
    event = cal.events[0]
    
    range_start = date(2026, 6, 1)
    range_end = date(2026, 12, 31)
    occurrences = expand_rrule(event, range_start, range_end)
    
    assert len(occurrences) == 6, f"应该有6个事件，实际{len(occurrences)}"
    
    import calendar
    for ev in occurrences:
        d = ev.dtstart.date()
        assert d.weekday() == 6, f"应该是周日(6)，实际{d.weekday()}"
        last_day = calendar.monthrange(d.year, d.month)[1]
        last_sunday = d.day > last_day - 7
        assert last_sunday, f"{d} 应该是该月最后一个周日"
    
    print(f"  ✓ 共{len(occurrences)}个事件")
    for ev in occurrences:
        print(f"    - {ev.dtstart.date()} (周日)")


def test_daily_interval():
    """测试DAILY+INTERVAL（每隔一天）"""
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:daily-interval-1
DTSTAMP:20260601T000000Z
DTSTART:20260601T080000
DTEND:20260601T083000
SUMMARY:隔日晨跑
RRULE:FREQ=DAILY;INTERVAL=2;COUNT=10
END:VEVENT
END:VCALENDAR
"""
    
    cal = parse_ics(ics_text, source="test")
    event = cal.events[0]
    
    range_start = date(2026, 6, 1)
    range_end = date(2026, 6, 30)
    occurrences = expand_rrule(event, range_start, range_end)
    
    assert len(occurrences) == 10, f"应该有10个事件，实际{len(occurrences)}"
    
    for i in range(len(occurrences) - 1):
        diff = (occurrences[i+1].dtstart.date() - occurrences[i].dtstart.date()).days
        assert diff == 2, f"间隔应该是2天，实际{diff}天"
    
    print(f"  ✓ 共{len(occurrences)}个事件")
    print(f"  ✓ 间隔都是2天")
    print(f"  第1个: {occurrences[0].dtstart.date()}")
    print(f"  最后1个: {occurrences[-1].dtstart.date()}")


def test_timezone_conflict_detection():
    """测试不同时区事件的冲突检测准确性"""
    ics1 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:meeting-sh-1
DTSTAMP:20260601T000000Z
DTSTART;TZID=Asia/Shanghai:20260615T090000
DTEND;TZID=Asia/Shanghai:20260615T100000
SUMMARY:上海会议
END:VEVENT
END:VCALENDAR
"""
    
    ics2 = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Tokyo
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0900
TZOFFSETTO:+0900
TZNAME:JST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:meeting-tk-1
DTSTAMP:20260601T000000Z
DTSTART;TZID=Asia/Tokyo:20260615T100000
DTEND;TZID=Asia/Tokyo:20260615T110000
SUMMARY:东京会议
END:VEVENT
END:VCALENDAR
"""
    
    cal1 = parse_ics(ics1, source="sh.ics")
    cal2 = parse_ics(ics2, source="tk.ics")
    
    conflicts = detect_conflicts([cal1, cal2])
    
    assert len(conflicts) == 1, f"应该检测到1个冲突，实际{len(conflicts)}"
    
    sh_start = cal1.events[0].dtstart
    tk_start = cal2.events[0].dtstart
    
    sh_utc = sh_start.astimezone(timezone.utc)
    tk_utc = tk_start.astimezone(timezone.utc)
    
    assert sh_utc == tk_utc, "上海9点 = 东京10点（UTC 1:00）"
    
    print(f"  ✓ 检测到{len(conflicts)}个冲突")
    print(f"  上海9点(CST) = UTC {sh_utc.hour}:{sh_utc.minute:02d}")
    print(f"  东京10点(JST) = UTC {tk_utc.hour}:{tk_utc.minute:02d}")
    print(f"  ✓ 不同时区事件正确检测冲突")


def test_exdate_with_timezone():
    """测试带时区的EXDATE"""
    ics_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:exdate-tz-1
DTSTAMP:20260601T000000Z
DTSTART;TZID=Asia/Shanghai:20260601T090000
DTEND;TZID=Asia/Shanghai:20260601T100000
SUMMARY:每日例会(有例外)
RRULE:FREQ=DAILY;COUNT=5
EXDATE;TZID=Asia/Shanghai:20260603T090000
END:VEVENT
END:VCALENDAR
"""
    
    cal = parse_ics(ics_text, source="test")
    event = cal.events[0]
    
    assert len(event.exdate) == 1, "应该有1个EXDATE"
    assert event.exdate[0].tzinfo is not None, "EXDATE应该带时区"
    
    range_start = date(2026, 6, 1)
    range_end = date(2026, 6, 10)
    occurrences = expand_rrule(event, range_start, range_end)
    
    dates = [ev.dtstart.date() for ev in occurrences]
    
    assert date(2026, 6, 3) not in dates, "6月3日应该被排除"
    assert date(2026, 6, 1) in dates, "6月1日应该存在"
    assert date(2026, 6, 2) in dates, "6月2日应该存在"
    
    for ev in occurrences:
        assert ev.dtstart.tzinfo is not None, "展开后的事件应该带时区"
    
    print(f"  ✓ 共{len(occurrences)}个事件")
    print(f"  ✓ 6月3日正确排除")
    print(f"  ✓ 所有展开事件都带时区信息")
    print(f"  日期: {[d.isoformat() for d in dates]}")


if __name__ == "__main__":
    print("CALMERGE 补充场景验证测试")
    print("=" * 60)
    
    run_test("导出VTIMEZONE和TZID参数", test_export_vtimezone)
    run_test("导出后重新导入一致性", test_reimport_exported)
    run_test("Merge合并不同日历时区", test_merge_timezones)
    run_test("WEEKLY+BYDAY多日展开", test_weekly_byday_multiple)
    run_test("MONTHLY+BYDAY=-1SU最后一个周日", test_monthly_byday_negative)
    run_test("DAILY+INTERVAL隔日", test_daily_interval)
    run_test("不同时区事件冲突检测", test_timezone_conflict_detection)
    run_test("带时区EXDATE", test_exdate_with_timezone)
    
    print(f"\n{'='*60}")
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print(f"{'='*60}")
    
    import os
    for f in ["test_export_tz.ics", "test_reimport.ics"]:
        if os.path.exists(f):
            os.remove(f)
    
    sys.exit(0 if failed == 0 else 1)
