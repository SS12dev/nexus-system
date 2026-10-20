---
name: email-writing
description: Write and reply to emails that get read and answered - clear subject lines, the ask up front, the right tone for the recipient, follow-ups, and polite declines - and save them as Gmail drafts or send them with approval. Use for any task that drafts, replies to or sends an email or message.
metadata:
  category: writing
---

# Writing emails

Tools: `gmail_search`/`gmail_read` for context, `contacts_search` for addresses,
`gmail_draft` to save a draft, `gmail_send` (or `send_email` without Gmail) to send, which
always asks the user to approve the exact message first.

## Before writing

- Read the whole thread being replied to, and earlier mail with this person
  (`gmail_search "from:x OR to:x" newer_than:90d`) to match tone and avoid repeating.
- Know the one thing the email must achieve (a decision, a meeting, information, a
  thank-you).
- Find the right address with `contacts_search`; never guess an address.

## Structure

1. **Subject:** specific and scannable: "Decision needed by Fri: Q4 vendor shortlist",
   "Re: EV report: two-line summary". Keep the thread's subject when replying.
2. **First line:** the ask or the answer. "Could you confirm the venue by Thursday?"
3. **Context:** only what the reader needs to act, in 1-3 short paragraphs or bullets.
4. **Close:** the next step and any deadline; sign-off in the user's usual style.

Plain text, short paragraphs, no more than one ask per email when possible. Attach library
files instead of pasting long content.

## Tone

- Match the relationship and the thread: colleagues and friends informal, external and
  senior contacts polite and concise. Mirror the other person's formality.
- Write as the user, in first person, in their voice (check the user's instructions and
  profile for their style and sign-off). Never claim things the user didn't say or agree to.
- Declines: thank, decline clearly, give a short reason only if helpful, offer an
  alternative if there is one.
- Follow-ups: reference the earlier email in one line, restate the ask, make it easy to
  answer.

## Send or draft?

- Default to `gmail_draft` and tell the user it's in their drafts, unless the task says to
  send.
- When sending, call `gmail_send` with the final text: the user approves it on a card in
  the app. Don't ask for approval in chat and don't send variations after a rejection.
