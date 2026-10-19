# Jarvis skills library

Shared, tested how-tos that Jarvis and every sub-agent load on demand, so nobody works out
"how do I edit a PPTX" twice. Skills use the open
[Agent Skills](https://agentskills.io) format, so skills written for other agents can be
imported on the Skills page.

```
library/
  instructions/
    global.md          safety rules in every agent's prompt; nothing later overrides them
    roles/<role>.md    defaults for agents with that role (researcher, writer, ...)
  core/<name>/         skills shipped with Jarvis, synced into the database at startup
    SKILL.md           frontmatter (name, description), then the instructions
    scripts/           tested helpers agents run in the sandbox
```

## How agents use a skill

1. **Discover.** An agent's prompt lists only the slug and description of each skill it was
   granted (`core/pdf: Create, merge, split...`), so a skill costs about 100 tokens until it
   is needed.
2. **Activate.** When a task matches, the agent calls `skills_load("core/pdf")` and gets the
   whole SKILL.md. `skills_search` finds other enabled skills.
3. **Execute.** In the sandbox, every skill the agent can use is unpacked at
   `skills/<scope>/<name>/`, so `python skills/core/pdf/scripts/md_to_pdf.py ...` works from
   `run_code`. Inputs are in `in/`, and files written to `out/` land in the user's library.

## Changing skills

- Shipped skills (`core/`) are edited here, in git. On startup the orchestrator syncs any
  that changed into the database as a new version. An admin's edit on the Skills page lasts
  until the shipped files change again; both stay in the version history.
- Personal skills, imported skills and skills Jarvis learns live in the database with their
  own version history. Imported and learned skills stay disabled until reviewed.

Each script prints `--help`; the SKILL.md says when to use it. Keep scripts dependency-free
beyond the libraries in `services/sandbox/pyproject.toml`, and add a test in
`services/sandbox/tests/test_skills.py` for every script.
