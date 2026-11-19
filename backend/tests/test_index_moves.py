"""Dashboard S&P 500 / Nasdaq-100 line: % change against the right previous close."""
from datetime import date, datetime, timezone

from app.main import index_move


def snap(trade, daily_bar_date, daily_close, prev_close, price):
    return {"price": price, "market_ts": trade, "daily_bar_date": daily_bar_date, "daily_close": daily_close,
            "prev_close": prev_close}


def test_pre_market_compares_with_yesterdays_close():
    # Thursday 8:00 am New York: today's daily bar not started, so dailyBar is Wednesday's (close 500)
    t = datetime(2026, 11, 19, 13, 0, tzinfo=timezone.utc)
    assert round(index_move(snap(t, date(2026, 11, 18), 500.0, 490.0, 505.0)), 4) == 1.0


def test_regular_session_compares_with_previous_close():
    # Thursday 11:00 am: dailyBar is Thursday's, prevDailyBar Wednesday's (close 500)
    t = datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc)
    assert round(index_move(snap(t, date(2026, 11, 19), 498.0, 500.0, 495.0)), 4) == -1.0


def test_missing_data_gives_none():
    assert index_move(snap(None, None, None, None, None)) is None


def test_endpoint_does_not_touch_the_database_and_survives_no_keys():
    from fastapi.testclient import TestClient

    import app.main as main

    main._INDEX_CACHE.update(at=0.0, out=None)
    r = TestClient(main.app).get("/api/index-moves")  # no database fixture: it must not need one
    assert r.status_code == 200 and "phase" in r.json()
