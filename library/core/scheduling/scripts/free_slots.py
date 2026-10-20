"""Free time slots from busy intervals. See SKILL.md.

python free_slots.py busy.json --from 2026-10-21 --to 2026-10-23 --tz Asia/Kolkata
       [--hours 09:30-18:30] [--minutes 30] [--buffer 0] [--weekends] [--all-day-busy]
       [--also Europe/Berlin,America/New_York] [--max 20]
busy.json: a list of {"start", "end"} (ISO date-times with offsets, or dates for all-day).
"""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_when(v: str | dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(v, dict):  # Google Calendar shape: {"dateTime": ...} or {"date": ...}
        v = v.get("dateTime") or v.get("date")
    v = str(v)
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), time(), tz), True
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)), False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("busy")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True, help="last day, inclusive")
    ap.add_argument("--tz", default="Asia/Kolkata")
    ap.add_argument("--hours", default="09:00-18:00")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--buffer", type=int, default=0, help="minutes kept free around events")
    ap.add_argument("--weekends", action="store_true")
    ap.add_argument("--all-day-busy", action="store_true")
    ap.add_argument("--also", default="")
    ap.add_argument("--max", type=int, default=20)
    a = ap.parse_args()

    tz = ZoneInfo(a.tz)
    others = [ZoneInfo(z.strip()) for z in a.also.split(",") if z.strip()]
    h0, h1 = (time.fromisoformat(x) for x in a.hours.split("-"))
    buf = timedelta(minutes=a.buffer)
    need = timedelta(minutes=a.minutes)

    raw = json.loads(Path(a.busy).read_text("utf-8"))
    raw = raw.get("events", raw) if isinstance(raw, dict) else raw
    busy = []
    for e in raw:
        s, all_day = parse_when(e["start"], tz)
        f, _ = parse_when(e["end"], tz)
        if all_day and not a.all_day_busy:
            continue
        busy.append((s - buf, f + buf))
    busy.sort()
    merged: list[list[datetime]] = []
    for s, f in busy:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], f)
        else:
            merged.append([s, f])

    now = datetime.now(tz)
    slots = []
    day = date.fromisoformat(a.start)
    last = date.fromisoformat(a.end)
    while day <= last:
        if a.weekends or day.weekday() < 5:
            cur = datetime.combine(day, h0, tz)
            stop = datetime.combine(day, h1, tz)
            cur = max(cur, now)  # never offer time that has already passed
            for s, f in merged:
                if f <= cur or s >= stop:
                    continue
                if s - cur >= need:
                    slots.append((cur, s))
                cur = max(cur, f)
            if stop - cur >= need:
                slots.append((cur, stop))
        day += timedelta(days=1)

    if not slots:
        print("No free slot of that length in the window.")
        return
    print(f"Free slots of at least {a.minutes} min ({a.tz}, {a.hours}):")
    for s, f in slots[: a.max]:
        s_local, f_local = s.astimezone(tz), f.astimezone(tz)
        line = (
            f"- {s_local:%a %d %b} {s_local:%H:%M}-{f_local:%H:%M} "
            f"({(f - s).seconds // 60} min free)"
        )
        for z in others:
            zs, zf = s.astimezone(z), f.astimezone(z)
            odd = (
                " (outside 08-20 there)"
                if zs.hour < 8 or zf.hour > 20 or zf.date() != zs.date()
                else ""
            )
            line += f" | {z.key}: {zs:%H:%M}-{zf:%H:%M}{odd}"
        print(line)
    if len(slots) > a.max:
        print(f"... and {len(slots) - a.max} more")


if __name__ == "__main__":
    main()
