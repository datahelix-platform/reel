# The conversational turn contract (v1 wire form of `Instruction`)

**Status:** 🟢 Canonical for amendments, from 2026-11-10. **Owner:** Reel (ADR-0008 §3).
**Depends on:** ADR-0001 (instruction path), ADR-0004 (mid-path edit), ADR-0006 (State =
QuerySpec), ADR-0007 (Reel behind Mosaic's validating relay), ADR-0008 (this contract is the v1
wire form of `Instruction`; Reel owns it).
**Feeds into:** Mosaic ADR-0010 (the relay that carries it; item 7: "the wire contract is not
Mosaic's to change … an amendment [in the planner's repo] first, then here"), Aperture ADR-0039
(the conversational panel that renders it), `src/reel/serve/http.py` (the endpoint that serves it).

## Where this contract was defined, and why it now lives here

The contract was specified as **Decision 8** of
`BU-Neuromics/mosaic-demo-small` `openspec/changes/add-exon-conversational-contract/design.md`
(with Decision 5 for edit semantics), and implemented by Exon's endpoint and Mosaic's relay
(`converse_query_spec`, mosaic PR #200). ADR-0008 §3 moves ownership of the contract to Reel at
migration. Phase B-runtime carried the endpoint (`src/reel/serve/http.py`) but not the spec text:
runbook step 6 (`../proposals/exon-migration.md`, Phase B-runtime) has not run. Until this file
existed, the only written copy was in a repo Reel does not modify, and Mosaic ADR-0010 still cites
that copy.

From 2026-11-10 **this file is where the contract is amended.** Its base section below restates
Decision 8 as shipped. It does not change it. The demo-repo text is the historical record up to
Amendment 1. Pointing that text and Mosaic ADR-0010 at this file is the owning repos' work:
`mosaic-demo-small` at migration Phase C3, and Mosaic when it next touches ADR-0010.

Every amendment is **additive**. A field this file adds is optional on the wire. A peer that
predates it must keep working: it ignores an unknown field it receives and never sends one.

## Base contract (Decision 8, as shipped)

One endpoint, `POST /turn`, JSON in and out. Mosaic's `converse_query_spec` calls it
server-to-server; a browser never does.

**Request**

```
{
  "utterance":    "<new NL turn text>",
  "query_spec":   <QuerySpec | null>,   # current draft; null on the first turn
  "turns":        [<Turn>, ...],        # full prior history, oldest first
  "edit_turn_id": "<id> | null"         # redo an earlier turn (ADR-0004); null = append
}
```

**Turn** (in the request history and in the response)

```
{
  "id":         "<stable turn id>",
  "utterance":  "<the NL input for this turn>",
  "status":     "proposal" | "clarification" | "suspended",
  "query_spec": <QuerySpec | null>,     # non-null iff status is proposal
  "message":    "<NL restatement or clarifying question>"
}
```

Inside Reel, a clarification may carry `"resolution": "answered"`
(`add-schema-discovery-for-query-building`): it answered a schema-discovery question rather than
blocking, and it does not suspend on an edit. That marker is **not on the wire**. `TurnModel`
does not declare it, so the response drops it, and `edit_turn` reads it only from a fresh
recompute.
Mosaic adds a fourth status, `"error"`, on its own side (planner unreachable, out-of-contract
response, or failed re-validation). Reel never emits it, and the endpoint drops an echoed `error`
turn from the history before planning.

**Response**

```
{
  "turn":               <Turn>,         # this call's result (the new turn, or the redone one)
  "suspended_turn_ids": ["<id>", ...],  # turns an edit suspended; [] when not editing
  "turns":              [<Turn>, ...]   # the FULL conversation after this call
}
```

`turns` is authoritative: a caller replaces its list with it. `turn` and `suspended_turn_ids`
duplicate information already in `turns` and never contradict it. Mosaic re-validates **every**
proposal in `turns`, not only `turn` (Mosaic ADR-0010 term 2).

**Current draft.** The draft at the end of a turn list is the `query_spec` of its last `proposal`.
On an append, the request's `query_spec` must equal the draft derived from `turns`. If they
differ, the endpoint answers 400 (Decision 9).

## Amendment 1 (2026-11-10): changed downstream steps are held for review

**Decided by** labadorf, 2026-11-10 decision pass. **Implements** ADR-0004's v1 rule, point 2.
**Reel implementation:** [reel#19](https://github.com/datahelix-platform/reel/issues/19).

### What it decides

After a mid-path edit (`edit_turn_id`), Reel re-asks each downstream step's original utterance
(ADR-0004, point 1). A downstream step whose resulting QuerySpec **differs from the QuerySpec the
user last accepted for that step** is **held for review**. The re-ask is returned as a proposal
awaiting the user's confirmation. It is **not applied** and nothing executes it until the user
confirms it. The held turn carries its previous QuerySpec so a client can show the difference.
A step that re-plans to the same QuerySpec is carried over without being held.

### Per-turn fields (optional, additive)

| Field | Type | Meaning |
|---|---|---|
| `held_for_review` | `bool`, absent = `false` | This step changed after an upstream edit and awaits the user's confirmation. Not applied. |
| `previous_query_spec` | `QuerySpec \| null`, present iff `held_for_review` | The step's QuerySpec as the user last accepted it, before the edit. `null` when the step previously carried no spec (a clarification, or a suspended turn). |

A held turn keeps the `status` its re-ask produced:

- `proposal`, with the new `query_spec`, is the usual case.
- A **non-blocking** clarification (one Reel classed as answered; on the wire, `status:
  "clarification"`, `query_spec: null`) is held when the step previously carried a spec. Its constraint would otherwise disappear from the draft
  without the user seeing it.

A blocking clarification is never held. It is `suspended`, as before (ADR-0004, point 3).

**Why a flag and not a new status.** A new status (`"held"`) would be rejected by every relay
that predates it: Mosaic's per-turn check (`_reject_malformed_turn`) allows exactly
`proposal|clarification|suspended`, and an unknown status turns the whole response into an
`error` turn. Every edit would fail until Mosaic upgraded. A held step is still a proposal (it has
a QuerySpec Mosaic must re-validate before any client sees it), so `status: "proposal"` keeps it
inside Mosaic's existing re-validation loop with no change there. Being held is a **review state**
layered on that proposal. It is not a validity state, so it doesn't belong in `status`, whose
values map onto ADR-0001's `valid | invalid | suspended`.

### Response field (optional, additive)

| Field | Type | Meaning |
|---|---|---|
| `changed_turn_ids` | `[id]`, absent = `[]` | Ids of the turns in `turns` that are held for review, in conversation order. Always `[]` on an append. Never contains the edited turn itself (the user just wrote it) or a suspended turn (`suspended_turn_ids` reports those). |

It sits beside `suspended_turn_ids` and, like it, duplicates per-turn state in `turns` without
ever contradicting it.

### What counts as changed

Each step has an **effective spec**: its `query_spec` if it is a `proposal`, otherwise `null`. A
recomputed step is held when its new effective spec differs from its **baseline**:

- the old turn's `previous_query_spec`, if the old turn was itself held (its baseline is what the
  user last accepted, not a spec they never confirmed);
- otherwise the old turn's effective spec.

Comparison is equality of the normalized QuerySpec objects. An implementation may compare
`criteria` order-insensitively to avoid spurious holds. In terms of the old and new turn, the
cases are:

| Old step | New step | Held? |
|---|---|---|
| proposal, spec S | proposal, spec S | no |
| proposal, spec S | proposal, spec S′ ≠ S | **yes**, `previous_query_spec: S` |
| proposal, spec S | answered clarification | **yes**, `previous_query_spec: S` |
| clarification / suspended | proposal | **yes**, `previous_query_spec: null` |
| clarification / suspended | answered clarification | no |
| any | blocking clarification | no; **suspended** (and cascades) |

A step that is not held comes back without either field: the held state is cleared, because its
spec now matches what the user accepted.

### Steps after a held step

**Rule (proposed; open question 1 below):** a held step does **not** suspend or cascade. The steps
after it are re-asked against the held step's **new** spec, as if it were confirmed. Each is
compared with its own baseline and held if it changed.

Consequences, which together keep "not applied" true:

1. **The applied draft stops at the first held turn.** A client executes, and shows as current,
   only the draft derived from the turns *before* the first held turn. Every turn from the first
   held turn on is pending, held or not. An unchanged step after a held step is pending only
   because of its position. It needs no review of its own and becomes applied once the held steps
   before it are confirmed.
2. **Confirmation is in order.** Confirming a held turn accepts its new spec. Only the first held
   turn can be confirmed on its own, because each later step was planned against the specs before
   it. A client may offer "confirm all" (confirm each held turn in order).
3. **Confirmation is client state; it needs no planner call.** The caller owns the conversation
   (Decision 3), so confirming a turn means removing `held_for_review` and `previous_query_spec`
   from it in the caller's copy. The caller sends the confirmed turn back as an ordinary turn on
   its next request. Mosaic validated the spec when it relayed it, so the client can execute it.
4. **The way to reject a held step is to edit it** (`edit_turn_id` on that step, with new
   wording). That recomputes the steps after it against the new baseline. v1 has no
   "restore previous spec" operation: the previous spec was planned against the pre-edit
   upstream and may not hold under the new one (open question 2).
5. **A request must not plan on top of a held turn.** The base a request plans against is
   `turns[:i]`, where `i` is the edited turn's index, or `len(turns)` for an append. If that base
   contains a turn with `held_for_review: true`, Reel answers **400** and names the turn: confirm
   it or edit it first. An edit at or before the first held turn is always allowed. A client that
   never echoes the flag never meets this rule.

### Compatibility (why every addition is optional)

| Reel | Relay / client | Result |
|---|---|---|
| amended | Mosaic and Aperture today | Mosaic's per-turn check ignores unknown keys, so the MCP envelope carries `held_for_review` and `previous_query_spec` on the turns through (`turns` is passed through as received). The relay rebuilds the envelope as `{turn, suspended_turn_ids, turns}`, which drops `changed_turn_ids`, and GraphQL `ConverseResult`'s fixed fields drop all three. A held step therefore reaches the user as an ordinary proposal. This matches the behaviour before the amendment: no regression and no error turns, but the hold is invisible until the follow-ups land. |
| amended | Mosaic passes the fields through; Aperture ignores them | Same as above at the UI. |
| amended | all three amended | Held steps are shown with their difference and a confirm action; nothing past the first held turn runs until it is confirmed. |
| pre-amendment | amended Mosaic / Aperture | The fields are never present; absent means not held. No change in behaviour. |

**Follow-ups (filed when this amendment was written; they start after it merges):** Mosaic passes
the fields through the relay and adds them to GraphQL `ConverseResult` and its turn type, under
Mosaic ADR-0010. Aperture's conversational panel shows held steps with the old/new difference and
a confirm action, under Aperture ADR-0039.

### Open questions for the maintainer

1. **The rule for steps after a held step**, above: re-ask against the held step's new spec and
   hold each one that changed. The alternatives are to suspend everything after a held step (no
   model calls, but every downstream step needs re-prompting after any change) or to re-ask
   against the last *applied* spec. The second is inconsistent: once the user confirmed, the
   later steps would have been planned against the wrong base.
2. **No reject-and-restore in v1.** Restoring `previous_query_spec` as-is would apply a spec
   planned against the pre-edit upstream. Should a "keep the old meaning" action exist? If it
   should, it is a planner call (re-ask with the old spec as a hint), not a client-side swap.
3. **Field names.** `held_for_review` and `previous_query_spec` (per turn) and `changed_turn_ids`
   (response) follow the 2026-11-10 brief. `held_turn_ids` would name the response field after
   what it lists. It is cheap to change before the Mosaic and Aperture follow-ups start.
