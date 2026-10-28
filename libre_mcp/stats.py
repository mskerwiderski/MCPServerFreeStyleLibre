"""Glucose statistics on (ts, mg/dL) series. Pure functions, no I/O.

The metrics and ranges follow the international consensus on CGM reporting
(Battelino et al., Diabetes Care 2019): time in ranges <54, 54-69, 70-180,
181-250, >250 mg/dL, mean, CV (target <= 36 %), GMI, and at least 70 % data
coverage for a period to count as representative.

Percentages are shares of readings. That equals shares of time as long as
the readings are evenly spaced, which sensor history is (every 5 minutes
with a Libre 3, every 15 with older sensors).
"""

from __future__ import annotations

import math
import statistics

BANDS = (("very_low", None, 54), ("low", 54, 70), ("in_range", 70, 181),
         ("high", 181, 251), ("very_high", 251, None))
MAX_GAP_S = 30 * 60              # a longer gap ends an episode: no data is not "still low"
NICE_MINUTES = (5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 720, 1440)


def interval_s(points: list[tuple[int, int]]) -> int:
    """The usual spacing of the series (median gap), 15 minutes if unknown."""
    gaps = [b[0] - a[0] for a, b in zip(points, points[1:]) if b[0] > a[0]]
    return int(statistics.median(gaps)) if gaps else 900


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def summarize(points: list[tuple[int, int]], period_s: int,
              target: tuple[int, int] | None = None) -> dict:
    values = [mg for _, mg in points]
    n = len(values)
    if not n:
        return {"readings": 0}
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    step = interval_s(points)
    out = {
        "readings": n,
        "coverage_pct": min(100.0, _pct(n * step, period_s)),
        "mean": round(mean),
        "median": round(statistics.median(values)),
        "sd": round(sd, 1),
        "cv_pct": round(100 * sd / mean, 1),
        "gmi_pct": round(3.31 + 0.02392 * mean, 1),
        "min": min(values),
        "max": max(values),
        "time_in_ranges_pct": {
            name: _pct(sum(1 for v in values
                           if (lo is None or v >= lo) and (hi is None or v < hi)), n)
            for name, lo, hi in BANDS},
    }
    if target:
        lo, hi = target
        out["own_target"] = {"range": f"{lo}-{hi}",
                             "in_target_pct": _pct(sum(1 for v in values if lo <= v <= hi), n)}
    return out


def percentile(sorted_values: list[int], q: float) -> float:
    if not sorted_values:
        return math.nan
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def daily_pattern(points_by_hour: dict[int, list[int]]) -> list[dict]:
    """Ambulatory glucose profile: percentiles per hour of the day."""
    rows = []
    for hour in range(24):
        vals = sorted(points_by_hour.get(hour, []))
        if not vals:
            continue
        rows.append({"hour": hour, "n": len(vals),
                     **{f"p{round(q * 100)}": round(percentile(vals, q))
                        for q in (0.05, 0.25, 0.5, 0.75, 0.95)}})
    return rows


def episodes(points: list[tuple[int, int]], below: int | None = None,
             above: int | None = None, min_minutes: int = 15) -> list[dict]:
    """Stretches below `below` (or above `above`) lasting at least
    `min_minutes`. Duration counts each reading as one sensor interval."""
    assert (below is None) != (above is None)
    step = interval_s(points)
    out: list[dict] = []
    run: list[tuple[int, int]] = []

    def close() -> None:
        if run:
            duration = run[-1][0] - run[0][0] + step
            if duration >= min_minutes * 60:
                extreme = (min if below is not None else max)(mg for _, mg in run)
                out.append({"start": run[0][0], "end": run[-1][0] + step,
                            "minutes": round(duration / 60), "extreme": extreme})
        run.clear()

    for ts, mg in points:
        hit = mg < below if below is not None else mg > above
        if run and ts - run[-1][0] > MAX_GAP_S:
            close()
        if hit:
            run.append((ts, mg))
        else:
            close()
    close()
    return out


def downsample(points: list[tuple[int, int]], max_points: int) -> tuple[int, list]:
    """At most max_points rows. Returns (bucket seconds, rows): raw
    [ts, mg] when the series is short enough (bucket 0), otherwise
    [bucket start, mean, min, max] per time bucket."""
    if len(points) <= max_points:
        return 0, [[ts, mg] for ts, mg in points]
    first, last = points[0][0], points[-1][0]
    # Round sizes that start on the clock (07:00, not 07:03); the smallest one
    # that stays within max_points, whole days beyond that.
    sizes = (*(m * 60 for m in NICE_MINUTES), *(d * 86400 for d in range(2, 4000)))
    bucket = next(b for b in sizes if (last - (first - first % b)) // b + 1 <= max_points)
    origin = first - first % bucket
    rows: dict[int, list[int]] = {}
    for ts, mg in points:
        rows.setdefault(origin + (ts - origin) // bucket * bucket, []).append(mg)
    return bucket, [[start, round(statistics.fmean(v)), min(v), max(v)]
                    for start, v in sorted(rows.items())]
