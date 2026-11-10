# ADR-0003: Instruction-path topology — general schema now, linear-only validator in v1

- **Status:** Accepted
- **Date:** 2026-11-10 (proposed 2026-06-17; migrated to Reel 2026-06-22)
- **Deciders:** labadorf (decision pass 2026-11-10); originally labadorf, design session
- **Related:** ADR-0001 (instruction-path model), Aperture ADR-0009 (dry-run validation), Aperture ADR-0021 (defer in-app chat; linear MVP first); `instruction-path-model.md` §4, §7

> **Migrated from Aperture ADR-0024 (2026-06-22)** on the data-story-engine split
> (boundary decision `datahelix:platform/design/decisions/ADR-0003`, whose **Outcome** section
> records the execution). Renumbered 0024 → Reel 0003. Portal-decision references
> are qualified **"Aperture ADR-NNNN"**; bare `ADR-NNNN` refers to Reel's own ADRs.
> **Names updated 2026-09-11:** Hippo → **Mosaic** (Mosaic ADR-0004), BASS/drylims → **DataHelix**;
> data-contract identifiers (e.g. `hippoSchema`, `hippo_core`) deliberately keep their spelling.

## Context

The instruction-path model (ADR-0001) gives each `Instruction` a reference to its parent
state(s). Topology then falls out of arity: **linear** (≤1 parent, ≤1 child), **tree** (≤1
parent, many children — forking), and **DAG** (many parents — convergence via grain-typed
**set-ops** like union/intersect/difference). The DAG case is genuinely useful: heterogeneous
cohort assembly (e.g. PTSD/MDD cases filtered by one set of criteria, controls by another, then
`union`ed). But trees and DAGs raise hard questions — narrative ambiguity ("which branch is *the*
story?"), rewind/scrub semantics on a DAG, and grain-compatibility of set-op operands — that we
should not solve to ship the keystone MVP, which is a single narrated linear path
(`data-stories.md`, Aperture ADR-0021).

The risk is choosing a *data model* that's too narrow now and forces a migration later, or one
that's too permissive now and lets v1 produce stories the v1 UI can't render.

## Decision

**Make the data model general and the validator narrow.** Persist `parents` as a *list* of
state-ids from day one (the schema accommodates linear, tree, and DAG without change). In **v1,
the dry-run validator enforces `len(parents) ≤ 1` and single-child**, so v1 stories are
physically linear while already stored in the general schema.

Lighting up richer topology later is then a **validator relaxation plus adding the `set-op` op
type to the catalog — never a data migration.** Set-ops, when enabled, are **grain-typed**:
operands must be union-compatible (same grain) or be pivoted to a common grain first; the
validator (Aperture ADR-0009) rejects ill-typed combinations or offers an auto-pivot.

This sets the build ladder: **(1) linear as-of rewind (v1)** → **(2) constrained cohort-assembly
mode** (set-op convergence only, no freeform branching) → **(3) full DAG story explorer**
(future). Rungs 2–3 are additive UI + validator changes.

## Consequences

- v1 ships the simple, narratable linear story with no exposure to tree/DAG complexity, yet
  every persisted story is forward-compatible — no migration when topology is unlocked.
- The DAG explorer and rewind-on-a-DAG are explicitly **deferred** (`instruction-path-model.md`
  §6, §11): once a node has multiple downstream paths and set-op ancestors, "scrub to a point" is
  no longer well-defined (recompute stays well-defined; the UI affordance does not).
- Establishes "topology is a property of the data, not the engine" as an invariant: the core
  never needs rebuilding to grow into richer modes.

## Alternatives considered

- **Linear-only data model now, generalize later.** Smallest schema today, but forces a real
  migration of stored stories to add `parents`/set-ops. Rejected: the list-of-parents schema is
  nearly free now and avoids the migration.
- **Expose tree/DAG in v1.** Maximizes power but drags in narrative ambiguity, DAG rewind, and
  set-op grain semantics before the keystone is even proven. Rejected as premature
  (cf. Aperture ADR-0021).
- **Allow DAG merges beyond set-ops** (arbitrary join semantics). Unbounded complexity; deferred
  entirely.

## Notes / open sub-questions

- Whether the **cohort-assembly mode (rung 2)** should precede generic branching is left open;
  the heterogeneous-filtering use case is valuable enough that it plausibly jumps the queue.

### Status update (2026-09-11)

- **Reciprocal reference recorded.** Aperture ADR-0035 (Accepted 2026-08-19) cites this ADR as
  the reason it *rejects arbitrary join semantics*: quantified relationship criteria
  (`RelatedCondition {edge, quantifier: some|none, criteria}`) and an **explicit** per-column
  `aggregate`-vs-`explode` choice on to-many paths (≙ this ADR's grain discipline) are the
  sanctioned mechanisms. `set-op` between States stays Reel's, deferred as here.
- **The prototype is linear-only, as v1 prescribes** — but its wire form carries an *ordered
  `turns` list* with an implicit single parent, not a persisted `parents` list. Adopting the
  general schema (list-of-parents, ≤1 enforced) is a **migration delta** the translation layer
  owns (Reel [ADR-0008](./ADR-0008-exon-seeds-reel.md); `../../proposals/exon-migration.md`
  delta D1) — exactly the "general schema now, narrow validator now" rule this ADR sets.

### Ratification (2026-11-10) — Accepted

Ratified as written by labadorf in the 2026-11-10 decision pass: general `parents`-list schema,
linear-only (`len(parents) ≤ 1`, single child) in v1.

**As built** (verified against `src/reel/story/conversation.py` at commit `1b050e6`): the code
stores an **ordered `turns` list with no `parents` field** — the single parent is implicit in
list position (`edit_turn` rewinds by slicing `turns[:idx]`). So v1 is physically linear, as
this ADR requires, but the general schema is **not yet adopted**; persisting
`instructions[].parents` and projecting it to the wire `turns` list is migration delta **D1**
(`../../proposals/exon-migration.md`). The "never a data migration" consequence therefore holds
from D1 onward — nothing is persisted today, so there is nothing to migrate.

