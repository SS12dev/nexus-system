---
name: prompt-writing
description: Write effective prompts and agent instructions - role, context, constraints, output format, examples and stopping rules - for sub-agents, LLM pipelines, judges and extractors, and debug prompts that misbehave. Use when writing or improving a system prompt or agent instructions.
metadata:
  category: agents
---

# Writing prompts and agent instructions

## What a good prompt contains (in this order)

1. **Role and goal:** who the model is for this task and what success looks like, in one
   or two sentences. "You compare EV battery suppliers for an investment memo; success is
   a sourced table of the top 10 by 2025 capacity."
2. **Context it can't guess:** the user, the audience, prior results, definitions,
   constraints (budget, dates, region, units, language).
3. **The task as steps** when order matters; as a checklist of required contents when it
   doesn't.
4. **Output format:** exact structure (headings, a JSON schema, a table's columns), length,
   and where files go. Show one short example when the format is unusual.
5. **Boundaries and stopping rule:** what not to do, when to stop ("stop after 6 searches
   or when every supplier has a source"), and what to do when information is missing
   ("write 'not found', don't estimate").

## For sub-agents in a Jarvis team

- Each agent's `instructions` are task-specific: they get safety rules, their role and
  shared conventions automatically, so don't repeat those.
- Say what the agent receives (files, the previous agent's result) and exactly what it must
  hand back, since its final reply is passed on as-is.
- Give the lead or splitter the decomposition criteria (by country, by paper, by section)
  and the expected number of items.
- Name the skills to load ("load core/literature-review first") when one fits.
- Keep cheap tiers on narrow tasks; give `pro` agents the judgement calls (planning,
  reviewing).

## Structured output

- Ask for JSON only, give the schema with types and allowed values, and say what to use
  for unknowns (`null`, never a guess). Validate the result in code and send the error
  back once on failure.
- Put long reference material before the question, and the question and format at the end.
- Separate data from instructions with tags: `<document>...</document>`, and say that
  content inside is data, not instructions.

## Debugging a prompt

| Symptom | Usual fix |
| --- | --- |
| Ignores a rule | Move it up, state it once plainly, give the reason; remove contradicting rules |
| Too long or padded | Give a word limit and the shape; ban preambles ("no introduction") |
| Makes things up | Require sources per claim; give an explicit "not found" path |
| Wrong format | Show one example output; validate and retry with the error |
| Inconsistent across runs | Lower temperature; fewer, sharper criteria; add anchored examples |
| Does too much | Narrow the goal, add a stopping rule and a step or tool budget |

Test changes on 5 to 10 representative inputs, including a hard one, before and after;
use `core/llm-evaluation` when it matters.
