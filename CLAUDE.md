# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Component Overview

**Reel** is the DataHelix platform's **AI-native data-story engine** — the headless
instruction-path core that composes typed query states (platform **QuerySpec** instances) into
replayable, reproducible stories and produces **View Contract** instances over the domain graph.
It does **no rendering** (the Aperture portal and other shells consume its output) and **no
query validation or execution of its own** (Mosaic's MCP boundary does both; Reel is a planner
behind Mosaic's validating relay — ADR-0007). See `design/vision.md` for the charter and
`design/platform-alignment.md` for how Reel's docs map onto the current platform.

Reel was split out of Aperture on 2026-06-22 (boundary: DataHelix platform ADR-0003 —
`https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/ADR-0003-reel-data-story-engine-separate-from-portal.md`;
its Outcome section records the execution). Reel is **not** mounted in the `datahelix` repo as a
submodule yet, so cross-repo links here are GitHub URLs or `datahelix:<path>` / `mosaic:<path>`
notation, not relative paths.

## Names (current)

- **DataHelix** — the platform (formerly BASS; the integration repo was `drylims`, now
  `datahelix-platform/datahelix`). Never write BASS/drylims in new text.
- **Mosaic** — the LinkML runtime / structured domain graph (formerly Hippo, Mosaic ADR-0004;
  dist `datahelix-mosaic`, `import mosaic`). Write "Mosaic" in prose. The data-contract
  identifiers were renamed in Mosaic v0.16.0 (platform ADR-0007): the GraphQL root fields are
  `mosaicSchema` / `mosaicEntityType`, the core schema module `mosaic_core`, annotation keys
  `mosaic_*`, and the actor header `X-DataHelix-Actor`. Don't write the old `hippo*` spellings
  in new text.
- **Aperture** — the rendering portal (React + Vite SPA); owner of the **QuerySpec** artifact
  (Aperture ADR-0035).
- **Reel** — ratified name (platform ADR-0003), no longer a codename.
- **Exon** — Reel's running prototype in `BU-Neuromics/mosaic-demo-small` (`exon/`). It migrates
  here per ADR-0008 / `proposals/exon-migration.md`. **Do not modify `mosaic-demo-small` from
  this repo**; until Phase C of the runbook it is the live prototype and another repo's property.

## Repository Layout (current and planned)

```
design/
├── INDEX.md                  # Document map + Decision Log (source of truth)
├── platform-alignment.md     # crosswalk: Reel docs ↔ current platform state
├── vision.md
├── instruction-path-model.md # the formal model (working)
├── prefab/data-stories.md    # the keystone MVP / probe (working)
└── decisions/                # ADRs (0001–0005 migrated from Aperture 0022–0026; 0006+ native)
proposals/
└── exon-migration.md         # the Exon → Reel migration runbook (preparation phase)
src/reel/                     # (planned) datahelix-reel — planner/, story/, harness/, serve/
tests/                        # (planned) no-model tests carried from Exon
openspec/                     # (planned) specs carried from Exon's conversational-contract change
```

Packaging follows platform ADR-0002: distribution `datahelix-reel`, bare import `reel`.

## Spec Structure

Design docs live in `design/`. Reel is designed **ADR-first**: load-bearing decisions are recorded
as ADRs in `design/decisions/`, indexed by the **Decision Log** in `design/INDEX.md` (the source
of truth — always check it before drafting or modifying design content). `vision.md` and the
working-design docs (`instruction-path-model.md`, `prefab/`) provide narrative context; **cite
ADRs for decisions**. `platform-alignment.md` is a living crosswalk — update it when a referenced
ADR changes status.

## Design Decisions (ADRs)

- Decisions are recorded as **ADRs** per the **platform-wide convention** — canonical process and
  template in `datahelix:platform/design/decisions/README.md`; local process notes in
  `design/decisions/README.md`; local template `design/decisions/_template.md`.
- ADR-0001–0005 were **migrated and renumbered from Aperture ADR-0022–0026** on the split; each
  carries a dated **Status update** section (2026-09-11) — their Decision sections are history,
  the status updates are what changed around them.
- **Cross-reference convention:** bare `ADR-NNNN` is a **Reel** ADR; others are qualified
  **Aperture ADR-NNNN**, **Mosaic ADR-NNNN**, **platform ADR-NNNN**. Caveats: low numbers overlap
  (Reel ADR-0003 *topology* vs. Aperture ADR-0003 *config-as-LinkML*); Aperture reused **0026**
  after the split (cite Reel ADR-0005 for the headless core); "Hippo ADR-0001" in older text is
  **Mosaic ADR-0001**; the platform's split ADR is **0003** (platform ADR-0001 is the
  certified-frontier ledger).
- New/non-trivial decisions get an ADR; open questions are `Proposed` ADRs ratified by a status
  flip. Decisions are never deleted — reversals `Supersede` with a forward pointer.
- When a Reel ADR imposes a requirement on another component, cross-reference that component's ADR
  / spec so the dependency is legible from both sides (e.g. ADR-0002 ↔ Mosaic ADR-0001; ADR-0006 ↔
  Aperture ADR-0035 / Mosaic ADR-0009; ADR-0007 ↔ Mosaic ADR-0010).

## Writing Guidelines

- Keep the platform's **SDK-first** principle and the **typed/declarative substrate** invariants
  (config-as-LinkML-in-Mosaic, no middle scripting layer, view-descriptions/View-Contract-not-DOM,
  dry-run-validatable) consistent across design docs.
- Reel is **headless** — it produces View Contract instances, never pixels. Rendering belongs to a
  consumer (the Aperture portal, a notebook, a third-party shell).
- Reel is a **planner** — the query noun is the QuerySpec (ADR-0006); validation and execution
  are Mosaic's (ADR-0007). Never design a Reel-owned validator, executor, or query dialect.
- Reel source is **generic** — no domain (e.g. brain-bank) nouns; the domain schema is the
  deployment's LinkML (inheriting Aperture ADR-0002's principle). Domain-bearing eval fixtures
  (the demo schema, cases, expected results) stay in a sibling repo and are pointed at.
