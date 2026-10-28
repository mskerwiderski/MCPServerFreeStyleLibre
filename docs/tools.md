# Tool reference

Eight tools, all read-only. Values are always **mg/dL**. Times in answers are
local wall-clock times (`2026-09-28T07:15`) in the user's time zone: in the
hosted mode the one set in MyAuth, standalone `$TZ` or the computer's.

All history comes from the server's own database (LibreLinkUp itself keeps
only 12 hours); only `get_current_glucose` asks LibreLinkUp live. The values
in the examples below are made up.

## Periods and sensors

The period tools - `get_glucose_readings`, `get_glucose_stats`,
`get_daily_pattern`, `list_glucose_episodes` - share three parameters:

| Parameter | Meaning |
|---|---|
| `date_from` | a local day (`2026-09-28`) or a local time (`2026-09-28T06:30`). Without it: the default period before `date_to` (or now) |
| `date_to` | a local day (included in full) or a local time (exact). Without it: now |
| `sensor` | a sensor's number from `list_sensors`, its serial number or its start day. Only that sensor's readings; without dates its whole run, with dates the overlap |

Times are what you want for a workout, a meal or a night: take the start and
end from the training app and pass them as `date_from`/`date_to`. A time with
an offset (`2026-09-28T06:30+02:00`) is taken as it is.

Every answer with readings carries `sensors`: which sensor measured which part
of the period, numbered as in `list_sensors`.

```json
"sensors": [
  {"sensor": 7, "serial": "0M00ABCDEF", "from": "2026-09-27T07:00",
   "to": "2026-09-28T06:55", "readings": 288}
]
```

## Errors

Failures come back as a readable message, not a generic tool error:

| Message (shortened) | Meaning |
|---|---|
| `LibreLinkUp is not connected yet - connect it on your MyAuth account page` | hosted: no LibreLinkUp connection in MyAuth |
| `not signed in to LibreLinkUp yet - run libre-mcp login …` | standalone: no session file |
| `The LibreLinkUp sign-in has expired …` | standalone: run `libre-mcp login` again |
| `No readings between … and …; stored readings cover … to …` | nothing stored for that period - the message says what is |
| `Sensor 3 was not worn then …` | `sensor` and dates do not overlap |
| `LibreLinkUp asked to pause until …` | Abbott locked the account briefly; the server waits |

---

## `whoami`

Who this connector belongs to, whose glucose it reads, how much history is
stored and what the collector did last. The tool to call when the others
report missing data. No parameters.

```json
{
  "user": "Anja",
  "glucose_of": "Anja Muster",
  "collector": {"connected": true, "last_success": "2026-09-28T07:14"},
  "history": {"readings": 41230, "first": "2026-03-02T10:05",
              "last": "2026-09-28T06:55", "sensors": 14, "kept_months": 24},
  "timezone": "Europe/Berlin"
}
```

`collector.connected` is `null` when MyAuth is not answering (hosted);
`last_error` and `myauth_error` appear when something failed. `kept_months` is
`"unlimited"` without a retention limit.

## `get_current_glucose`

The latest sensor glucose with its trend, asked live from LibreLinkUp. It is as
fresh as the phone's last upload - check `minutes_ago`. No parameters.

```json
{
  "mg_dl": 112, "trend": "rising", "measured_at": "2026-09-28T07:14",
  "minutes_ago": 1, "live": true, "target_range": "70-160"
}
```

`trend` is one of `falling fast`, `falling`, `stable`, `rising`, `rising fast`.
`flag` appears when the value is below or above the alarm limit set in the
Libre app; `target_range` is the person's own target range from the app. When
LibreLinkUp does not answer, the newest stored reading comes back with
`"live": false` and a `note` saying why.

## `get_glucose_readings`

The curve of a period, oldest first. Default period: the last 24 hours.

| Parameter | Default | |
|---|---|---|
| `date_from`, `date_to`, `sensor` | - | see above |
| `max_points` | 300 | at most this many rows (10 to 1000) |

Short periods come back as raw readings `[time, mg_dl]`:

```json
{
  "unit": "mg/dL", "from": "2026-09-28T06:00", "to": "2026-09-28T07:30",
  "sensors": [{"sensor": 7, "serial": "0M00ABCDEF", "from": "2026-09-28T06:00",
               "to": "2026-09-28T07:25", "readings": 18}],
  "columns": ["time", "mg_dl"], "bucket_minutes": null,
  "rows": [["2026-09-28T06:00", 104], ["2026-09-28T06:05", 107], "..."]
}
```

Longer ones are condensed into clock-aligned time buckets (5 minutes up to
whole days) with `columns` `["bucket_start", "mean", "min", "max"]` and
`bucket_minutes` set.

## `get_glucose_stats`

A period in the standard CGM metrics (international consensus 2019). Default
period: the last 14 days. Parameters: `date_from`, `date_to`, `sensor`.

```json
{
  "unit": "mg/dL", "from": "2026-09-14T07:15", "to": "2026-09-28T07:15",
  "readings": 3980, "coverage_pct": 98.7,
  "mean": 118, "median": 113, "sd": 27.4, "cv_pct": 23.2, "gmi_pct": 6.1,
  "min": 61, "max": 214,
  "time_in_ranges_pct": {"very_low": 0.0, "low": 1.2, "in_range": 92.9,
                         "high": 5.9, "very_high": 0.0},
  "own_target": {"range": "70-160", "in_target_pct": 89.4},
  "gmi_setting_days": 14,
  "sensors": ["..."]
}
```

| Field | Meaning |
|---|---|
| `coverage_pct` | share of the period with readings; below 70 the figures are not representative |
| `cv_pct` | coefficient of variation (SD / mean); 36 % or below counts as stable |
| `gmi_pct` | glucose management indicator: estimated HbA1c from the mean (3.31 + 0.02392 × mean) over this period |
| `time_in_ranges_pct` | `very_low` <54, `low` 54-69, `in_range` 70-180, `high` 181-250, `very_high` >250 mg/dL |
| `own_target` | the share within the person's own target range from the Libre app, when it is known |
| `gmi_setting_days` | the period the person chose for "their" GMI on the account page (hosted); `gmi_pct` here covers the requested period |

## `get_daily_pattern`

The typical day (ambulatory glucose profile): per local hour the 5th, 25th,
50th, 75th and 95th percentile of all readings in the period. Default period:
the last 14 days. Parameters: `date_from`, `date_to`, `sensor`.

```json
{
  "unit": "mg/dL", "from": "2026-09-14T07:15", "to": "2026-09-28T07:15",
  "sensors": ["..."],
  "hours": [
    {"hour": 0, "n": 168, "p5": 82, "p25": 94, "p50": 103, "p75": 112, "p95": 131},
    {"hour": 1, "n": 168, "p5": 79, "p25": 91, "p50": 100, "p75": 109, "p95": 127}
  ]
}
```

Hours without readings are left out.

## `list_glucose_episodes`

Lows and highs that last long enough to matter. Default period: the last 14
days.

| Parameter | Default | |
|---|---|---|
| `date_from`, `date_to`, `sensor` | - | see above |
| `low` | 70 | a low is below this; 54 for clinically significant lows |
| `high` | 180 | a high is above this; 250 for very high |
| `min_minutes` | 15 | shorter episodes are ignored |

```json
{
  "unit": "mg/dL", "from": "2026-09-14T07:15", "to": "2026-09-28T07:15",
  "sensors": ["..."],
  "lows": {"count": 1, "episodes": [
    {"start": "2026-09-21T03:10", "end": "2026-09-21T03:40", "minutes": 30,
     "extreme": 63, "sensor": 7}]},
  "highs": {"count": 2, "episodes": ["..."]}
}
```

`extreme` is the lowest value of a low, the highest of a high. A gap of more
than 30 minutes ends an episode - no data is not "still low". At most the 50
newest of each are listed; `count` is the full number.

## `get_sensor`

The sensor worn now (the newest one). No parameters.

```json
{
  "number": 14, "serial": "0M00ABCDEF", "start": "reported",
  "started_at": "2026-09-25T08:02", "last_reading": "2026-09-28T07:10",
  "days": 3.0, "status": "running", "readings": 860,
  "coverage_pct": 99.5, "mean": 117, "in_range_pct": 93.4, "below_70_pct": 1.0,
  "of": 14,
  "ends_at": {"libre_3": "2026-10-09T08:02", "libre_3_plus": "2026-10-10T08:02"}
}
```

`status` is `warming_up`, `running` or `ended`. `ends_at` gives both lifetimes
(a FreeStyle Libre 3 lasts 14 days, a Libre 3 Plus 15), since LibreLinkUp's
type code is undocumented. Values from the first hours and from the last day
of a sensor are the least accurate.

## `list_sensors`

Every sensor in the stored history, oldest first and numbered, each with the
same fields as `get_sensor` (without `of` and `ends_at`). No parameters.

```json
{"unit": "mg/dL", "count": 14, "sensors": [
  {"number": 1, "serial": null, "start": "history_start",
   "started_at": "2026-03-02T09:05", "last_reading": "2026-03-15T22:40",
   "days": 13.6, "status": "ended", "readings": 3901,
   "coverage_pct": 99.1, "mean": 121, "in_range_pct": 90.2, "below_70_pct": 0.8},
  "..."
]}
```

`start` says how the start is known:

| `start` | Meaning |
|---|---|
| `reported` | LibreLinkUp named the sensor and its activation (with serial number) |
| `estimated` | worn before the server knew it (LibreView CSV, older history): the change was recognised by the gap of at least an hour a new sensor leaves; start = first reading minus the warm-up hour, no serial number |
| `history_start` | it was running when the stored history begins and may have started earlier |

Numbers count the stored sensors: importing older readings or the retention
can shift them; start day and serial number stay. A sensor that ended clearly
before 14 (or 15) days failed or came off.
