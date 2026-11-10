# Reel — AI-Native Data-Story Engine (DataHelix platform)

**Reel** is the [DataHelix](https://github.com/datahelix-platform/datahelix) platform's **headless
data-story / instruction-path engine** over the domain graph. It turns natural-language and
UI-driven exploration into **typed, replayable, reproducible data stories** — sequences of
platform **QuerySpec** states plus the instructions that produced them — and emits **View
Contract** instances (a declarative spec + the data to show) for *any* renderer to visualize.
Reel does **no rendering** (the Aperture portal does) and **no query validation or execution of
its own** (Mosaic's MCP boundary does): Reel plans and composes.

## Status

**Design, refreshed 2026-09-11.** This repo carries design only; there is no Reel code yet. But
Reel's rung-1 prototype **is running** under another name: **Exon**, in
[`BU-Neuromics/mosaic-demo-small`](https://github.com/BU-Neuromics/mosaic-demo-small) (`exon/`) —
a schema-grounded NL → QuerySpec planner with a reliability harness, and a conversational turn
contract written in Reel's vocabulary so it can migrate here. That migration is decided in
principle ([ADR-0008](design/decisions/ADR-0008-exon-seeds-reel.md), `Proposed`) and prepared in
[`proposals/exon-migration.md`](proposals/exon-migration.md); it has **not** started, and
`mosaic-demo-small` is untouched until its preconditions hold.

Reel was split out of **Aperture** on 2026-06-22 so the config-driven portal could ship as the
rendering MVP while the AI-native engine matures on its own cadence. The boundary decision and
the joining interface live in the DataHelix platform repo:

- Boundary: [`platform/design/decisions/ADR-0003`](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/ADR-0003-reel-data-story-engine-separate-from-portal.md) — *Separate the data-story engine (Reel) from the rendering portal (Aperture)* (its **Outcome** section records the execution)
- Interface: [`platform/design/view-contract.md`](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/view-contract.md) — the renderer-agnostic spec-with-data-attached (still a stub)

## Where to start

- [`design/platform-alignment.md`](design/platform-alignment.md) — **read first if you last read
  Reel before 2026-09**: what moved (names, ADR numbers, the interfaces that now exist, the
  keystone-probe results).
- [`design/INDEX.md`](design/INDEX.md) — the design index + **Decision Log** (the source of truth).
- [`design/vision.md`](design/vision.md) — what Reel is and why.
- [`design/instruction-path-model.md`](design/instruction-path-model.md) — the formal data-story model.
- [`design/prefab/data-stories.md`](design/prefab/data-stories.md) — the keystone MVP / first probe.
- [`design/decisions/`](design/decisions/) — the ADRs: 0001–0005 migrated from Aperture
  0022–0026; 0006–0008 authored here (QuerySpec as v1 State; delegation to Mosaic's boundary;
  Exon seeds Reel).
- [`proposals/exon-migration.md`](proposals/exon-migration.md) — the migration runbook.

## Relationship to the DataHelix platform

| | |
|---|---|
| **Platform repo** | [`datahelix-platform/datahelix`](https://github.com/datahelix-platform/datahelix) — Reel is **not yet mounted** there as a submodule (Mosaic and Aperture are); mounting is follow-on work named in platform ADR-0003 and this repo's runbook. |
| **Domain graph runtime** | **Mosaic** (formerly Hippo, Mosaic ADR-0004) — [`datahelix-platform/mosaic`](https://github.com/datahelix-platform/mosaic). Hosts the MCP boundary (Mosaic ADR-0009) that validates/executes QuerySpecs and the validating relay (ADR-0010) that drives a planner such as Exon/Reel. Data-contract identifiers (`hippoSchema`, `hippo_core`) keep their historical spelling. |
| **Query artifact** | **QuerySpec** — Aperture ADR-0035 defines it; Mosaic ADR-0009 validates it; Reel composes instances (ADR-0006). |
| **Renderer** | **Aperture** — [`datahelix-platform/aperture`](https://github.com/datahelix-platform/aperture), the config-driven portal (React + Vite SPA); consumes View Contract instances; reaches a planner only through Mosaic. |
| **Packaging (planned)** | `datahelix-reel` distribution, `import reel`, `src/reel/` (platform ADR-0002). |

Reel is **headless** and **generic** (no domain nouns): the domain schema is the deployment's
LinkML, hosted in Mosaic.

## Configuration

The turn service reads its settings from `REEL_*` environment variables; the planner's model,
token and retry settings are documented where they are read, in
[`src/reel/config.py`](src/reel/config.py). Logging:

| Variable | Default | Effect |
|---|---|---|
| `REEL_LOG_UTTERANCES` | unset | Reel's WARNING lines for a failed turn are **redacted by default**: they name the turn id (for an edit), the request kind, the prior-turn count, the utterance's length and a short hash (first 12 hex of its sha256), the model, the attempt count, and for each parse error only the exception type and the field it names, never a value. Set to `1` to log the full utterance and the full parse and provider errors instead, for debugging on a deployment whose log you control. The 502 response body is not affected. |

## Conventions

Decisions are **ADRs** in `design/decisions/`, indexed by the Decision Log in `design/INDEX.md`,
per the platform-wide convention
([`platform/design/decisions/README.md`](https://github.com/datahelix-platform/datahelix/blob/main/platform/design/decisions/README.md)).
Inside Reel, bare `ADR-NNNN` is a Reel ADR; others are qualified **Aperture ADR-NNNN**, **Mosaic
ADR-NNNN**, **platform ADR-NNNN**.

## Acknowledgments

Reel is part of the DataHelix platform, developed by Adam Labadorf with the National Center for PTSD Brain Bank, U.S. Department of Veterans Affairs.

## License

MIT — see [`LICENSE`](LICENSE).
