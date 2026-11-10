# ADR-0004: Mid-path edits recompute downstream and suspend-on-invalid (not discard)

- **Status:** Accepted (amended at ratification — see *Amendment and ratification (2026-11-10)*)
- **Date:** 2026-11-10 (proposed 2026-06-17; migrated to Reel 2026-06-22)
- **Deciders:** labadorf (decision pass 2026-11-10); originally labadorf, design session
- **Tracking issue:** [reel#19](https://github.com/datahelix-platform/reel/issues/19) (implement the hold; contract: `../turn-contract.md` Amendment 1)
- **Related:** ADR-0001 (instruction-path model), ADR-0002 (as-of watermark), Aperture ADR-0009 (dry-run validation), Aperture ADR-0020 (provenance events); `instruction-path-model.md` §6

> **Migrated from Aperture ADR-0025 (2026-06-22)** on the data-story-engine split
> (boundary decision `datahelix:platform/design/decisions/ADR-0003`, whose **Outcome** section
> records the execution). Renumbered 0025 → Reel 0004. Portal-decision references
> are qualified **"Aperture ADR-NNNN"**; bare `ADR-NNNN` refers to Reel's own ADRs.
> **Names updated 2026-09-11:** Hippo → **Mosaic** (Mosaic ADR-0004), BASS/drylims → **DataHelix**;
> data-contract identifiers (e.g. `hippoSchema`, `hippo_core`) deliberately keep their spelling.

## Context

A data story is "rewindable like CAD": the user scrolls back to instruction *k*, changes it, and
continues. Two questions, often conflated under the word "diverge," are actually orthogonal:

- **Topology** — does the story fork? (settled linear-first in ADR-0003)
- **Mid-path edit behavior** — when instruction *k* is edited, what happens to *k+1 … n*?

This ADR settles the second. The naïve options are **discard** downstream (truncate-and-rebuild)
or **recompute** it (replay forward). Discard is lossy — fixing a typo in step 2 of a 6-step
story throws away four turns. Recompute risks nondeterminism *unless* replay is pinned — which it
is, by the as-of watermark (ADR-0002).

## Decision

**On a mid-path edit, recompute downstream against the fixed as-of watermark, and
suspend-and-flag any downstream instruction that no longer validates** (rather than discarding
downstream work). Because replay re-evaluates typed ops as-of T (ADR-0002), recompute is
deterministic. A downstream instruction whose op no longer validates against the changed upstream
State (e.g. it references a slot the new filter removed) is marked **suspended** (CAD-style
"failed to regenerate") for the user to re-prompt — it is **not** silently dropped.

Edits operate at two layers, mirroring the "pull new data" discipline (ADR-0002):
- **Story content** is *mutable by replacement* — the recomputed path becomes the story.
- **Edit history** is **append-only**: a provenance event records the edit ("at 14:03 user
  rewound to step 2 and replaced downstream"), per Aperture ADR-0020. Nothing is truly lost
  (audit log), yet the story stays a clean line (content).

## Consequences

- Editing is predictable and non-lossy: downstream turns survive an upstream change when they
  still validate; only genuinely broken steps surface for attention.
- The dry-run validator (Aperture ADR-0009) gains a second role beyond apply-time checking: it is
  the **regenerate-time gate** that detects which downstream steps to suspend.
- Content-addressed nodes (ADR-0002) make recompute cheap — only reachable descendants whose hash
  changed re-run.
- **Deferred:** recompute is well-defined on a DAG (re-evaluate the reachable subgraph), but the
  *rewind/scrub UI affordance* is not; deferred with the DAG explorer (ADR-0003).

## Alternatives considered

- **Discard downstream (truncate-and-rebuild).** Simpler, but lossy and hostile to multi-step
  stories. Rejected: with as-of replay, recompute is safe, so there's no reason to throw work
  away.
- **Recompute and silently auto-repair** broken downstream steps (re-elaborate their NL).
  Tempting, but hides correctness changes from the user. Rejected for trust: suspend-and-flag
  keeps the human in the loop on any step that no longer holds.
- **Forbid mid-path edits; only append.** Avoids the question entirely but cripples the "CAD
  rewind" promise. Rejected.

## Notes / open sub-questions

- The narration of a recomputed story (do downstream prose summaries re-generate?) is left to the
  renderer; the model only guarantees the typed ops and artifacts are re-evaluated.

### Status update (2026-09-11) — exercised end to end by the prototype

- **Implemented in the prototype's contract.** Exon's conversational wire contract
  (`mosaic-demo-small` `add-exon-conversational-contract/design.md`, Decisions 5 and 8) carries
  an `edit_turn_id` on the request, a `suspended` turn status, and a `suspended_turn_ids` list on
  the response — this ADR's recompute-with-suspend, in Reel's own vocabulary by design.
- **The relay side confirms the semantics matter.** Mosaic ADR-0010 term 2 ("every QuerySpec in
  the response is re-validated, not only the turn just taken") was *widened* (mosaic PR #200)
  precisely because editing an earlier turn makes the planner recompute the turns that follow —
  freshly generated specs the deployment has never seen. The validator's second role named above
  (the regenerate-time gate) is therefore real and hosted in Mosaic.
- **Not in the prototype:** the **append-only edit-history log** (Exon Decision 7: no
  conversation persistence — the transcript lives in the caller). Persisting edit provenance is a
  migration delta (`../../proposals/exon-migration.md` delta D3), pending Aperture ADR-0020's
  provenance-event model (still `Deferred (MVP)` in Aperture).

### Amendment and ratification (2026-11-10) — the v1 rule is re-ask + flag + suspend

Amended, then accepted, by labadorf in the 2026-11-10 decision pass. The Decision above assumes
something v1 does not have: **deterministic replay of typed ops against a fixed watermark**. A v1
turn carries no op expansion (migration deltas D4/D5) and no watermark (ADR-0002, delta D2) —
only its utterance and the resulting full QuerySpec. So recompute in v1 cannot replay ops; it can
only ask the planner again. The v1 rule:

1. **Re-ask.** On an upstream edit, each downstream step's **original utterance is re-asked**
   against the new state (the edited draft plus the recomputed turns before it) — as
   `src/reel/story/conversation.py` `edit_turn` does today.
2. **Flag changed specs.** Any downstream step whose resulting QuerySpec **differs from its
   QuerySpec before the edit** is **flagged for the user to review**, not silently applied. A
   step that re-plans to the same QuerySpec is carried without a flag.
3. **Suspend on clarification.** A downstream step whose re-ask comes back as a blocking
   clarification is **suspended** (existing behaviour: `status: "suspended"`, the model's
   question as the re-prompt, every later step cascades to suspended without further model
   calls). A clarification that *answered* a schema-discovery question (`resolution:
   "answered"`) does not suspend — also existing behaviour.

**Target, unchanged:** deterministic typed-op replay against the as-of watermark, per the
Decision above, once the op deltas (D4/D5) and the watermark (D2) exist. At that point recompute
stops being a model call and flagging narrows to genuine validity changes.

**The trust trade-off.** This ADR originally rejected "recompute and silently auto-repair broken
downstream steps (re-elaborate their NL)" because it hides correctness changes. Re-asking the
original utterance *is* re-elaboration — the planner may read the same words differently against
the new state, and an LLM may not answer identically even when the edit should not have affected
a step. v1 accepts re-elaboration because there is nothing else to replay, and buys back the
trust property with the flag: **no downstream QuerySpec changes without the user seeing it.**
The costs: (a) review noise — a nondeterministic re-plan can flag a step whose meaning did not
change (spurious differences in an equivalent spec, or genuine drift); (b) the user carries the
burden of judging each flagged step; (c) a re-plan that is *wrong but identical* to the old spec
is, correctly, not flagged — the flag detects change, not correctness. Suspend-on-clarification
still catches the steps the planner itself cannot resolve.

**As built** (verified against `src/reel/story/conversation.py` and `src/reel/serve/http.py` at
commit `1b050e6`): points 1 and 3 are implemented; the HTTP response returns `turn`,
`suspended_turn_ids` and the full recomputed `turns`. **Point 2 is not**: a recomputed proposal
replaces the old turn with no comparison and no flag. Tracked in [reel#19](https://github.com/datahelix-platform/reel/issues/19). The append-only
edit-history log (Decision, second layer) remains unbuilt (delta D3).

**What "flagged" means (2026-11-10, labadorf): held for review.** A downstream step whose
QuerySpec changed comes back as a proposal **awaiting the user's confirmation**. It is not applied
or run, and it carries its `previous_query_spec` so a UI can show the difference. The response
lists these steps in `changed_turn_ids`, beside `suspended_turn_ids`. This is a change to the turn
contract (ADR-0008 §3; Mosaic ADR-0010 item 7), so it is recorded as **Amendment 1** of
[`../turn-contract.md`](../turn-contract.md), which also fixes how a held step affects the steps
after it. The Reel side is [reel#19](https://github.com/datahelix-platform/reel/issues/19). A user
sees a held step only after Mosaic passes the fields through its relay and GraphQL
`ConverseResult`, and Aperture's conversational panel (Aperture ADR-0039) renders them with a
confirm action. Both follow-ups start after the amendment merges.

