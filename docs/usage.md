# Usage examples

What to ask, which tool answers, and what the numbers mean. The model picks
the tools itself; the tool names are only here so you know what happens. All
values are mg/dL; ask for mmol/L and the model converts (1 mmol/L = 18 mg/dL).

**Not a medical device.** The values come from an unofficial interface and can
be late, missing or wrong. Never base a treatment decision on them.

## Right now

> What is my glucose right now, and where is it heading?

`get_current_glucose`: the latest value with its trend arrow, live from
LibreLinkUp. Look at `minutes_ago` - the phone uploads in the background and
irregularly; opening the FreeStyle Libre app usually sends a fresh value.

## A period

> How much time was I in range last month?

> Compare my glucose in the first and the second half of September.

`get_glucose_stats` over the period. The headline numbers:

| Metric | Meaning | Consensus target (adults with diabetes) |
|---|---|---|
| Time in range 70-180 | share of readings in range | more than 70 % |
| Below 70 / below 54 | lows / clinically significant lows | less than 4 % / less than 1 % |
| Above 180 / above 250 | highs / very high | less than 25 % / less than 5 % |
| Mean | average glucose | - |
| GMI | estimated HbA1c from the mean | - |
| CV | variability: SD as a share of the mean | 36 % or below |
| Coverage | share of the period with readings | at least 70 % for the numbers to count |

The ranges follow the international consensus on CGM reporting (Battelino et
al., Diabetes Care 2019). The targets are for people with diabetes; without
diabetes, glucose is usually in range almost all the time.

## The typical day

> Do I have a dawn rise? When in the day am I highest?

`get_daily_pattern`: per hour of the day the 5th, 25th, 50th, 75th and 95th
percentile over the period (the ambulatory glucose profile). The median line
shows the rhythm; a wide band between the 25th and 75th percentile at the same
hour means that hour differs a lot from day to day. Patterns that single days
hide - a rise before waking, peaks after lunch, lows at night - show up here.

## Lows and highs

> Were there lows at night in the last two weeks?

> List every time I was above 250 this month.

`list_glucose_episodes`: each stretch below 70 or above 180 mg/dL that lasted
at least 15 minutes, with start, end, duration, the extreme value and the
sensor. The thresholds and the minimum duration can be changed ("below 54",
"at least 30 minutes"). A gap of more than half an hour in the data ends an
episode.

## A workout, a meal, a night

> How was my glucose during this morning's run, from 7:05 to 8:20?

> What did my glucose do in the two hours after lunch?

`get_glucose_readings` with exact local times returns the curve itself. Next
to a Garmin MCP server, the model can take the start and end of an activity
from there and put glucose and training side by side. Long periods are
condensed into time buckets with mean, minimum and maximum.

## Sensors

> How long did my last sensors last? Did one fail early?

> Was my time in range better with the current sensor than with the last one?

`list_sensors` lists every sensor with days worn and its own figures;
`get_sensor` shows the current one and when it ends (a Libre 3 lasts 14 days,
a Libre 3 Plus 15). Any period question can be limited to one sensor ("with
sensor 12"). Values from the first hours and the last day of a sensor are the
least accurate.

## When something is missing

> Why can't you see my glucose from last Tuesday?

`whoami` shows how far the stored history reaches and when the collector last
succeeded or failed. LibreLinkUp only keeps 12 hours, so the history starts
when collecting started: standalone, it collects while Claude runs it (or with
`libre-mcp collect` on a schedule); hosted, around the clock - and older
readings can be imported from LibreView.
