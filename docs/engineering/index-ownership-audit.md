# Index Ownership Audit (live DB vs SQLAlchemy models)

**Date:** 2026-09-20 · **DB:** hsdeep (local Postgres) · **Method:** read-only
`pg_index` / `pg_constraint` inspection compared against `Base.metadata`
(75 model tables, all imports present after the `app/models/__init__.py`
`Task` fix). Probe script used: per-table `pg_index` dump joined with model
declarations; reproduced below.

## Result

`alembic check` is CLEAN ("No new upgrade operations detected") after
revisions `c4d9a2b7e105`, `d8f3a1c5e207`, `e1a7b3c9f204`. **35 live indexes
remain that the models do not declare. All 35 are CONSTRAINT-BACKED** — each
is the physical index Postgres created to enforce a `UniqueConstraint`
(`uq_*`) declared in the corresponding model.

## Classification

| Class | Count | Verdict |
|---|---|---|
| CONSTRAINT-backed (`uq_*` enforcing model-declared UniqueConstraint) | 35 | **KEEP** — owned by the constraint; dropping the index would violate it, and Alembic compares these as constraints, not indexes |
| PARTIAL / expression | 0 | n/a |
| LEGACY REDUNDANT (same column(s)+method as a declared index) | 0 | n/a — the 4 that existed (`ix_resource_relationships_source/_target/_type`, `ix_source_health_audits_event_type_2`) were dropped by `e1a7b3c9f204` after per-pair verification |
| PLAIN-EXTRA (no model owner) | 0 | n/a |

## Why nothing further is droppable

A unique index backing a constraint cannot be dropped independently of the
constraint in Postgres. The only legitimate way to "remove" one is to retire
the business rule (the UniqueConstraint) itself — a product decision, not a
schema-hygiene one. None of the 35 constraints are candidates: they are the
platform's identity/idempotency rules (tenant+name, source+external_id,
tenant+number, connection+external_id, etc.).

## Guidance for future index work

1. **New query-path indexes go in the model** (`Index(...)` in
   `__table_args__`), never migration-only — undeclared indexes are what
   made `alembic check` propose destructive drops in the first place.
2. **Dedup check before adding**: a column already covered by a
   `UniqueConstraint`'s leading columns does not need its own index.
3. **Any future drop requires**: pg-level proof the pair is identical
   (`pg_get_indexdef` comparison), a raise-on-mismatch guard in the
   migration, and a keeper index confirmed present first — the pattern used
   in `e1a7b3c9f204`.
