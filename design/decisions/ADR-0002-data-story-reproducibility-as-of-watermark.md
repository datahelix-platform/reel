# ADR-0002: Data-story reproducibility via one as-of watermark per story-version

- **Status:** Accepted (with a stated v1 limit — see *Ratification (2026-11-10)*)
- **Date:** 2026-11-10 (proposed 2026-06-17; migrated to Reel 2026-06-22)
- **Deciders:** labadorf (decision pass 2026-11-10); originally labadorf, design session
- **Related:** ADR-0001 (instruction-path model), Aperture ADR-0020 (provenance events), Aperture ADR-0017 (data plane vs control plane); `instruction-path-model.md` §5; Mosaic `docs/data-model.md` (provenance & history, `state_at`); Mosaic ADR-0001 (graph-level as-of); mosaic#269 (`asOf` + `RelatedCondition` — lifts the v1 limit)

> **Migrated from Aperture ADR-0023 (2026-06-22)** on the data-story-engine split
> (boundary decision `datahelix:platform/design/decisions/ADR-0003`, whose **Outcome** section
> records the execution). Renumbered 0023 → Reel 0002. Portal-decision references
> are qualified **"Aperture ADR-NNNN"**; bare `ADR-NNNN` refers to Reel's own ADRs.
> **Names updated 2026-09-11:** Hippo → **Mosaic** (Mosaic ADR-0004), BASS/drylims → **DataHelix**;
> data-contract identifiers (e.g. `hippoSchema`, `hippo_core`) deliberately keep their spelling.

## Context

A data story is a narrative artifact people share, revisit, and cite. **It must tell the same
story whenever it is rerun** — a story composed today should reproduce identically next month —
*unless the user explicitly asks to pull in new data*. But the story's instructions query
**live Mosaic**, whose graph grows over time (new ingestions, supersessions). Naïve replay
("re-run the queries") would silently change artifacts as data lands, destroying reproducibility
and trust. This is the one way the parametric-CAD analogy breaks: CAD recompute is deterministic;
querying a moving database is not.

Mosaic already provides the substrate: no hard deletes; an append-only provenance log with
`state_snapshot` + `previous_state_hash`; per-entity as-of reconstruction (`client.state_at`);
and `schema_version` derived from the provenance log (so the *type system* as-of T is recoverable
too).

## Decision

**A `DataStory` carries exactly one as-of watermark (a timestamp), and every query in the story
resolves against the graph as it stood at that watermark.** Replay re-evaluates the instructions'
typed ops as-of T, so it is deterministic and the story reproduces identically regardless of when
it runs.

**"Pull in new data" is never a silent refresh.** It is an explicit instruction that produces a
**new story-version at a new watermark** (replaying the path against the new T) and is itself a
recorded, rewindable provenance event. One watermark per story-version — a single story never
mixes data from multiple times.

To make recompute-on-edit efficient and to mirror Mosaic's own design, **instruction nodes are
content-addressed**: a node's identity is `hash(op, parent-hashes, watermark)`, so editing an
instruction recomputes only reachable descendants whose hash changed; unchanged branches are
reused (the same idea as Mosaic's `previous_state_hash`).

## Consequences

- Reproducibility is a property of the data model, not of operator discipline: rewind/replay
  shows *what you saw then*, materialized artifacts are data-version-stamped, and "refresh against
  current data" is an explicit, audited action.
- **Forces State to stay intensional** (ADR-0001): you can only replay-as-of-T if the State is a
  spec re-evaluated against the graph, not a frozen bag of objects.
- **Depends on a Mosaic platform capability: graph-level / query-spanning as-of** — "evaluate this
  whole subgraph query as the graph stood at T," resolving every entity, relationship, and schema
  version to T, over the transport Reel uses. Mosaic today exposes this only **per entity**
  (`state_at`) and not on the GraphQL surface (equality-filter + additive-only). The substrate
  exists in the provenance log; the query-spanning resolver is a build (filed as Mosaic ADR-0001 /
  graph-level as-of). This extends the `vision.md` invariant to "typed, introspectable,
  dry-run-validatable, provenance-tracked — **and time-travelable**."
- Content-addressing gives free memoized recompute and aligns the instruction graph with the
  provenance graph.

## Alternatives considered

- **Always re-execute against current data on replay.** Simplest to implement; destroys
  reproducibility (the story changes under you). Rejected — this is the failure mode the whole
  ADR exists to prevent.
- **Snapshot the materialized results into the story** (extensional state). Reproducible, but
  stories balloon, can't be re-rooted/replayed, and lose the "re-runnable artifact" property.
  Rejected in favor of intensional state + as-of replay.
- **Per-instruction watermarks.** Would let one story mix data from many times — confusing and
  rarely desired. Rejected: one watermark per story-version; "pull new data" forks a new version.

## Notes / open sub-questions

- Depends on **Mosaic graph-level as-of query**, which is a Mosaic spec item (Mosaic ADR-0001);
  this ADR cannot be ratified until that capability is on Mosaic's roadmap.

### Status update (2026-09-11) — the Mosaic dependency is Accepted and partly built

- **Mosaic ADR-0001 (graph-level / query-spanning as-of) is `Accepted`** — ratified 2026-06-17,
  the same day this ADR was authored, so the "cannot be ratified until on Mosaic's roadmap"
  blocker above is cleared. Its design is Mosaic `sec6 §6.8`; implementation is staged in five
  increments and in progress (Mosaic `design/INDEX.md`).
- **What is live today:** `asOf` is an additive parameter on Mosaic's GraphQL reads and a field of
  the QuerySpec artifact (Aperture ADR-0035; Mosaic's `query_spec.py` validator accepts it) —
  with **one hard constraint**: `asOf` is **not combinable with a relationship predicate**
  (`RelatedCondition`) on the same query until Mosaic's temporal join ("M5a") lands. A v1 story
  whose path contains an `exists-related-filter` therefore cannot yet pin a watermark; the
  validator rejects the combination with a coded error rather than answering wrongly.
- **The prototype makes no reproducibility promise.** Exon's conversational contract
  (`mosaic-demo-small` `add-exon-conversational-contract/design.md`, Decision 6) re-runs anchor
  pivots "fresh against current data" and states the graph-level resolver gap as a non-goal.
  Watermark pinning per story-version is therefore the **first capability Reel adds** over the
  migrated prototype — see `../../proposals/exon-migration.md` (delta D2).
- **Content-addressed nodes are not in the prototype.** Exon's turn `id`s are opaque, not
  `hash(op, parent-hashes, watermark)`; this is a migration delta, not a contract change (the
  wire `id` stays opaque to callers).

### Ratification (2026-11-10) — Accepted, with a stated v1 limit

Ratified by labadorf in the 2026-11-10 decision pass: one as-of watermark per story-version;
"pull in new data" is a recorded watermark-advance producing a new story-version; content-addressed
instruction nodes.

**v1 limit — stories with a related-entity filter cannot be pinned.** A story whose path contains
an `exists-related-filter` (a QuerySpec `RelatedCondition` anywhere in `criteria`) **cannot carry
a watermark** until Mosaic supports `asOf` combined with a `RelatedCondition`. Mosaic's validator
rejects that combination today with the coded error `ASOF_RELATIONSHIP_FILTER_UNSUPPORTED`
(`mosaic:src/mosaic/core/query_spec.py`, the `spec.as_of is not None and
_contains_related_condition(...)` check at ~L476–483 on `main` as of 2026-11-10). Reel must not
work around it — not by dropping the watermark silently, nor by pinning only the non-related
steps (that would mix data from two times, which this ADR rejects). Lifting the limit is a Mosaic
change (the temporal join, Mosaic ADR-0001 increment "M5a"), tracked in
[mosaic#269](https://github.com/datahelix-platform/mosaic/issues/269).

**As built** (verified against `src/reel/` at commit `1b050e6`): nothing of this ADR is
implemented in Reel yet — no watermark field, no `asOf` emitted by the planner, no node hashes
(turn ids are opaque `uuid4().hex`). Watermark pinning and content addressing are migration
delta **D2** (`../../proposals/exon-migration.md`), whose gate already states the v1 limit
above. Until D2 lands, Reel stories make no reproducibility promise (inherited from Exon
Decision 6).

