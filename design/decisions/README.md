# Reel Design Decisions (ADRs)

Reel records design decisions as **ADRs** following the **platform-wide convention** — the
canonical process, lifecycle/statuses, and template live in the DataHelix platform repo at
[`platform/design/decisions/README.md`](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/README.md)
(see also that repo's root `CLAUDE.md`). [`_template.md`](./_template.md) is the local copy of
the canonical template.

> If a decision isn't recorded here, it isn't decided. Prose in `vision.md` is *context*; an ADR
> is the *decision*.

The [`../INDEX.md`](../INDEX.md) **Decision Log** table is Reel's index of record — one row per
ADR, status-tracked. Open questions are `Proposed` ADRs (the *decision queue*); ratifying one is
a status flip from `Proposed` → `Accepted`, not a new document. Decisions are never deleted —
reversals `Supersede` with a forward pointer.

## Provenance: migrated from Aperture

ADR-0001–0005 were authored inside the `aperture` component as **Aperture ADR-0022–0026** and
**renumbered** when the data-story engine was split into Reel (2026-06-22; boundary decision
DataHelix platform
[ADR-0003](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/ADR-0003-reel-data-story-engine-separate-from-portal.md),
whose **Outcome** section records the execution — no separate runbook landed). The originals are
`Superseded by` these in Aperture with forward pointers. The renumbering map:

| Reel ADR | was Aperture ADR | Topic |
|---|---|---|
| [0001](./ADR-0001-data-story-is-an-instruction-path.md) | 0022 | data story = instruction path |
| [0002](./ADR-0002-data-story-reproducibility-as-of-watermark.md) | 0023 | as-of watermark reproducibility |
| [0003](./ADR-0003-instruction-path-linear-first-general-schema.md) | 0024 | topology: general schema, linear validator |
| [0004](./ADR-0004-mid-path-edit-recompute-with-suspend.md) | 0025 | mid-path edit: recompute + suspend |
| [0005](./ADR-0005-headless-core-thin-shell.md) | 0026 | headless core + thin shell |

**Names in the migrated ADRs.** They were written when the platform was called BASS/`drylims`
and the graph runtime was Hippo. On 2026-09-11 their prose was updated to **DataHelix** and
**Mosaic** (Mosaic ADR-0004) and their cross-references re-pointed (the platform's split ADR is
**0003**, not 0001 — platform ADR-0001 is now the certified-frontier ledger). Data-contract
identifiers (`hippoSchema`, `hippo_core`, `hippo_*` annotation keys) deliberately kept their
spelling, matching Mosaic's own carve-out at the time. Platform ADR-0007 later retired that
carve-out: Mosaic v0.16.0 renamed them to `mosaicSchema`, `mosaic_core` and `mosaic_*`. The
ADRs' old spellings are left as written, with a dated note. Each migrated ADR carries a dated **Status update**
section noting what the platform has since decided or shipped; their Decision sections are
unchanged.

## ADR-0006 onward: authored in Reel

From ADR-0006 the numbering continues natively. ADR-0006–0008 (2026-09-11) record the Reel side
of cross-component decisions made while Reel had no code (Aperture ADR-0035, Mosaic ADR-0009/0010)
and the decision to seed Reel by migrating the **Exon** prototype
(`BU-Neuromics/mosaic-demo-small`) — see [`../../proposals/exon-migration.md`](../../proposals/exon-migration.md).

## Cross-reference convention

Inside Reel ADRs, bare `ADR-NNNN` refers to **Reel's own** ADRs. Other components' decisions are
always qualified:

| Prefix | Repo | Notes |
|---|---|---|
| **Aperture ADR-NNNN** | `datahelix-platform/aperture` | Low numbers overlap Reel's (Reel ADR-0003 *topology* vs. Aperture ADR-0003 *config-as-LinkML-in-Mosaic*). Aperture reused **0026** after the split, so "Aperture ADR-0026" is ambiguous — cite Reel ADR-0005 for the headless-core decision. |
| **Mosaic ADR-NNNN** | `datahelix-platform/mosaic` | Mosaic (formerly Hippo, Mosaic ADR-0004). Older docs say "Hippo ADR-0001" for graph-level as-of; it is **Mosaic ADR-0001**. |
| **platform ADR-NNNN** | `datahelix-platform/datahelix` `platform/design/decisions/` | Cross-component decisions. `datahelix:` prefixes a path in that repo (e.g. `datahelix:platform/design/view-contract.md`). |

When a Reel ADR imposes a requirement on another component, reference that component's ADR /
spec so the dependency is legible from both sides (e.g. ADR-0002 ↔ Mosaic ADR-0001; ADR-0006 ↔
Aperture ADR-0035 / Mosaic ADR-0009; ADR-0007 ↔ Mosaic ADR-0010).
