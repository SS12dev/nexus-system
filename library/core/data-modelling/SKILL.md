---
name: data-modelling
description: Design the data an application stores - entities, keys, constraints, relationships, indexes and migrations - and choose where it lives (browser storage, SQLite, Postgres). Use when an app keeps data, when adding a table or field, or when reviewing a schema.
metadata:
  category: engineering
---

# Data modelling

Data outlives code. A wrong schema is the most expensive mistake to fix later, so decide it
on paper first.

## Where the data lives

| Need | Choice |
| --- | --- |
| One person, one device, loss is tolerable (settings, a game's high score) | `localStorage` with a versioned key |
| One person, one device, more than a few hundred records or needs queries | IndexedDB |
| Shared between devices or people, modest size, one server | SQLite file on the server (WAL mode) |
| Many concurrent writers, several services, growth expected | Postgres |

State the choice and the reason in the design. Don't add a server only to store what a
browser can hold, and don't keep in a browser what the user would be upset to lose.

## Entities

- Model the things the user talks about. One table per thing; a field holds one value.
- Every table: a primary key (`id`, a UUID or an integer), `created_at`, and `updated_at`
  when rows change.
- Give each field a type, nullability and a limit. Null means "unknown or not applicable",
  never "empty"; use `NOT NULL` with a default wherever a value always exists.
- Let the database enforce the rules: `UNIQUE`, `CHECK` (ranges, allowed values), foreign
  keys with a chosen `ON DELETE`. Rules enforced only in application code get broken.
- Money as integer minor units; times as UTC timestamps; enumerations as a checked text
  column or a lookup table.

## Relationships

- One-to-many: a foreign key on the many side, indexed.
- Many-to-many: a join table whose primary key is the pair.
- Don't store what can be derived (a count, a total) unless measured need says so; then say
  how it stays correct.
- Every row that belongs to a user carries `user_id`, and every query filters on it.

## Indexes

Index what you filter, join and sort by: foreign keys, the columns in common `WHERE` clauses,
and the list's order. A composite index follows the query (`user_id, created_at DESC`). Don't
index everything; each index slows writes.

## Changing the schema

- Use a migration tool from the first table (Alembic for SQLAlchemy, the ORM's own for
  Node). The schema is whatever the migrations produce; no hand-run SQL, no
  `create_all` in production paths.
- Migrations are small, ordered and forward-safe: add a column as nullable or with a
  default, backfill, then tighten. Never edit a migration that has been applied.
- Destructive changes (drop, rename) need a note in the plan on what happens to existing data.
- For browser storage, keep a `version` in the stored object and migrate old shapes on load;
  tolerate missing or corrupt data by falling back to defaults.

## Access patterns

- Use transactions for anything that changes more than one row together.
- Parameterised queries only; never build SQL from strings.
- Avoid the N+1 pattern: load related rows in one query.
- Cap every list query with a limit.

## Checklist for the plan

- Each entity, its fields, types, limits and constraints.
- Each relationship and its delete behaviour.
- Indexes, each tied to a query.
- The migration tool and the first migration.
- What is private to whom, and how every query enforces it.
- How the data is backed up or exported, in one line.
