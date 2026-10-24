# Nexus skills library

Shared, tested how-tos that the assistant and every sub-agent load on demand, so nobody works out
"how do I edit a PPTX" twice. Skills use the open
[Agent Skills](https://agentskills.io) format, so skills written for other agents can be
imported on the Skills page.

```
library/
  instructions/
    global.md          safety rules in every agent's prompt; nothing later overrides them
    shared.md          house conventions (dates, sources, file names, hand-overs) for
                       the assistant and every agent
    roles/<role>.md    defaults for agents with that role (researcher, scientist, ...)
  core/<name>/         skills shipped with Nexus, synced into the database at startup
    SKILL.md           frontmatter (name, description, metadata.category), then the steps
    scripts/           tested helpers agents run in the sandbox
    references/        longer material an agent reads only when it needs it
```

## What's on the shelves

| Shelf | Skills |
| --- | --- |
| documents | `pdf`, `docx`, `pptx`, `xlsx`, `charts`, `images`, `file-conversion` |
| research | `web-research`, `literature-review`, `paper-reading`, `research-gaps`, `paper-writing`, `citations`, `fact-check` |
| writing | `report-writing`, `summarize`, `email-writing`, `meeting-notes` |
| data | `data-analysis`, `statistics`, `ml-experiments`, `llm-evaluation` |
| productivity | `inbox-triage`, `scheduling`, `compare-options` |
| work | `lesson-planning`, `study-plan`, `meeting-prep`, `proposal-writing`, `creative-brief`, `clinical-evidence`, `job-application`, `sales-outreach` |
| life | `meal-planning`, `trip-planning`, `training-plan`, `budgeting` |
| agents | `prompt-writing` |
| engineering | `code-review`, `3d-modeling` |

A skill names its shelf in frontmatter:

```yaml
---
name: literature-review
description: What it does and when to use it, in one or two sentences (under 350 characters).
metadata:
  category: research
---
```

Shelves are listed in `CATEGORIES` in `packages/common/src/nexus_common/skills.py`.

## Instruction layers

Every agent's prompt gets, in order: the safety rules (`global.md`), the house conventions
(`shared.md`) plus any global instructions an admin adds, its role's instructions (shipped
plus admin additions), then the user's own instructions. The assistant's own chat gets the
conventions, the admin's global instructions and the user's instructions. The team designer
picks each agent's role from the files in `roles/`.

## How agents use a skill

1. **Discover.** An agent's prompt lists only the slug and description of each skill it was
   granted (`core/pdf: Create, merge, split...`), so a skill costs about 80 tokens until it
   is needed. The assistant's chat lists the whole library while it has 12 skills or fewer; beyond
   that it lists the skills that match the message in full and the rest by name per shelf.
2. **Activate.** When a task matches, the agent calls `skills_load("core/pdf")` and gets the
   whole SKILL.md. `skills_search` finds other enabled skills.
3. **Execute.** In the sandbox, every skill the agent can use is unpacked at
   `skills/<scope>/<name>/`, so `python skills/core/pdf/scripts/md_to_pdf.py ...` works from
   `run_code`. Inputs are in `in/`, and files written to `out/` land in the user's library.

## Changing skills

- Shipped skills (`core/`) are edited here, in git. On startup the orchestrator syncs any
  that changed into the database as a new version. An admin's edit on the Skills page lasts
  until the shipped files change again; both stay in the version history.
- Personal skills, imported skills and skills the assistant learns live in the database with their
  own version history. Imported and learned skills stay disabled until reviewed.
- **Share with everyone** (admins, on the Skills page) moves one of your own skills, for
  example one the assistant learned from a run, into the shared library, turned on for every user.
  It keeps its history; from then on admins edit it. To ship it with Nexus for good, copy
  it into `core/` here.

Each script prints `--help`; the SKILL.md says when to use it. Keep scripts dependency-free
beyond the libraries in `services/sandbox/pyproject.toml` (pandas, numpy, scipy,
scikit-learn, matplotlib, pillow and the document libraries; build123d, trimesh and
manifold3d from its `cad` extra, which `test_cad.py` skips without), and add a test in
`services/sandbox/tests/test_skills.py` or `test_library.py` for every script.
`test_library.py` also checks that every skill parses, has a shelf, and only points at
scripts and references that exist.

## Profile packs

`profiles/<id>.yaml` holds what the setup interview sets up for one profession (`kind: role`),
one interest (`kind: interest`) or everyone (`kind: base`): starter memories, prompts shown on
the home screen and in the welcome chat, and routines it offers. The API and orchestrator read
them from disk (`nexus_common/profiles.py`); a file that doesn't parse is logged and skipped.

```yaml
id: teaching              # also the role or interest id the interview sends
label: Teaching
kind: role                # role | interest | base
order: 17                 # where it sits in the interview's lists
blurb: Lesson plans, quizzes and the week ahead.
match: [tutoring]         # other role or interest ids that pick this pack (optional)
memories:                 # role packs only; added, pinned, for people who pick it
  - {kind: fact, content: "Works as a teacher."}
prompts:
  - "Plan a 45-minute lesson on this topic"
routines:
  - id: week-ahead
    name: Sunday week ahead
    days: [SU]            # daily | weekdays | [MO, TH] | "monthly:1"
    time: "18:00"         # "HH:MM", day_start, or day_start+30 (minutes after the day starts)
    needs: [calendar, tasks]   # Google services it reads; shown to the person
    only_if_new: false    # stay silent when there's nothing new (answer NOTHING_NEW)
    default_on: true      # ticked in the interview
    prompt: >
      Look at my calendar and tasks for the coming week...
```

Write routine prompts as the person would ask, self-contained (they run with no chat around
them), and lean on "from what you know about me" for topics rather than naming any.
`services/api/tests/test_onboarding.py` loads the shipped packs.
