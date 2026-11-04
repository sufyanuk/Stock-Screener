from datetime import date, datetime, timezone

from app import market_calendar as mc
from app.tasks import should_run


def test_holidays_early_close_and_dst():
    assert not mc.is_session(date(2026, 11, 26))  # Thanksgiving
    o, c = mc.session_times(date(2026, 11, 27))  # early close
    assert (o.hour, o.minute, c.hour) == (9, 30, 13)
    # 09:30 New York is 14:30 UTC in winter and 13:30 UTC in summer
    assert mc.session_times(date(2026, 1, 5))[0].astimezone(timezone.utc).hour == 14
    assert mc.session_times(date(2026, 7, 6))[0].astimezone(timezone.utc).hour == 13


def test_scheduler_skips_closed_market():
    assert not should_run("close", datetime(2026, 11, 26, 21, 20, tzinfo=timezone.utc))  # holiday
    assert not should_run("intraday", datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc))  # after early close
    assert should_run("intraday", datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc))
    assert mc.last_completed_session(datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)) == date(2026, 11, 27)
