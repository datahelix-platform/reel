# Reel ↔ platform alignment (crosswalk, 2026-09-11)

**Status:** 🟢 Reference (living). Reel's design set was written 2026-06-17/22, before several
decisions that now bind it. This crosswalk maps each claim or dependency in Reel's docs to the
**current** platform state, so a reader of the older docs knows what has moved. Update it when
a referenced decision changes status. Decisions themselves live in `decisions/` and the Decision
Log in [`INDEX.md`](./INDEX.md).

## Names

| Reel docs said | Now | Source |
|---|---|---|
| **BASS** platform; `drylims` integration repo | **DataHelix**; `datahelix-platform/datahelix` | DataHelix rebrand 2026-07-07 (datahelix PR #43) |
| **Hippo** (graph runtime) | **Mosaic** — distributed as `datahelix-mosaic`, imported as `mosaic`. Data-contract identifiers (`hippoSchema`, `hippo_core`, `hippo_*` annotations, `X-Hippo-Actor`) deliberately unchanged. | Mosaic ADR-0004 |
| "Reel" is a **provisional codename** (Strand, Loom, Lumen, Cadence) | **Ratified** — Reel is the component's real name | platform ADR-0003 ("Name" note) |
| Boundary decision `drylims:platform/design/decisions/ADR-0001` | **platform ADR-0003** (platform ADR-0001 is now the certified-frontier ledger; 0002 is the metapackage) | `datahelix:platform/design/INDEX.md` |
| Runbook `proposals/reel-split.md` | Never landed; the split's execution is recorded in platform ADR-0003's **Outcome** section | same |
| Reel "attached to the integration repo as a submodule" | **Not mounted.** `datahelix` mounts only `mosaic/` and `aperture/`; mounting Reel is named follow-on work | platform ADR-0003 Notes; `proposals/exon-migration.md` Phase D |
| Packaging (unstated) | `datahelix-reel` dist, `import reel` (Layout A) | platform ADR-0002 |

## Dependencies Reel's ADRs declared, and where they stand

| Reel ADR | Declared dependency | Status now |
|---|---|---|
| ADR-0001 | Aperture ADR-0010 (typed noun-catalog, "keystone") | Still `Proposed` in Aperture; no longer on Reel's critical path — view primitives sit beyond the View Contract, and the query half of the keystone was probed without them (see **Keystone probe** below). |
| ADR-0001 | Aperture ADR-0003 (config-as-LinkML-in-Mosaic) for `DataStory` persistence | Accepted. Aperture ADR-0032 (Accepted, amended 2026-08-28) gives the concrete control-plane shape: versioned `{kind, name, payload}` documents on a Mosaic collection with `owner`/`visibility`. A persisted `DataStory` would be a new document kind there. |
| ADR-0001 | Aperture ADR-0020 (conversations are provenance events) | Still `Deferred (MVP)` in Aperture. Reel's edit-history log (ADR-0004; migration delta D3) waits on it or defines its own event. |
| ADR-0002 | **Hippo ADR-0001** graph-level as-of ("cannot ratify until on roadmap") | **Mosaic ADR-0001 Accepted 2026-06-17**; designed (sec6 §6.8); implementation in progress (increment 1 of 5). `asOf` live on GraphQL and on the QuerySpec, **not combinable with `RelatedCondition`** until the temporal join (M5a). |
| ADR-0003 | Aperture ADR-0021 (linear MVP first) | Aperture ADR-0021 `Deferred (MVP)`, then Aperture ADR-0026 (portal-first MVP) ratified the deferral. Independently, Aperture ADR-0035 cites Reel ADR-0003 as the reason it rejects arbitrary join semantics. |
| ADR-0005 | Aperture ADR-0014 "should be superseded/rewritten" | Ratified as posed (`Accepted`) — the split made it the shell's question; Aperture chose its stack in **ADR-0030** (React + Vite SPA) and **ADR-0031** (config-selected layouts). |
| ADR-0005 | "agent surface (MCP/API, Aperture ADR-0021)" in the core | Hosted by **Mosaic** (Mosaic ADR-0009), with outbound delegation to a planner (Mosaic ADR-0010). Reel's position: ADR-0007. |
| ADR-0005 | Aperture ADR-0008 (capability-scoped client), auth-unaware core | Reinforced: platform ADR-0006 (OIDC proxy in recipes; PDP stays Bridge), Aperture ADR-0038 (presents identity, never authenticates), Mosaic sec8 (inbound actor injection). Exon-side follow-ups E1–E3 (Mosaic ADR-0010) fall to Reel on migration. |
| ADR-0005 | View Contract at `drylims:platform/design/view-contract.md` | At `datahelix:platform/design/view-contract.md`; still a **stub** ("not yet binding"); open questions on versioning, data shape vs. as-of, conformance, `schema_ref` ← Mosaic `schema_typing`. Aperture ADR-0037 (`graph` view primitive, Proposed) is the first new primitive proposed since. |

## Interfaces `prefab/data-stories.md` said Reel would need

| # | Interface | Then | Now |
|---|---|---|---|
| 1 | Serializable selection/cohort object, pivotable | "core-loop query-state, made central" (unbuilt) | **`QuerySpec`** (Aperture ADR-0035; Mosaic ADR-0009) — Reel ADR-0006 |
| 2 | Relationship-existence filters with predicates; group-by + count | ⚠️ gaps in Hippo's GraphQL (equality + AND/OR + offset + FTS) | **Closed.** Mosaic ADR-0006 typed filters incl. relationship predicates (`RelatedCondition`); ADR-0007 counts / `facetCounts` / min-max / `order_by`; exposed as MCP tools (`count_query_spec`, `facet_query_spec`, `field_range_query_spec`, `search_query_spec`) |
| 3 | Schema grounding for the LLM | `hippoSchema` introspection resolver | `mosaic://schema` + **`mosaic://capabilities`** (server-derived manifest: per-field legal ops, `enum_values`, predicate flags, aggregatable/orderable/searchable) + the `construct-query-spec` prompt |
| 4 | View-primitive set (noun-catalog) | Aperture ADR-0010 | Unchanged (`Proposed`); becomes the View Contract's primitive catalog |
| 5 | Dry-run validation of the LLM's output | "the keystone guardrail" (unbuilt) | **`validate_query_spec`**, total and introspection-driven, with per-criterion coded errors; re-run in-process by Mosaic's relay on every turn (Mosaic ADR-0010 term 2) |

Open questions **DS-2** (does the GraphQL support join filters and group-by+count?) is therefore
**resolved: yes**, and **DS-3** (which schema grounds the probe?) is **resolved in practice**: the
probe ran against `mosaic-demo-small`'s four-class demo schema (`Donor`/`Sample`/`Workflow`/
`Dataset`), not the brain-bank schema — a deliberate choice to keep the probe generic and
scannable. Both are updated in `prefab/data-stories.md`.

## Keystone probe

Reel's INDEX said "**First action: run the keystone probe**." It has run — as **Exon**
(`mosaic-demo-small/exon/`, 2026-08-05 → present), with a measuring harness rather than a
one-off demo:

| Question the probe asked | Answer so far |
|---|---|
| Can an LLM reliably emit a typed artifact that passes a total validator? | **Yes, structurally**, with hosted models: forced tool calls comply, plans are well-shaped, relationship criteria collapse correctly into a single `RelatedCondition`. Local 12B models did not (ignored forced tool calls under load). |
| Does "validated" mean "correct"? | **No.** The dominant failure class is *faithfulness*: enum values paraphrased from the utterance; a counting question answered with a valid row listing; a reference field placed where a traversal was needed. Validators cannot catch these by design. |
| Does context tuning move it? | Somewhat: holdout 0.50 → 0.67 on Sonnet 5 after one refine iteration; flat on Haiku 4.5. Value-vocabulary grounding (now in the manifest) is the identified lever. |
| Is the loop reproducible/measurable? | Yes — per-case pass rate over *k* samples, strict count, flake rate, environment failures withheld from the refiner, holdout isolation enforced. |

Consequence for Reel: ADR-0001–0004 were gated on this probe; the model held. Ratification is a
status flip the deciders make (see INDEX Decision Queue).
**Ratified 2026-11-10:** ADR-0001–0005 are `Accepted` — 0001, 0004 and 0005 amended to their v1
forms, 0002 with the `asOf` ⟂ `RelatedCondition` v1 limit; see each ADR's ratification section. The probe's faithfulness gap is Reel's
first measured backlog (`proposals/exon-migration.md` §4).

## Where Reel sits on the platform roadmap

`datahelix:platform/design/roadmap-1.0.md` (2026-07-07): "Data stories (Aperture ADR-0022–0025)
and in-app chat stay deferred past 1.0"; the 1.0 AI beachhead is "portal-complete + MCP agent
surface" with the keystone probe as P3.5. Since then the MCP surface landed in **Mosaic** (not
Aperture as P3.4 assumed) and the probe runs as Exon. Reel's v1 (linear stories over QuerySpec)
is therefore **post-1.0 by roadmap**, while its prototype is already load-bearing for Aperture's
planned chat MVP. Reconciling the roadmap wording (ADR-0022–0025 → Reel ADR-0001–0004; P3.4 →
Mosaic ADR-0009) is a `datahelix` doc fix, noted for its owners.

## Known stale references in sibling repos (not Reel's to fix; noted for their owners)

- **Aperture:** tombstones ADR-0022–0026 and `instruction-path-model.md`/`prefab/data-stories.md`
  cite `drylims:platform/design/decisions/ADR-0001` and `proposals/reel-split.md`; the INDEX
  Decision Log shows 0022–0025 as `Deferred (MVP)` while the files say `Superseded by Reel
  ADR-000N`, and omits the superseded 0026 (headless core) row entirely; README still links
  Hippo.
- **DataHelix:** `roadmap-1.0.md` cites Aperture ADR-0022–0025 for data stories; `mkdocs.yml`
  has no Reel section; Reel unmounted (platform ADR-0003 Notes).
- **Mosaic:** ADR-0001's Context cites "Aperture ADR-0023" (now Reel ADR-0002) — accurate as
  history, but the live pointer is Reel.
