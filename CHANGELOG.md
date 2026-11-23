# Changelog

## v0.3.0 — 2026-11-23 (the `/turn` append plans from the reader's edited draft, behind a request-shape guard; pairs with Aperture v0.8.0)

Not breaking: every request v0.2.0 accepted is still accepted with the same
answer. The difference is that an append whose `query_spec` differs from the
draft the `turns` derive is now planned from, where v0.2.0 answered 400.
**Aperture v0.8.0 needs this release.** Its conversational panel sends the
reader's result-table columns back on every refinement turn, and Reel v0.2.0
answers 400 to those.

### Changed

- **A `/turn` append plans from the request's `query_spec`** (#26; turn
  contract Amendment 2, which amends Decision 9). The request's draft goes to
  the planner as `existing_query_spec`, and it no longer has to equal the
  draft derived from `turns`. This is the unlock path Decision 9 itself named,
  and it lets the reader edit a draft between turns (aperture#89: the columns
  they chose). Edits (`edit_turn_id`) are unchanged.
  - `query_spec` must be `null` exactly when the turns derive no draft.
    Otherwise the answer is 400.
  - A held turn in the planning base is still a 400 (Amendment 1), and it is
    checked first.

### Added

- **A request-shape guard on an appended `query_spec`** (#26). The answer is
  400, naming the first problem, unless:
  - `v` is the integer `1`;
  - `anchor` is a string;
  - `mode` is `AND` or `OR`;
  - `criteria` is a list;
  - `columns`, if present, is a list of `{path, mode?}`, where `path` is a
    non-empty list of non-empty strings, `mode` is a string, and there are no
    other keys.

  This is **not query validation** (Reel ADR-0007). Reel checks only the
  envelope the planner reads. Mosaic's relay still re-validates every proposal
  Reel returns, so an edited draft can shape what is proposed but can't make
  anything unvalidated run.

### Docs

- `design/turn-contract.md` **Amendment 2 (2026-11-23)**. Decision 9's text
  is kept, with a pointer to the amendment. `design/INDEX.md` notes it.

### Upgrading

- No configuration change. Deploy Reel v0.3.0 **before or together with
  Aperture v0.8.0**. Aperture v0.8.0 against Reel ≤ v0.2.0 gets a 400 on
  every refinement turn once the result table holds a traversal column.
- Still needs Mosaic v0.16.0 or later, as in v0.2.0. Mosaic's relay forwards
  `query_spec` unchanged, so it needs no change.
- Pull `ghcr.io/datahelix-platform/reel:0.3.0`, pinned by the digest in the
  release's `image-digest.json`.

## v0.2.0 — 2026-11-21 (BREAKING: needs Mosaic ≥ v0.16.0 for `mosaicSchema`; held-for-review turns, redacted failure logging, transient-error retries)

**Breaking release.** Reel now reads Mosaic's schema through the renamed
`mosaicSchema` GraphQL root field (datahelix platform ADR-0007). Mosaic v0.16.0
removed `hippoSchema` with no alias, so this release needs **Mosaic v0.16.0 or
later**. Against an older Mosaic, schema grounding fails. Read `### Upgrading`
before deploying.

### Changed

- **BREAKING: schema grounding queries `mosaicSchema`** (#24; platform ADR-0007,
  Q3). `fetch_mosaic_schema` in `planner/capabilities.py` asks for, and reads,
  `mosaicSchema` instead of `hippoSchema`. Reel never called `hippoEntityType`.
  The docs now give the renamed contract identifiers (`mosaicSchema`,
  `mosaic_core`, the `mosaic_*` annotation keys, and the `X-DataHelix-Actor`
  header).
- **Images publish to `ghcr.io/datahelix-platform/reel`** (#9). v0.1.0 was
  published at `ghcr.io/bu-neuromics/reel`. The repository URLs in
  `pyproject.toml` and the docs point at `datahelix-platform`.
- **The anchor example names no schema class; class families are grounded**
  (#18, closes #10). The `anchor` description in `SPEC_TOOL` / `TURN_TOOL` now
  gives the format (the singular class name, spelled as one of the grounding's
  `entity "..."` names) instead of the example `"Sample"`. That example led the
  planner to propose abstract anchors on the brain bank schema. When the
  capability manifest carries a hierarchy (Mosaic ADR-0014's `abstract` /
  `parent` / `kinds`), the grounding gains a "Kinds of entities" section. It
  tells the planner which families have no collection of their own and which
  concrete kinds to anchor on instead.

### Added

- **Held-for-review turns after a mid-path edit** (#22, closes #19; turn
  contract Amendment 1, #21; Reel ADR-0004 point 2).
  - After an edit, a downstream step whose QuerySpec changed carries
    `held_for_review: true` and `previous_query_spec`, the spec the user last
    accepted, or `null`. A held turn keeps its status; there is no new status.
  - The `/turn` response adds `held_turn_ids` beside `suspended_turn_ids`.
  - Planning on top of a held turn is refused with a 400 that names the turn.
    This applies to an append, or an edit after the held turn. Editing the held
    step itself, or anything before it, is allowed.
  - An unheld turn has exactly the previous wire shape.
- **The turn contract lives in Reel**, at `design/turn-contract.md` (#21). It
  restates the shipped contract (mosaic-demo-small's Decision 8) and records
  Amendment 1.
- **A missing turn `status` is inferred** (#17, closes #11). A payload with a
  `query_spec` object becomes a `proposal`. One with no spec and a non-empty
  `message` becomes a `clarification`. A present-but-invalid status, a
  non-object `query_spec`, or a clarification carrying a spec is still
  rejected. `TurnAttempt.status_inferred` records the inference for evals.
- **Reel logs its own failed turns, redacted by default** (#17). The facade
  logs one WARNING when it gives up on a turn, and `/turn` logs one per 502.
  - By default these lines carry identifiers, counts, the utterance's length
    and a short hash, the model, and only the exception type and schema field
    of each parse error. They never carry the utterance or values from the
    model's output.
  - `REEL_LOG_UTTERANCES=1` logs the utterance and the full errors instead.
  - The README has a new Configuration section.
- **Transient provider errors are retried** (#7, fixes #5). litellm's
  `ServiceUnavailableError`, `RateLimitError` and `InternalServerError` are
  retried up to `REEL_TRANSIENT_RETRIES` times (default 3). The backoff is
  exponential: `REEL_RETRY_BACKOFF_S`, capped at `REEL_RETRY_BACKOFF_MAX_S`.
  These retries don't use up `MAX_ATTEMPTS`. Error messages now separate a
  provider outage from a misconfiguration.
- **The release workflow notifies DataHelix** (#16, closes #15). After
  publishing, it sends the release's `image-digest.json` to
  `datahelix-platform/datahelix` as a `component-release` dispatch, so the
  bump bot can open the lock PR. This step is best effort and never fails a
  release.

### Fixed

- **Echoed `error` turns no longer cause 422s** (#7, fixes #6). `/turn` accepts
  `status: "error"` on incoming turns (Aperture echoes them back) and drops
  them before planning. Reel itself still never sends `error`.

### Docs

- ADR-0001–0005 ratified in the 2026-11-10 decision pass. ADR-0001, 0004 and
  0005 were amended (#20).
- The README acknowledges the National Center for PTSD Brain Bank (#8).

### Upgrading

- **Upgrade Mosaic to v0.16.0 or later first.** Reel v0.2.0 against Mosaic
  ≤ v0.15 fails schema grounding, because `hippoSchema` no longer exists on
  the newer server and `mosaicSchema` doesn't exist on the older one.
- **Pull the image from `ghcr.io/datahelix-platform/reel`.** Pin it by the
  digest in the release's `image-digest.json`.
- Optional: set `REEL_LOG_UTTERANCES=1` only where logging user utterances is
  acceptable. The default is redacted.

## v0.1.0 — 2026-09-29

First release: the release pipeline and the turn path carried from the Exon
prototype (#4). Image at `ghcr.io/bu-neuromics/reel`.
