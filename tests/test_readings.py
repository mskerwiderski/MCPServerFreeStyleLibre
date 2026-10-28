"""Storage, the history limit, and the statistics."""
import time

import pytest

from libre_mcp import readings, stats, users


def test_the_same_reading_twice_is_stored_once(user_id):
    t = int(time.time()) - 86400
    assert readings.add(user_id, [(t, 100), (t + 900, 110)], "llu") == 2
    # the CSV has minutes only: 40 s off is the same reading
    assert readings.add(user_id, [(t + 40, 100), (t + 1800, 120), (t + 1800, 120)], "csv") == 1
    assert readings.between(user_id, t, t + 3600) == [(t, 100), (t + 900, 110), (t + 1800, 120)]


def test_history_limit(user_id):
    now = time.time()
    old, recent = int(now - 400 * 86400), int(now - 10 * 86400)
    users.set_history_months(user_id, 12)
    assert readings.add(user_id, [(old, 100), (recent, 100)], "csv") == 1

    users.set_history_months(user_id, 0)                  # no limit
    assert readings.add(user_id, [(old, 100)], "csv") == 1
    users.set_history_months(user_id, 3)
    assert readings.prune(user_id) == 1
    assert readings.summary(user_id)["count"] == 1


def test_only_offered_periods(user_id):
    with pytest.raises(ValueError):
        users.set_history_months(user_id, 7)


def series(values, step=900, start=0):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_summary_metrics():
    pts = series([50, 60, 100, 120, 150, 200, 260, 110, 100, 100])
    s = stats.summarize(pts, period_s=10 * 900, target=(70, 160))
    assert s["readings"] == 10 and s["coverage_pct"] == 100.0
    assert s["mean"] == 125 and s["gmi_pct"] == round(3.31 + 0.02392 * 125, 1)
    assert s["time_in_ranges_pct"] == {"very_low": 10.0, "low": 10.0, "in_range": 60.0,
                                       "high": 10.0, "very_high": 10.0}
    assert s["own_target"] == {"range": "70-160", "in_target_pct": 60.0}
    assert stats.summarize(pts, period_s=20 * 900)["coverage_pct"] == 50.0


def test_episodes():
    pts = series([100, 65, 60, 62, 100, 68, 100, 190, 200, 100])
    lows = stats.episodes(pts, below=70)
    assert lows == [{"start": 900, "end": 3600, "minutes": 45, "extreme": 60},
                    {"start": 4500, "end": 5400, "minutes": 15, "extreme": 68}]
    assert len(stats.episodes(pts, below=70, min_minutes=20)) == 1
    assert stats.episodes(pts, above=180)[0]["extreme"] == 200


def test_a_gap_ends_an_episode():
    pts = [(0, 60), (900, 60), (900 + 3 * 3600, 60)]
    assert len(stats.episodes(pts, below=70)) == 2


def test_daily_pattern():
    rows = stats.daily_pattern({6: [80, 90, 100, 110, 120], 7: [100]})
    assert rows[0] == {"hour": 6, "n": 5, "p5": 82, "p25": 90, "p50": 100, "p75": 110, "p95": 118}
    assert [r["hour"] for r in rows] == [6, 7]


def test_downsample():
    pts = series(list(range(100, 196)))                  # 96 readings, one day
    assert stats.downsample(pts, 100)[0] == 0
    bucket, rows = stats.downsample(pts, 24)
    assert bucket == 3600 and len(rows) == 24
    assert rows[0] == [0, 102, 100, 103]


def test_downsample_never_exceeds_the_limit():
    for offset in range(0, 3600, 60):                    # wherever the series starts
        pts = series(list(range(100, 244)), step=300, start=1_790_000_000 + offset)
        for limit in (10, 12, 24, 50):
            assert len(stats.downsample(pts, limit)[1]) <= limit
