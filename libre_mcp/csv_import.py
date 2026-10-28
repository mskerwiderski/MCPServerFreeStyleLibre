"""The LibreView glucose export (CSV): the history from before this server.

LibreView > Glucose History > Download glucose data gives one CSV with every
reading the Libre app ever uploaded. Its layout depends on the account
language, so columns are found by their header, not their position:

    Glukosewerte,Erstellt am,28-09-2026 08:00 UTC,Erstellt von,<name>
    Gerät,Seriennummer,Gerätezeitstempel,Aufzeichnungstyp,Glukosewert-Verlauf mg/dL,...
    FreeStyle Libre 3,...,27-09-2026 07:02,0,112,...

Only record type 0 (the sensor's own history, evenly spaced) is imported.
Scans (type 1) are extra spot readings; mixed in they would weight the
statistics towards whenever somebody looked at the phone. Times are local
(the user's zone, see times.py); mmol/L exports are converted to mg/dL.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

from . import times

MMOL_TO_MG = 18.016


class CsvError(ValueError):
    """Message meant to be shown to the person in the browser."""


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CsvError("The file is not readable text.")


def _find(header: list[str], *words: str) -> int | None:
    for i, name in enumerate(header):
        low = name.lower()
        if any(w in low for w in words):
            return i
    return None


_STAMP = re.compile(r"^(\d{1,4})[-./](\d{1,2})[-./](\d{1,4})[ T](\d{1,2}):(\d{2})")


def _day_first(stamps: list[str]) -> bool:
    """dd-mm-yyyy or mm-dd-yyyy: a first part above 12 settles it, a second
    part above 12 settles it the other way; undecided means day first (the
    order of every European account)."""
    for s in stamps:
        m = _STAMP.match(s)
        if m and len(m.group(1)) <= 2:
            if int(m.group(1)) > 12:
                return True
            if int(m.group(2)) > 12:
                return False
    return True


def _parse_stamp(s: str, day_first: bool) -> datetime | None:
    m = _STAMP.match(s.strip())
    if not m:
        return None
    a, b, c, hh, mm = (int(x) for x in m.groups())
    if len(m.group(1)) == 4:                      # yyyy-mm-dd
        y, mo, d = a, b, c
    elif day_first:
        d, mo, y = a, b, c
    else:
        mo, d, y = a, b, c
    try:
        return datetime(y, mo, d, hh, mm)
    except ValueError:
        return None


def parse(data: bytes) -> list[tuple[int, int]]:
    """(ts, mg/dL) of every history reading in a LibreView export."""
    text = _decode(data)
    lines = text.splitlines()
    head_at = next((i for i, line in enumerate(lines[:5])
                    if _find([line], "zeitstempel", "timestamp") is not None), None)
    if head_at is None:
        raise CsvError("This does not look like a LibreView glucose export "
                       "(no time stamp column found).")
    delimiter = ";" if lines[head_at].count(";") > lines[head_at].count(",") else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines[head_at:])), delimiter=delimiter))
    header, body = rows[0], rows[1:]
    i_time = _find(header, "zeitstempel", "timestamp")
    i_type = _find(header, "aufzeichnungstyp", "record type")
    i_hist = _find(header, "verlauf", "historic")
    if i_type is None or i_hist is None:
        raise CsvError("This does not look like a LibreView glucose export "
                       "(no record type or history column found).")
    mmol = "mmol" in header[i_hist].lower()

    history = [r for r in body if len(r) > max(i_time, i_type, i_hist)
               and r[i_type].strip() == "0" and r[i_hist].strip()]
    day_first = _day_first([r[i_time] for r in history])
    out = []
    for r in history:
        when = _parse_stamp(r[i_time], day_first)
        try:
            value = float(r[i_hist].strip().replace(",", "."))
        except ValueError:
            continue
        mg = round(value * MMOL_TO_MG) if mmol else round(value)
        if when is not None and 10 <= mg <= 700:
            out.append((times.from_local(when), mg))
    if not out:
        raise CsvError("The file contains no sensor history readings.")
    return sorted(out)
