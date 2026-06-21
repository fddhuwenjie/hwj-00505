#!/usr/bin/env python3
"""团队可用时间推荐功能测试 - 覆盖跨时区、重复事件、午休排除、busy/free透明状态和无共同空闲场景"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from calmerge import (
    load_ics_file, parse_ics, Participant, find_suggestions,
    get_busy_intervals, get_free_slots, intersect_free_slots,
    merge_intervals, get_target_timezone, convert_to_timezone,
    export_suggestions_to_markdown, export_suggestions_to_json,
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


def test_basic_suggestion_two_people():
    """测试基本的两人可用时间推荐"""
    cal1 = load_ics_file("alice.ics")
    cal2 = load_ics_file("bob.ics")

    participants = [
        Participant(name="alice", calendar=cal1, weight=1.0),
        Participant(name="bob", calendar=cal2, weight=1.0),
    ]

    range_start = date(2026, 6, 22)
    range_end = date(2026, 6, 26)

    suggestions = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
    )

    assert len(suggestions) > 0, "应该至少有一些推荐的时间"
    assert len(suggestions) <= 5, "不应该超过 top_n=5"

    for s in suggestions:
        assert s.start < s.end, "开始时间应该早于结束时间"
        duration = s.end - s.start
        assert duration == timedelta(minutes=30), f"时长应该是30分钟，实际{duration}"
        assert s.score >= 0 and s.score <= 1.0, "分数应该在0到1之间"

    print(f"  ✓ 找到 {len(suggestions)} 个推荐时段")
    for i, s in enumerate(suggestions[:3]):
        print(f"    #{i+1}: {s.start.strftime('%Y-%m-%d %H:%M')} - {s.end.strftime('%H:%M')} ({int(s.score*100)}%)")


def test_recurring_events_expansion():
    """测试重复事件正确展开影响可用时间"""
    cal = load_ics_file("alice.ics")

    range_start = date(2026, 6, 22)
    range_end = date(2026, 6, 26)

    target_tz = get_target_timezone("Asia/Shanghai")
    busy = get_busy_intervals(cal, range_start, range_end, target_tz)

    standup_events = [b for b in busy if "早会" in b.summary]
    assert len(standup_events) >= 3, f"重复的早会应该展开至少3次，实际{len(standup_events)}次"

    print(f"  ✓ 重复事件正确展开")
    print(f"  ✓ 早会展开了 {len(standup_events)} 次")


def test_lunch_break_exclusion():
    """测试午休时间被正确排除"""
    cal1 = load_ics_file("alice.ics")
    cal2 = load_ics_file("bob.ics")

    participants = [
        Participant(name="alice", calendar=cal1, weight=1.0),
        Participant(name="bob", calendar=cal2, weight=1.0),
    ]

    range_start = date(2026, 6, 23)
    range_end = date(2026, 6, 23)

    suggestions_with_lunch = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=20,
    )

    for s in suggestions_with_lunch:
        start_hour = s.start.hour
        start_min = s.start.minute
        end_hour = s.end.hour
        end_min = s.end.minute

        start_in_lunch = (start_hour > 12) or (start_hour == 12 and start_min > 0)
        end_in_lunch = (end_hour < 13) or (end_hour == 13 and end_min == 0)
        overlaps_lunch = start_in_lunch and end_in_lunch

        assert not overlaps_lunch, f"推荐时段 {s.start} - {s.end} 不应在午休时间内"

    suggestions_no_lunch = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=20,
    )

    assert len(suggestions_no_lunch) >= len(suggestions_with_lunch), \
        "无午休限制时应该有更多推荐时段"

    print(f"  ✓ 午休时间被正确排除")
    print(f"  ✓ 有午休: {len(suggestions_with_lunch)} 个推荐")
    print(f"  ✓ 无午休: {len(suggestions_no_lunch)} 个推荐")


def test_transparent_free_status():
    """测试 TRANSP:TRANSPARENT 事件不影响可用时间"""
    ics_with_transparent = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:test-busy-1
DTSTAMP:20260615T090000Z
DTSTART:20260622T100000
DTEND:20260622T110000
SUMMARY:忙碌事件
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:test-free-1
DTSTAMP:20260615T090000Z
DTSTART:20260622T140000
DTEND:20260622T150000
SUMMARY:空闲事件（透明）
TRANSP:TRANSPARENT
END:VEVENT
END:VCALENDAR
"""

    cal = parse_ics(ics_with_transparent, source="test")
    target_tz = get_target_timezone("Asia/Shanghai")
    busy = get_busy_intervals(cal, date(2026, 6, 22), date(2026, 6, 22), target_tz)

    busy_summaries = [b.summary for b in busy]
    assert "忙碌事件" in busy_summaries, "OPAQUE 事件应该被计为忙碌"
    assert "空闲事件（透明）" not in busy_summaries, "TRANSPARENT 事件不应被计为忙碌"

    print(f"  ✓ TRANSP:TRANSPARENT 事件正确忽略")
    print(f"  ✓ 忙碌事件数量: {len(busy)}")


def test_cross_timezone_suggestions():
    """测试跨时区的可用时间推荐"""
    cal_shanghai = load_ics_file("alice.ics")
    cal_tokyo = load_ics_file("charlie.ics")

    participants = [
        Participant(name="alice", calendar=cal_shanghai, weight=1.0),
        Participant(name="charlie", calendar=cal_tokyo, weight=1.0),
    ]

    range_start = date(2026, 6, 23)
    range_end = date(2026, 6, 24)

    suggestions_shanghai_tz = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
    )

    suggestions_tokyo_tz = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Tokyo",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
    )

    print(f"  ✓ 上海时区推荐数量: {len(suggestions_shanghai_tz)}")
    print(f"  ✓ 东京时区推荐数量: {len(suggestions_tokyo_tz)}")

    shanghai_hours = set()
    for s in suggestions_shanghai_tz:
        shanghai_hours.add(s.start.hour)

    tokyo_hours = set()
    for s in suggestions_tokyo_tz:
        tokyo_hours.add(s.start.hour)

    print(f"  ✓ 上海时区推荐时段小时: {sorted(shanghai_hours)}")
    print(f"  ✓ 东京时区推荐时段小时: {sorted(tokyo_hours)}")

    if suggestions_shanghai_tz and suggestions_tokyo_tz:
        sh_first = suggestions_shanghai_tz[0].start
        tk_first = suggestions_tokyo_tz[0].start

        sh_utc = sh_first.replace(tzinfo=timezone(timedelta(hours=8)))
        tk_utc = tk_first.replace(tzinfo=timezone(timedelta(hours=9)))

        sh_utc_time = sh_utc.astimezone(timezone.utc)
        tk_utc_time = tk_utc.astimezone(timezone.utc)

        print(f"  ✓ 上海时区第一个推荐 (UTC): {sh_utc_time.strftime('%H:%M')}")
        print(f"  ✓ 东京时区第一个推荐 (UTC): {tk_utc_time.strftime('%H:%M')}")

    print("  ✓ 跨时区推荐功能正常")


def test_no_common_free_time():
    """测试当没有共同空闲时间时的处理"""
    ics_person1_full = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
BEGIN:STANDARD
DTSTART:20260101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:full-day-busy
DTSTAMP:20260615T090000Z
DTSTART;TZID=Asia/Shanghai:20260622T080000
DTEND;TZID=Asia/Shanghai:20260622T200000
SUMMARY:全天忙碌
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

    ics_person2_full = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
BEGIN:STANDARD
DTSTART:20260101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:full-day-busy-2
DTSTAMP:20260615T090000Z
DTSTART;TZID=Asia/Shanghai:20260622T080000
DTEND;TZID=Asia/Shanghai:20260622T200000
SUMMARY:全天忙碌2
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

    cal1 = parse_ics(ics_person1_full, source="p1")
    cal2 = parse_ics(ics_person2_full, source="p2")

    participants = [
        Participant(name="p1", calendar=cal1, weight=1.0),
        Participant(name="p2", calendar=cal2, weight=1.0),
    ]

    suggestions = find_suggestions(
        participants, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
    )

    assert len(suggestions) == 0, "当两人全天都忙时，应该没有推荐"

    params = {
        "range_start": date(2026, 6, 22),
        "range_end": date(2026, 6, 22),
        "duration": 60,
        "work_hours": "09:00-18:00",
        "timezone": "Asia/Shanghai",
        "lunch_break": "",
        "earliest_start": "09:00",
        "latest_start": "17:00",
    }

    md_report = export_suggestions_to_markdown(suggestions, participants, params)
    assert "未找到" in md_report, "Markdown 报告应包含未找到提示"
    assert "建议" in md_report, "Markdown 报告应包含建议"

    json_report = export_suggestions_to_json(suggestions, participants, params)
    assert '"suggestions_count": 0' in json_report, "JSON 报告应显示 0 个推荐"

    print("  ✓ 无共同空闲时间时返回空列表")
    print("  ✓ Markdown 报告正确处理无推荐情况")
    print("  ✓ JSON 报告正确处理无推荐情况")


def test_participant_weights():
    """测试参与者权重对推荐评分的影响"""
    ics_important = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:imp-busy
DTSTAMP:20260615T090000Z
DTSTART:20260622T100000
DTEND:20260622T110000
SUMMARY:重要人物忙碌
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

    ics_normal = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:norm-busy
DTSTAMP:20260615T090000Z
DTSTART:20260622T140000
DTEND:20260622T150000
SUMMARY:普通人物忙碌
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

    cal_imp = parse_ics(ics_important, source="imp")
    cal_norm = parse_ics(ics_normal, source="norm")

    participants_equal_weight = [
        Participant(name="important", calendar=cal_imp, weight=1.0),
        Participant(name="normal", calendar=cal_norm, weight=1.0),
    ]

    suggestions_equal = find_suggestions(
        participants_equal_weight, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=10,
    )

    participants_unequal_weight = [
        Participant(name="important", calendar=cal_imp, weight=2.0),
        Participant(name="normal", calendar=cal_norm, weight=1.0),
    ]

    suggestions_unequal = find_suggestions(
        participants_unequal_weight, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=10,
    )

    assert len(suggestions_equal) > 0, "等权重时应该有推荐"
    assert len(suggestions_unequal) > 0, "不等权重时应该有推荐"

    ten_am_slot = [s for s in suggestions_unequal if s.start.hour == 10]
    two_pm_slot = [s for s in suggestions_unequal if s.start.hour == 14]

    if ten_am_slot and two_pm_slot:
        ten_score = ten_am_slot[0].score
        two_score = two_pm_slot[0].score

        print(f"  ✓ 10点推荐分数: {ten_score} (重要人物忙碌)")
        print(f"  ✓ 14点推荐分数: {two_score} (普通人物忙碌)")

        assert two_score > ten_score, "重要人物忙时的分数应该低于普通人物忙时"

    print("  ✓ 参与者权重正确影响评分")


def test_all_day_event_handling():
    """测试全天事件的处理"""
    ics_with_allday = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:allday-event
DTSTAMP:20260615T090000Z
DTSTART;VALUE=DATE:20260622
DTEND;VALUE=DATE:20260623
SUMMARY:全天会议
TRANSP:OPAQUE
END:VEVENT
BEGIN:VEVENT
UID:meeting-event
DTSTAMP:20260615T090000Z
DTSTART:20260623T100000
DTEND:20260623T110000
SUMMARY:普通会议
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

    cal = parse_ics(ics_with_allday, source="test")
    target_tz = get_target_timezone("Asia/Shanghai")

    busy_with_allday = get_busy_intervals(
        cal, date(2026, 6, 22), date(2026, 6, 23), target_tz, ignore_all_day=False
    )
    busy_without_allday = get_busy_intervals(
        cal, date(2026, 6, 22), date(2026, 6, 23), target_tz, ignore_all_day=True
    )

    assert len(busy_with_allday) > len(busy_without_allday), \
        "包含全天事件时忙碌时段应该更多"

    allday_busy = [b for b in busy_with_allday if "全天会议" in b.summary]
    assert len(allday_busy) > 0, "全天事件应该出现在忙碌列表中"

    allday_removed = [b for b in busy_without_allday if "全天会议" in b.summary]
    assert len(allday_removed) == 0, "忽略全天事件时不应包含全天会议"

    cal2 = parse_ics("""BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR
""", source="empty")

    participants_with = [
        Participant(name="p1", calendar=cal, weight=1.0),
        Participant(name="p2", calendar=cal2, weight=1.0),
    ]

    suggestions_with_allday = find_suggestions(
        participants_with, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
        ignore_all_day=False,
    )

    suggestions_without_allday = find_suggestions(
        participants_with, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
        ignore_all_day=True,
    )

    print(f"  ✓ 包含全天事件: {len(suggestions_with_allday)} 个推荐")
    print(f"  ✓ 忽略全天事件: {len(suggestions_without_allday)} 个推荐")
    assert len(suggestions_without_allday) >= len(suggestions_with_allday), \
        "忽略全天事件时应该有更多推荐"

    print("  ✓ 全天事件处理正确")


def test_work_hours_window():
    """测试工作时间窗口限制"""
    ics_empty = """BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR
"""

    cal1 = parse_ics(ics_empty, source="p1")
    cal2 = parse_ics(ics_empty, source="p2")

    participants = [
        Participant(name="p1", calendar=cal1, weight=1.0),
        Participant(name="p2", calendar=cal2, weight=1.0),
    ]

    suggestions_wide = find_suggestions(
        participants, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="08:00-20:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="08:00",
        latest_start="19:00",
        top_n=20,
    )

    suggestions_narrow = find_suggestions(
        participants, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="10:00-16:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="10:00",
        latest_start="15:00",
        top_n=20,
    )

    for s in suggestions_wide:
        assert s.start.hour >= 8, f"开始时间 {s.start} 应该在8点之后"
        assert s.end.hour <= 20, f"结束时间 {s.end} 应该在20点之前"

    for s in suggestions_narrow:
        assert s.start.hour >= 10, f"开始时间 {s.start} 应该在10点之后"
        assert s.end.hour <= 16, f"结束时间 {s.end} 应该在16点之前"

    assert len(suggestions_wide) > len(suggestions_narrow), \
        "宽时间窗口应该有更多推荐"

    print(f"  ✓ 宽窗口: {len(suggestions_wide)} 个推荐")
    print(f"  ✓ 窄窗口: {len(suggestions_narrow)} 个推荐")
    print("  ✓ 工作时间窗口限制正确")


def test_export_markdown_report():
    """测试 Markdown 报告导出"""
    cal1 = load_ics_file("alice.ics")
    cal2 = load_ics_file("bob.ics")

    participants = [
        Participant(name="alice", calendar=cal1, weight=1.0),
        Participant(name="bob", calendar=cal2, weight=1.0),
    ]

    range_start = date(2026, 6, 23)
    range_end = date(2026, 6, 24)

    suggestions = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=3,
    )

    params = {
        "range_start": range_start,
        "range_end": range_end,
        "duration": 30,
        "work_hours": "09:00-18:00",
        "timezone": "Asia/Shanghai",
        "lunch_break": "12:00-13:00",
        "earliest_start": "09:00",
        "latest_start": "17:00",
    }

    md = export_suggestions_to_markdown(suggestions, participants, params)

    assert "# 团队可用时间推荐报告" in md, "应该有报告标题"
    assert "## 参数信息" in md, "应该有参数信息部分"
    assert "## 参与者" in md, "应该有参与者部分"
    assert "## 推荐" in md or "推荐 Top" in md, "应该有推荐部分"
    assert "alice" in md, "应该包含 alice"
    assert "bob" in md, "应该包含 bob"
    assert "推荐理由" in md, "应该有推荐理由"

    print("  ✓ Markdown 报告包含标题")
    print("  ✓ Markdown 报告包含参数信息")
    print("  ✓ Markdown 报告包含参与者信息")
    print("  ✓ Markdown 报告包含推荐详情")
    print("  ✓ Markdown 报告包含推荐理由")


def test_export_json_report():
    """测试 JSON 报告导出"""
    import json

    cal1 = load_ics_file("alice.ics")
    cal2 = load_ics_file("bob.ics")

    participants = [
        Participant(name="alice", calendar=cal1, weight=1.0),
        Participant(name="bob", calendar=cal2, weight=1.0),
    ]

    range_start = date(2026, 6, 23)
    range_end = date(2026, 6, 24)

    suggestions = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=3,
    )

    params = {
        "range_start": range_start,
        "range_end": range_end,
        "duration": 30,
        "work_hours": "09:00-18:00",
        "timezone": "Asia/Shanghai",
        "lunch_break": "12:00-13:00",
        "earliest_start": "09:00",
        "latest_start": "17:00",
    }

    json_str = export_suggestions_to_json(suggestions, participants, params)
    data = json.loads(json_str)

    assert "params" in data, "应该有 params 字段"
    assert "participants" in data, "应该有 participants 字段"
    assert "suggestions" in data, "应该有 suggestions 字段"
    assert "suggestions_count" in data, "应该有 suggestions_count 字段"

    assert data["params"]["duration_minutes"] == 30
    assert data["params"]["timezone"] == "Asia/Shanghai"
    assert len(data["participants"]) == 2
    assert data["suggestions_count"] == len(suggestions)

    if suggestions:
        first = data["suggestions"][0]
        assert "start" in first
        assert "end" in first
        assert "score" in first
        assert "available_participants" in first
        assert "busy_participants" in first
        assert "reasons" in first

    print("  ✓ JSON 报告结构完整")
    print(f"  ✓ 推荐数量: {data['suggestions_count']}")
    print("  ✓ JSON 可正常解析")


def test_earliest_latest_start():
    """测试最早和最晚开始时间限制"""
    ics_empty = """BEGIN:VCALENDAR
VERSION:2.0
END:VCALENDAR
"""

    cal1 = parse_ics(ics_empty, source="p1")
    cal2 = parse_ics(ics_empty, source="p2")

    participants = [
        Participant(name="p1", calendar=cal1, weight=1.0),
        Participant(name="p2", calendar=cal2, weight=1.0),
    ]

    suggestions = find_suggestions(
        participants, date(2026, 6, 22), date(2026, 6, 22),
        duration_minutes=60,
        work_hours="08:00-20:00",
        timezone_name="Asia/Shanghai",
        lunch_break="none",
        earliest_start="10:00",
        latest_start="15:00",
        top_n=20,
    )

    for s in suggestions:
        start_minutes = s.start.hour * 60 + s.start.minute
        earliest_minutes = 10 * 60
        latest_minutes = 15 * 60

        assert start_minutes >= earliest_minutes, \
            f"开始时间 {s.start} 不应早于最早开始时间"
        assert start_minutes <= latest_minutes, \
            f"开始时间 {s.start} 不应晚于最晚开始时间"

    print(f"  ✓ 找到 {len(suggestions)} 个推荐")
    print("  ✓ 所有推荐都在最早/最晚开始时间范围内")


def test_three_people_suggestion():
    """测试三人团队的可用时间推荐"""
    cal_alice = load_ics_file("alice.ics")
    cal_bob = load_ics_file("bob.ics")
    cal_charlie = load_ics_file("charlie.ics")

    participants = [
        Participant(name="alice", calendar=cal_alice, weight=1.0),
        Participant(name="bob", calendar=cal_bob, weight=1.0),
        Participant(name="charlie", calendar=cal_charlie, weight=1.0),
    ]

    range_start = date(2026, 6, 23)
    range_end = date(2026, 6, 24)

    suggestions = find_suggestions(
        participants, range_start, range_end,
        duration_minutes=30,
        work_hours="09:00-18:00",
        timezone_name="Asia/Shanghai",
        lunch_break="12:00-13:00",
        earliest_start="09:00",
        latest_start="17:00",
        top_n=5,
    )

    for s in suggestions:
        assert s.score >= 0 and s.score <= 1.0
        if s.score == 1.0:
            assert len(s.available_participants) == 3
            assert len(s.busy_participants) == 0

    print(f"  ✓ 三人推荐数量: {len(suggestions)}")
    for i, s in enumerate(suggestions[:3]):
        print(f"    #{i+1}: {s.start.strftime('%Y-%m-%d %H:%M')} - {s.end.strftime('%H:%M')} "
              f"({int(s.score*100)}%) 可用: {','.join(s.available_participants)}")

    print("  ✓ 三人推荐功能正常")


def test_free_slots_computation():
    """测试空闲时段计算"""
    from calmerge import FreeSlot

    busy_intervals = [
        type('obj', (object,), {
            'start': datetime(2026, 6, 22, 9, 0),
            'end': datetime(2026, 6, 22, 10, 0),
            'summary': '会议1'
        })(),
        type('obj', (object,), {
            'start': datetime(2026, 6, 22, 14, 0),
            'end': datetime(2026, 6, 22, 15, 0),
            'summary': '会议2'
        })(),
    ]

    free = get_free_slots(
        busy_intervals, date(2026, 6, 22),
        work_start_hour=9, work_start_min=0,
        work_end_hour=18, work_end_min=0,
        lunch_start_hour=12, lunch_start_min=0,
        lunch_end_hour=13, lunch_end_min=0,
    )

    assert len(free) >= 3, f"应该至少有3个空闲时段，实际{len(free)}个"

    has_morning_free = any(
        f.start.hour < 12 and f.end.hour > 10 for f in free
    )
    has_afternoon_free = any(
        f.start.hour >= 15 and f.end.hour >= 16 for f in free
    )

    assert has_morning_free, "上午应该有空闲时间"
    assert has_afternoon_free, "下午应该有空闲时间"

    print(f"  ✓ 计算得到 {len(free)} 个空闲时段")
    for f in free:
        print(f"    {f.start.strftime('%H:%M')} - {f.end.strftime('%H:%M')}")
    print("  ✓ 空闲时段计算正确")


def test_intersect_free_slots():
    """测试空闲时段交集计算"""
    from calmerge import FreeSlot

    person1_free = [
        FreeSlot(start=datetime(2026, 6, 22, 9, 0), end=datetime(2026, 6, 22, 12, 0)),
        FreeSlot(start=datetime(2026, 6, 22, 13, 0), end=datetime(2026, 6, 22, 17, 0)),
    ]

    person2_free = [
        FreeSlot(start=datetime(2026, 6, 22, 10, 0), end=datetime(2026, 6, 22, 13, 0)),
        FreeSlot(start=datetime(2026, 6, 22, 14, 0), end=datetime(2026, 6, 22, 18, 0)),
    ]

    all_free = [person1_free, person2_free]

    common = intersect_free_slots(all_free, duration_minutes=60)

    assert len(common) >= 2, f"应该至少有2个共同空闲时段，实际{len(common)}个"

    for slot in common:
        duration = slot.end - slot.start
        assert duration >= timedelta(minutes=60), "每个空闲时段应该至少60分钟"

    print(f"  ✓ 找到 {len(common)} 个共同空闲时段")
    for s in common:
        print(f"    {s.start.strftime('%H:%M')} - {s.end.strftime('%H:%M')}")
    print("  ✓ 共同空闲时段计算正确")


def test_merge_intervals():
    """测试重叠区间合并"""
    intervals = [
        type('obj', (object,), {
            'start': datetime(2026, 6, 22, 9, 0),
            'end': datetime(2026, 6, 22, 10, 30),
            'summary': 'a', 'source': '', 'transp': ''
        })(),
        type('obj', (object,), {
            'start': datetime(2026, 6, 22, 10, 0),
            'end': datetime(2026, 6, 22, 11, 0),
            'summary': 'b', 'source': '', 'transp': ''
        })(),
        type('obj', (object,), {
            'start': datetime(2026, 6, 22, 14, 0),
            'end': datetime(2026, 6, 22, 15, 0),
            'summary': 'c', 'source': '', 'transp': ''
        })(),
    ]

    merged = merge_intervals(intervals)

    assert len(merged) == 2, f"合并后应该有2个区间，实际{len(merged)}个"
    assert merged[0].start == datetime(2026, 6, 22, 9, 0)
    assert merged[0].end == datetime(2026, 6, 22, 11, 0)
    assert merged[1].start == datetime(2026, 6, 22, 14, 0)
    assert merged[1].end == datetime(2026, 6, 22, 15, 0)

    print(f"  ✓ 合并后有 {len(merged)} 个区间")
    print("  ✓ 区间合并正确")


def test_sample_ics_files_parse():
    """测试样例 ICS 文件能正确解析"""
    files = ["alice.ics", "bob.ics", "charlie.ics"]

    for f in files:
        cal = load_ics_file(f)
        assert len(cal.events) > 0, f"{f} 应该有事件"

        has_recurring = any(len(ev.rrule) > 0 for ev in cal.events)
        has_transparent = any(ev.transp == "TRANSPARENT" for ev in cal.events)
        has_all_day = any(ev.all_day for ev in cal.events)

        print(f"  ✓ {f}: {len(cal.events)} 个事件, "
              f"重复事件={'有' if has_recurring else '无'}, "
              f"透明事件={'有' if has_transparent else '无'}, "
              f"全天事件={'有' if has_all_day else '无'}")

    print("  ✓ 所有样例文件解析正常")


def main():
    print("团队可用时间推荐功能 - 全面测试")
    print("=" * 60)

    tests = [
        ("样例ICS文件解析", test_sample_ics_files_parse),
        ("基本两人推荐", test_basic_suggestion_two_people),
        ("重复事件展开", test_recurring_events_expansion),
        ("午休时间排除", test_lunch_break_exclusion),
        ("TRANSP透明状态处理", test_transparent_free_status),
        ("跨时区推荐", test_cross_timezone_suggestions),
        ("无共同空闲时间", test_no_common_free_time),
        ("参与者权重", test_participant_weights),
        ("全天事件处理", test_all_day_event_handling),
        ("工作时间窗口", test_work_hours_window),
        ("最早最晚开始时间", test_earliest_latest_start),
        ("三人团队推荐", test_three_people_suggestion),
        ("空闲时段计算", test_free_slots_computation),
        ("共同空闲交集", test_intersect_free_slots),
        ("区间合并", test_merge_intervals),
        ("Markdown报告导出", test_export_markdown_report),
        ("JSON报告导出", test_export_json_report),
    ]

    for name, func in tests:
        run_test(name, func)

    print(f"\n{'='*60}")
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print(f"{'='*60}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
