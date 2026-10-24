# Ready for friends: onboarding, profiles and routines

Status: built, apart from the two items under **Left** at the end. What has to change for Nexus to work for people who aren't
its owner (other jobs, other hobbies, not developers), and the order to build it in. The
**Where it stands** section at the end lists what changed from this plan and what is left.

## Where Nexus stands today

Nexus is solid underneath (invites, roles, budgets, rate limits, backups, monitoring). The parts
a new person touches were built for one AI engineer:

| Area | Today | Why it matters for a friend |
| --- | --- | --- |
| Onboarding | Name the assistant (`NameAssistantPage.tsx`), then the home screen | Nexus knows nothing about them. The first answers are generic until memory catches up, which takes several chats |
| First screen | The orb and an empty chat box (`HomePage.tsx`) | No suggestions, so people don't know what to ask or what it can do |
| Scheduled work | Only system cron jobs (memory upkeep, self-review in `worker.py`). Users can't schedule anything | A briefing every morning or a weekly digest is the main reason to come back every day |
| Time zone | Not stored on `users` | Needed before anything can run "at 8am" |
| Skills | 28 core skills, mostly research, ML, data and engineering | A teacher, nurse or sales lead gets little from them |
| Navigation | Library, Apps, Memory, Agents, Skills, Settings | "Agents", "Skills" and "Apps" (deployed web apps) are developer ideas, and show to everyone |
| Notifications | Email only for access requests (`notify.py`); no web push | Nothing can tell someone a routine finished or an approval is waiting |
| Trust | Langfuse records every prompt and reply in full, and the owner can read it | Friends should know this, or their content should be masked |
| Their data | "Forget everything" for memories; no export, no account deletion | Expected by anyone you invite |
| Look | Dark theme only | Fine, but a light theme is the most requested polish item in apps like this |

## The plan in five parts

1. **Onboarding interview**: about two minutes, every step skippable, fills memory and picks a
   profile.
2. **Profile packs**: data files, one per profession or hobby, holding starter memories,
   suggested prompts, skills and routines.
3. **Routines**: scheduled prompts that run as the person, on their time zone, within a budget,
   delivered to a chat thread plus push or email.
4. **UX polish**: plainer words, features shown when someone needs them, suggestions on empty
   screens, a short tour, a light theme, push notifications.
5. **Ready to share**: privacy notice, trace masking, export and delete, costs, uptime.

## 1. Onboarding interview

Replace the single page with a short stepper (same route guard in `App.tsx`: show it while
`user.onboarded_at` is null). Each step has **Skip**; Back keeps answers. Progress dots, the orb
at the top reacting to each step, keyboard friendly, works on a phone.

| Step | Asks | Input | Stored as |
| --- | --- | --- | --- |
| 1. Name | What should your assistant be called? | existing form | `users.assistant_name` |
| 2. You | What do you do? | chips (Student, Software / IT, Research / academia, Founder / manager, Design / creative, Sales / marketing, Healthcare, Teaching, Finance / legal / consulting, Freelance, Other) + a line of free text ("PhD in marine biology") | memory `fact`, pinned; `users.profile.roles` |
| 3. Your day | Time zone (from the browser, editable), when your day starts, work days | select, time, weekday toggles | `users.timezone`, `users.profile.day_start`, `work_days` |
| 4. Help with | What should it help with? | multi-select chips: email, calendar, research, writing, learning a subject, job hunt, side projects / coding, health and fitness, cooking, money, travel, reading, news on topics, family and home | `users.profile.interests`; memories `goal` |
| 5. Style | How should it answer? | short / balanced / detailed; casual / professional; reply language | memory `instruction`, pinned |
| 6. Connect | Gmail, Calendar, Tasks (optional) | the existing Connections card, with one line on what each one is used for | Google grant |
| 7. Your routines | Suggested routines from the packs that match, each with a toggle, a time and a preview of what it does | toggles, time picker | `routines` rows |
| Done | "You're set." Opens a first chat where the assistant greets them by name, says what it set up, and offers 3 prompts from their pack | | a chat |

Notes:

- Write the answers through the existing memory code, with `source="onboarding"`, so they show
  on the Memory page and can be edited or deleted like anything else. Pin the ones that came from
  a direct answer. Queue a profile rebuild right away, so the first turn already has the profile.
- Free text in step 2 and 4 goes to one `nexus-nano` call that maps it onto the nearest packs and
  returns extra starter memories as JSON (validated with a Pydantic model; fall back to the chips
  alone if it fails). One call, a fraction of a cent.
- A **Re-run setup** button in Settings brings the interview back with the answers filled in.
- Existing users (you) get `onboarded_at` set by the migration, so nothing changes for them.
- Keep it to 7 screens. Every extra question loses people; anything else can be learned in chat.

Schema: `users.timezone` (IANA name, default from the browser), `users.onboarded_at`,
`users.profile` (JSONB: roles, interests, day_start, work_days, style, pack ids).

## 2. Profile packs

One YAML file per pack in `library/profiles/`, synced into the database on start-up like the
core skills, so adding a profession is a pull request and not code. Owners can add their own on
the Skills page later.

```yaml
id: teacher
label: Teaching
match: { roles: [teaching], interests: [] }
memories:
  - { kind: fact, content: "Works as a teacher." }
prompts:                        # shown on the home screen and the first chat
  - "Plan a 45-minute lesson on photosynthesis for 12-year-olds"
  - "Write a rubric for a persuasive essay"
  - "Turn this article into a 10-question quiz"
skills: [lesson-planning, rubric-writing, email-writing, pptx, docx]
routines:
  - id: weekly-plan
    name: "Sunday week ahead"
    schedule: "every sunday at 18:00"
    prompt: >
      Look at my calendar for the coming week and my open tasks. Give me a short plan:
      lessons and meetings by day, deadlines, and anything I should prepare tonight.
    needs: [calendar]
    default_on: true
```

Starter set (each is a few memories, 3 to 5 prompts, a skill list and 1 to 3 routines):

| Pack | Example routines | New skills it needs |
| --- | --- | --- |
| Student | Daily study plan; weekly deadlines from calendar; spaced review quiz | study-plan, flashcards, exam-prep |
| Software / IT | Morning brief; weekly digest of releases for my stack; PR and issue summary (with GitHub) | (have code-review) |
| Research / academia | New papers on my topics (arXiv, Semantic Scholar) twice a week; reading list | (have literature-review, paper-reading) |
| Founder / manager | Morning brief; weekly competitor and market news; Friday "what happened this week" from mail and calendar | competitor-watch, one-pager |
| Design / creative | Weekly inspiration digest; client follow-up reminders | creative-brief, moodboard |
| Sales / marketing | Morning brief with today's calls; weekly industry news; follow-up drafts for stale threads | outreach-email, account-research |
| Healthcare | Shift-aware daily summary; guideline and journal updates on chosen topics | clinical-summary (sources only, no advice) |
| Teaching | Sunday week ahead; parent email drafts | lesson-planning, rubric-writing |
| Finance / legal / consulting | Market or regulation news on chosen topics; meeting prep the evening before | meeting-prep, contract-review (non-advice) |
| Freelance | Invoices and follow-ups due; weekly pipeline check | invoice, proposal-writing |

Hobby packs stack on top of a profession:

| Pack | Example routines |
| --- | --- |
| Health and fitness | Weekly training plan; Sunday meal plan with a shopping list |
| Cooking | Weekly meal plan from what they like; shopping list |
| Money | Monthly budget check-in (from a sheet they upload); news on chosen stocks or funds, no advice |
| Travel | Countdown and checklist before a trip found in calendar or mail |
| Reading and learning | Weekly "one thing to learn" lesson on a chosen subject, with a quiz |
| News on topics | Daily or weekly digest of their topics, with sources |
| Family and home | Weekly family calendar overview; bills and renewals reminders from mail |

Everyone gets a **Morning brief** (calendar, important unread mail, tasks due, weather for their
city if they gave one), off by default unless they connected Google.

## 3. Routines

A routine is a saved prompt with a schedule. When it fires, Nexus runs it as that person, with
their memory, tools and budget, in a chat thread of its own, and tells them it's done. This is
the main new piece of backend.

### Data

```
routines
  id, user_id, name, prompt
  schedule        text     -- RRULE, e.g. "FREQ=WEEKLY;BYDAY=SU;BYHOUR=18;BYMINUTE=0"
  timezone        text     -- copied from the user, editable per routine
  chat_id         uuid     -- the thread its runs post into
  delivery        text[]   -- {"chat", "push", "email"}
  max_cost_usd    numeric  -- per run, default ROUTINE_RUN_BUDGET_USD (0.10)
  only_if_new     bool     -- stay quiet when there is nothing new (see below)
  enabled         bool
  source          text     -- onboarding | pack:<id> | chat | user
  next_run_at     timestamptz
  failures        int      -- consecutive; 3 pauses it
  created_at, updated_at

routine_runs
  id, routine_id, scheduled_for, started_at, finished_at,
  status (ok | quiet | failed | skipped_budget | waiting_approval),
  message_id, cost_usd, error
```

RRULE (via `python-dateutil`) handles "weekdays at 8", "every second Monday" and "the last day
of the month" without inventing a format. Store `next_run_at` in UTC, compute it in the
routine's time zone so daylight saving changes don't move it.

### Running them

- A cron job in the orchestrator every minute:
  `SELECT ... FROM routines WHERE enabled AND next_run_at <= now() FOR UPDATE SKIP LOCKED LIMIT 50`,
  set the next `next_run_at` in the same transaction, then enqueue `run_routine` with the arq job
  id `routine:{id}:{scheduled_for}`, so two workers or a retry never run it twice.
- `run_routine` adds the prompt as a user message (marked `origin="routine"`) to the routine's
  chat and calls the normal turn path (`run_turn`), so routines get memory, tools, the router
  and teams for free. Big asks still become teams.
- Context added to the prompt: the current local date and time, when it last ran, and its last
  result, so "what's new since last time" works.
- **Quiet runs.** With `only_if_new`, the prompt asks for a single `NOTHING_NEW` line when there
  is nothing worth saying; the run is stored as `quiet` and nobody is notified. A news digest
  that pings with nothing loses people fast.
- **Budget.** Skip with `skipped_budget` (and tell the person once) when the run's cap doesn't
  fit what they have left; a run's own LiteLLM key capped at `max_cost_usd`, like team runs.
- **Safety.** Routines get the same tools as chat, and the same approval cards: a routine never
  sends mail or deletes anything without the person tapping Approve. A run waiting on approval
  shows as `waiting_approval` and pushes a notification.
- **Failures.** Retry once with backoff; three failures in a row pauses the routine and tells
  the person why.
- Missed runs (worker down at 8:00) run once when it is back if they are less than an hour late,
  otherwise skip to the next time.

### Making and changing them

- **In chat**: a `create_routine` tool. "Every weekday at 8 tell me what's on my calendar and
  anything urgent in my inbox" ends in a card showing the name, the schedule in words, the next
  three run times and the prompt, with Save / Edit. The model writes the RRULE; the API
  validates it and refuses anything more often than hourly (`ROUTINE_MIN_INTERVAL_MINUTES`).
  `list_routines`, `pause_routine` and `update_routine` too, so "stop the news digest" works.
- **Routines page** (in the sidebar): each routine with its schedule in words, next run, last
  result, cost so far this month, toggle, **Run now**, edit (schedule picker with presets and
  a "next 3 runs" preview), history, delete. A **Browse** tab lists routines from every pack,
  so people find ideas outside their own profile.
- Members get `ROUTINES_MAX_PER_USER` (10) routines.

### Telling people

- **In the app**: the routine's chat moves to the top with an unread dot; a bell in the header
  lists recent runs and waiting approvals.
- **Web push**: Nexus is already a PWA. Add VAPID keys, a `push_subscriptions` table, a service
  worker `push` handler and `pywebpush` in the orchestrator. Ask for permission only after the
  first routine is saved ("Want a notification when this is ready?"), never on first load.
  iOS supports it once the app is added to the home screen; say so.
- **Email** (SMTP is already there): optional per routine, the reply rendered as plain HTML with
  a link back.

## 4. UX and flow

### Show what each person needs

- Hide **Apps** and dev-team features unless the person picked Software / IT, has the exec
  permission, or asks to build something; keep them one click away under "More".
- Words: **Agents** becomes **Teams** ("Nexus puts a small team on bigger jobs"), **Skills**
  moves under Settings › Abilities for most people, **Library** stays. Tooltips and page guides
  in plain language.
- The orb stays the home screen, but under the composer show the person's 3 to 4 pack prompts
  as chips, plus "What can you do?" which opens a short capability card.

### First week

- A three-stop tour after onboarding (the composer, Memory, Routines), dismissible, never again.
- A checklist card on the home screen until done: connect Google, send a first message, upload a
  file, save a routine. Gone once ticked or dismissed.
- After the first routine run, a one-line "Was this useful?" with thumbs, saved to tune packs.

### Polish

- Light theme from the system setting (tokens in `index.css` already make this a palette swap).
- Skeletons instead of "Loading…", consistent empty states with one action each, optimistic
  updates on toggles and renames.
- Plain error messages everywhere ("Your monthly budget is used up; it resets on 3 Nov").
- A budget meter in Settings in words ("$1.20 of $5 used this month, resets on 3 Nov").
- Accessibility pass on the stepper and the Routines page (focus order, labels, contrast).
- Keyboard shortcut for new chat; the composer's chips reachable with Tab.

## 5. Ready to share

| Item | What to do |
| --- | --- |
| Privacy notice | A short page linked from onboarding and Settings: what is stored, who can see it (owner can read Langfuse traces), which model provider sees prompts, how to delete |
| Trace masking | Langfuse supports masking: send `[masked]` for prompt and completion content for users other than the owner (keep token counts and cost), or an opt-in per user. On by default for members |
| Export | "Download my data": chats, memories, files list and routines as a zip, made by a job |
| Delete account | Removes chats, memories, files, routines, keys and the Google grant; audited |
| Costs | Default budget per friend ($5/month is plenty for chat plus a few routines on mini and nano). Routines on `nexus-nano` / `nexus-mini` unless they build a team. Show the owner monthly spend per person in Admin (already there) |
| Uptime | Embeddings run on Ollama on the Mac: routines at 8am fail if the Mac sleeps. Either keep the host awake, or move `nexus-embed` to a hosted model (`text-embedding-3-small`, already in `config.yaml`) before inviting people; roadmap phase 6 (server) fixes this for good |
| Google | Unverified app: friends see a warning and click through; 100-user cap. Fine for friends; put a screenshot of the screen in the invite email so they aren't put off |
| Invite email | Today it says "name your assistant". Change it to a short welcome with what to expect and the Google screen |
| Feedback | A "Send feedback" item in the menu that lands in Admin, so friends can report problems without messaging you |

## Build order

Each phase is one pull request that can ship on its own.

| Phase | Contents | Size |
| --- | --- | --- |
| A. Foundations | `users.timezone`, `users.onboarded_at`, `users.profile`; migration that marks existing users onboarded; time zone in Settings | small |
| B. Onboarding | The stepper, answers to memories, nano mapping of free text, the first chat, Re-run setup | medium |
| C. Routines core | Tables, RRULE scheduling, the minute cron, `run_routine` through `run_turn`, quiet runs, budget and failure handling, tests | large |
| D. Routines UI and tools | Routines page, chat tools and card, Browse tab | medium |
| E. Profile packs | YAML format and sync, the 10 profession and 7 hobby packs, the new skills they list, onboarding step 7 and home chips from packs | medium (mostly writing) |
| F. Notifications | Web push, bell, email delivery | medium |
| G. Polish and trust | Progressive nav, renames, tour, checklist, light theme, privacy page, trace masking, export, delete account | medium |

A and C are the critical path; B can ship with chips only before E fills in the packs.

## Open questions

- How many friends, roughly? Under 20 keeps everything on the Mac; more makes moving embeddings
  and maybe the whole stack off the Mac the first job.
- Should friends get teams and the code sandbox by default, or only chat, memory, files and
  routines until they ask?
- Default trace masking for members: on (more private) or off (easier for you to debug)?
- Which channel for notifications matters most to them: phone push, email, or later WhatsApp or
  Telegram (a bot would be a natural next channel for routines)?

## Where it stands

Built (README: Onboarding and routines, Privacy):

- Phase A: `users.timezone`, `onboarded_at`, `profile`, `phone`, `share_usage`; existing users
  marked onboarded.
- Phase B: the stepper (six steps, skippable), answers to memories, the welcome chat, Run setup
  again. The free text is stored as memories as written rather than mapped by a `nexus-nano`
  call: memory extraction refines it in the first chats anyway, and it keeps the API free of
  model calls.
- Phases C and D: routines with RRULE schedules, the minute scheduler, quiet runs, budget skips,
  auto-pause, the Routines page with ideas, and the chat tools. Two decisions beyond the plan:
  routine runs skip the team router (a team per run would cost about $0.50 each), and don't
  feed memory extraction.
- Phase E: 19 packs (10 professions, 8 interests, everyday) and home-screen prompts. No new
  skills were written for them yet; their routines use the assistant's own tools.
- Phase F: email and WhatsApp (Cloud API template) notices per routine.
- Phase G: Privacy page, Langfuse masking for everyone but owners, "Help improve Nexus" opt-out
  for the self-review, data export, account deletion.

Decided with the owner: friends get every feature except admin; email and WhatsApp for notices.

Added after (owner's requests): web push to phones and browsers (routines and approvals), a
five-stop first-run tour, a light theme, 12 skills on two new shelves (work: lesson planning,
study plans, meeting prep, proposals, creative briefs, clinical evidence, job applications,
sales outreach; life: meal planning, trip planning, training plans, budgeting), in-app
feedback that reaches the owner and the self-review, and public Terms of Service and Privacy
Policy pages with recorded agreement at sign-up. WhatsApp stays a send-only notice channel by
decision; no replies.

Left:

- Plainer names (Agents → Teams) and hiding developer features: not done, since friends get
  every feature.
- Linking packs to skills explicitly; for now the assistant finds the right skill by relevance.
