---
name: scheduling
description: Find free time and schedule well - reading the user's Google Calendar, computing open slots across working hours and time zones, proposing options, adding events, and handling reminders and tasks. Use for "find a time", "when am I free", "schedule/move a meeting", "remind me", or planning a day or week.
metadata:
  category: productivity
---

# Scheduling

Tools: `calendar_list_events`, `calendar_create_event` (events without guests),
`calendar_update_event`, `calendar_invite` (with guests; the user approves it),
`tasks_add`/`tasks_list`/`tasks_complete`, `contacts_search`.

## Time zones first

The user's time zone is in their profile (Shivam: Asia/Kolkata, IST, UTC+5:30). Always
state times with the zone, and for guests elsewhere give both ("4 pm IST / 11:30 am CET").
Resolve "today", "tomorrow", "next week" from today's date in the user's zone.

## Find free slots

List events for the window with `calendar_list_events`, then in `run_code`:

```bash
cat > busy.json <<'JSON'
[{"start": "2026-10-21T10:00:00+05:30", "end": "2026-10-21T11:00:00+05:30"},
 {"start": "2026-10-21T14:00:00+05:30", "end": "2026-10-21T15:30:00+05:30"}]
JSON
python skills/core/scheduling/scripts/free_slots.py busy.json --from 2026-10-21 --to 2026-10-23 \
  --tz Asia/Kolkata --hours 09:30-18:30 --minutes 45 --buffer 10 --also Europe/Berlin
```

It merges overlapping events, keeps a buffer around them, skips weekends unless
`--weekends`, and prints slots in the user's zone (and the `--also` zones, flagging times
outside 08:00-20:00 there). All-day events count as busy only with `--all-day-busy`.

## Proposing and booking

- Offer 2-3 options, preferring mornings for focus work and avoiding back-to-back blocks.
- Adding an event for the user alone: `calendar_create_event` directly when asked.
- With guests: `calendar_invite` with the final details; it asks the user to approve.
- "Remind me to X at 7 pm": `tasks_add` with the due date (Google Tasks keeps the date, not
  the time), or a short calendar event at 7 pm when the time matters; say which you used.
- Moving an event: `calendar_update_event` only for events without other guests; with
  guests, draft a note to them instead.
