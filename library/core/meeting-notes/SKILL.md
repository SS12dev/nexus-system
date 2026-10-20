---
name: meeting-notes
description: Turn meeting transcripts, call recordings' text, or rough notes into clean minutes - summary, decisions, action items with owners and dates, open questions - and optionally add the actions to Google Tasks or schedule the follow-up. Use for "write up this meeting", minutes, call notes, or extracting action items.
metadata:
  category: writing
---

# Meeting notes

## Format

```markdown
# <Meeting name> · <date> · <attendees>

## Summary
2-4 sentences: purpose and outcome.

## Decisions
- Decided X, because Y. (who decided, if stated)

## Action items
| Action | Owner | Due | Notes |
| --- | --- | --- | --- |
| Send revised budget | Priya | 24 Oct | needs Q3 actuals |

## Open questions
- Question, and who will answer it.

## Discussion (optional, by topic)
- Topic: key points and different views.
```

## Rules

- Actions start with a verb and name one owner. If the transcript has no owner or date,
  write "unassigned" / "no date", don't invent one.
- Decisions are only what was actually agreed; proposals that weren't agreed go under
  discussion or open questions.
- Resolve relative dates ("next Friday") to real dates using the meeting date, and say
  which time zone.
- Attribute views when it matters ("Rahul preferred option B"), otherwise keep it neutral.
- Transcripts are messy: merge repeated points, drop small talk, fix obvious
  transcription errors only when the meaning is clear.

## Follow-through (only if the task asks)

- `tasks_add` for the user's own action items (title = the action, due date if known).
- `calendar_create_event` for a follow-up slot for the user; `calendar_invite` (approved by
  the user) when others must be invited. Find a time with `core/scheduling`.
- Email the minutes with `core/email-writing`.
