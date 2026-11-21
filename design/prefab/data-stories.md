# Prefab — Data Stories over Mosaic Metadata (the keystone MVP)

> **Migrated from Aperture (2026-06-22)** with the data-story engine split (DataHelix platform
> [ADR-0003](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/ADR-0003-reel-data-story-engine-separate-from-portal.md)).
> This is Reel's keystone probe — **and it has run**, as **Exon** (`BU-Neuromics/mosaic-demo-small`);
> see "Probe status" below. References to portal-era Aperture docs (`core-loop.md`,
> `gen3-comparison.md`) name docs that stay in the **Aperture** repo; ADR references to the
> portal's decisions are qualified **"Aperture ADR-NNNN"**.
> **Names updated 2026-09-11:** Hippo → **Mosaic** (Mosaic ADR-0004), BASS → **DataHelix**;
> `hippoSchema` keeps its spelling as a data-contract identifier.
> **2026-11-20:** platform ADR-0007 retired that carve-out. Since Mosaic v0.16.0 these identifiers are
> `mosaicSchema` / `mosaicEntityType`, `mosaic_core` and `mosaic_*`.

**Status:** 🟠 Working design (2026-06-17, migrated 2026-06-22, probe status added 2026-09-11).
The simplest concrete realization
of the [vision](../vision.md): a NotebookLM-style conversational exploration of **Mosaic metadata only**
(workflow editing is the deferred "final form"). This is **keystone probe rung 1–2** — the
cheapest decisive test of "can an LLM reliably drive a typed declarative artifact through a
validator to a correct change?"

## The driving example (verbatim use case)

A multi-turn data story (the **gene-expression-*value*** turn from the original example is
**excised** — see "The boundary" below — because it crosses from the domain graph into bulk-data
slicing that Canon/Cappella don't yet implement; "*has* expression for gene Y" stays, as an
existence/relationship fact):
1. *"show me a summary of all the cases that are either PTSD, MDD, or controls, with negative
   toxicology reports for amphetamines, and that have gene expression data for gene Y"*
2. *"which genotypes do we have on these subjects?"*
3. *"how do the samples break down by genotypes A and B?"*

## The key insight: a data story is a narrated sequence of cohort states

Each turn operates on an **evolving selection** (a cohort), not from scratch. Turn 1 *establishes*
a cohort of subjects; later turns operate on "these subjects / these samples." So the central
object is exactly the **serializable query-state** identified in Aperture's portal `core-loop.md`
Step 4 — now promoted from "URL state" to "the thing the
conversation builds." **A data story = a sequence of core-loop states + transforms, narrated.**
This unifies the AI vision with the paused core-loop work: same substrate, conversational driver
on top.

**The cohort object is grain-agnostic and re-rootable.** Per the platform
[domain-graph model](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/domain-graph.md), a query returns a *knowledge
subgraph*, and "metadata vs. data" is a **role a node plays relative to the query**, not a storage
category. Turn 3 ("how do the *samples* break down…") re-roots the focal entity from subjects to
samples while the same diagnosis/toxicology predicates stay attached as *descriptors* — the
role-swap made operational. So the selection is not "a set of subjects" but **"a position in the
graph + predicates," re-rootable to any entity type.**

## Decomposing the turns into a query algebra

| Turn | Operation(s) | Grain |
|---|---|---|
| 1 | **filter** (set membership: `diagnosis ∈ {PTSD,MDD,control}`) + **exists-related filter** (has ToxReport{analyte=amphetamine, result=negative}; has expression *data* for gene Y) + **summarize/count** | subjects |
| 2 | **distinct values** of an attribute (genotype) over the cohort | subjects |
| 3 | **group-by + count**, **pivot grain** (subjects → samples) by genotype ∈ {A,B} | samples |

*(The excised value-histogram turn would have added `bind-measured-value` — the one op that
reaches across the structured/bulk boundary; deferred with the boundary, below.)*

**The minimal operation set:** filter (predicates, set-membership, ranges) · exists/related-entity
filter (join with predicate) · distinct-values (facet enumeration) · group-by + aggregate(count) ·
bind-measured-value · render-as-primitive (summary, histogram, bar, table, value-list) · and
**carry-cohort-across-turns + pivot-grain**.

## Interfaces this implies (the "set it up correctly" list)

1. **A serializable selection/cohort object** that persists across turns and can pivot grain
   (subjects↔samples). = core-loop query-state, made central.
2. **A query capability** over Mosaic supporting: set-membership filters, **relationship-existence
   filters with predicates on the related entity**, distinct-value enumeration, and
   **group-by + count**. ⚠️ Several exceed Mosaic's current GraphQL (equality + AND/OR + offset +
   FTS): relationship-existence/join filters and group-by/count are the notable gaps — same
   capability-negotiation theme as the core loop, and the same answer space (a/b/c: Mosaic
   enhancement, or the Gen3-style aggregation tier, or client-side compute).
3. **Schema grounding for the LLM** — the introspected LinkML types/slots/enums/descriptions are
   the model's context for NL→typed-query (so "PTSD, MDD, or controls" → `diagnosis IN [...]`,
   "genotypes A and B" → the genotype slot). This is Aperture ADR-0005 ("the schema *is* the agent's
   context") + the `mosaicSchema` (formerly `hippoSchema`) introspection resolver, finally exercised by a consumer.
4. **A view-primitive set** (the noun-catalog, Aperture ADR-0010): summary/stat card, histogram, bar,
   table, value-list.
5. **Dry-run validation** of the LLM's translated query/view-spec before it runs — the keystone
   guardrail.

The LLM's job each turn: **NL utterance + current cohort + schema grounding → a typed
query/view-spec → dry-run validate → execute → render + narrate.** Nothing freeform; everything
typed and checked.

**2026-09 status — the list is satisfied by the platform** (details and pointers in
[`platform-alignment.md`](../platform-alignment.md)): #1 is the **QuerySpec** (Aperture ADR-0035;
Reel [ADR-0006](../decisions/ADR-0006-v1-state-is-the-queryspec.md)); #2's gaps are **closed**
(Mosaic ADR-0006 relationship predicates, ADR-0007 counts/facet counts/ranges/ordering, exposed as
MCP tools); #3 is Mosaic's server-derived **capability manifest** (`mosaic://capabilities`, with
per-field legal operators and `enum_values`) plus the `construct-query-spec` prompt; #5 is
`validate_query_spec`, re-run in-process by Mosaic's relay on every turn (Mosaic ADR-0010).
Only #4 (the view-primitive catalog, Aperture ADR-0010 → the View Contract) is still open, and the
probe did not need it. Reel's own job is the *sequencing* — the cohort carried across turns.

## The boundary (decided): structured graph vs. bulk payload — Option B

The original example's value-histogram turn crossed a real boundary — but the boundary is **not**
"metadata vs. data" (a query-relative role); it is **structured relational records vs. bulk opaque
payloads** (see [domain-graph.md](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/domain-graph.md)). The sharp reason to
excise that turn: expression *values* are a **bulk array payload**, not a structured record.

**Decision (2026-06-17): Option B.** Mosaic tracks that the expression *data exists* (a
relationship/existence fact — subject has a sample with an RNASeq datafile); the actual values
live in files, mediated by **Canon/Cappella** (deferred). The MVP data story therefore covers
**attribute / relationship / categorical / count** questions over the domain graph (all three
remaining turns), and **defers value-distribution turns** until the bulk-slice path exists.

When that path arrives, it is the **deliberate boundary crossing** described in the domain-graph
model: a bulk slice (e.g., gene-Y normalized counts) is **induced as a subgraph** and **unioned**
into the query — *or* a derived per-(gene,group) **summary** is promoted into the graph (the
Gen3-Guppy two-tier lesson, Aperture's portal `gen3-comparison.md`). Either way it is an
expert-implemented slicer behind uniform graph semantics, not Mosaic becoming a data warehouse.

## Why this is the right first probe

It exercises the entire keystone — schema-grounded NL→typed-query, dry-run validation, typed view
primitives, cohort-state across turns — on the structured domain graph (Mosaic), with a real,
motivating use case and **no** dependency on Cappella/Canon (all three remaining turns stay within
the structured graph). If this rung works, the vision is real; if NL→validated-query proves
unreliable even here, we learn it cheaply.

## Probe status (2026-09-11)

The probe has been running since 2026-08-05 as **Exon** (`mosaic-demo-small/exon/`), against the
four-class demo schema (`Donor`/`Sample`/`Workflow`/`Dataset`, ~3,600 synthetic records) rather
than the brain-bank schema, with a harness that runs each of 29–35 benchmark questions *k* times
and grades **faithfulness** (every stated constraint present, none added) as well as validity.
Findings, in this doc's terms:

- **Schema-grounded NL → typed artifact → dry-run validate → execute works** with hosted models:
  forced tool calls comply; relationship-existence criteria collapse into a single
  `RelatedCondition`; unknown slots and illegal operators come back as coded, per-criterion
  errors the model can act on. A 12B local model did not clear the bar (ignored forced tool
  calls under load).
- **"Validated" ≠ "correct."** The dominant failure is *faithfulness*, which the keystone
  guardrail (#5) cannot catch by design: enum values paraphrased from the utterance
  (`"brain tissue"` for `tissue`), a counting question answered with a valid row listing, an
  empty related-criteria block that matches everything. Context tuning moved holdout from 0.50
  to 0.67 on one model and not at all on another; value-vocabulary grounding (now in the
  manifest) is the identified lever.
- **The cohort-across-turns half** (the conversational contract: stateless turn function,
  rewind-and-edit with recompute-and-suspend, `proposal` vs `clarification`) is specified and
  implemented on the Mosaic relay side (`converse_query_spec`); Exon's own turn endpoint is the
  next build there.

Verdict for this rung: **the vision is real at the query layer; the reliability question moved
from "can the LLM emit something valid" to "does the valid thing mean what was said."** That is
Reel's first measured backlog ([`../../proposals/exon-migration.md`](../../proposals/exon-migration.md) §4).

## Open questions
- **DS-1 — RESOLVED (2026-06-17): Option B.** Structured-record/bulk-payload boundary; values live
  in files via Canon/Cappella (deferred); MVP stories stay within the domain graph. See above +
  [domain-graph.md](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/domain-graph.md).
- **DS-2 — RESOLVED (2026-08-19, Mosaic enhancement):** yes, now. Mosaic ADR-0006 (typed
  per-class filter inputs with relationship predicates — `RelatedCondition {edge, quantifier,
  criteria}`) and ADR-0007 (counts, `facetCounts`, min/max, `order_by`), both Accepted and shipped
  (v0.13), exposed through the MCP boundary as `validate/execute/count/facet/field_range/search_query_spec`.
  The "aggregation tier" and "client-side compute" options were not needed; Aperture's planner
  keeps a single declared semijoin fallback for pre-M5 endpoints (Aperture ADR-0035).
- **DS-3 — RESOLVED in practice (2026-08-05): the small demo schema.** The probe grounds in
  `mosaic-demo-small`'s generic four-class biobank/omics schema, chosen to be scannable and free
  of brain-bank nouns (Aperture ADR-0002's rule, inherited by Reel). Re-running the harness
  against a real deployment schema is a fixture swap, not a design change
  (`proposals/exon-migration.md` Phase A6).
