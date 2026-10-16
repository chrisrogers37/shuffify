# Workshop sources

Inbox, Archive and Routines share the Workshop's occurrence order, review, Save and navigation guard. Their state belongs to the signed-in actor and exact target playlist. Closing a task does not discard its inputs, staged selections, accepted operation or uncertain request.

## Staging and Save

Adding pending tracks stages exact inbox row IDs. Adding archive rows stages their captured archive ID and ordered URI selections. Draft additions append missing URIs through the Workshop controller; repeated inbox IDs and physical archive rows remain distinct selections. Manual removal queues only saved occurrences for auto-archive. Appending those tracks back cancels excess queued copies. Loading a named draft or comparing Spotify does not fabricate archive removals.

Save reviews the exact target order and the source tasks together. The admission registry reserves the target and every frozen source request atomically. Only the original successful target operation with the reviewed saved order activates the source chain. Promotion, archive restoration and archiving run in order; an append to a shared source waits for its preceding removals. Each source task keeps its original request key, body, dependencies and accepted operation ID. A source failure leaves the target save recorded.

A changed pairing never retargets staged or saved work automatically. Unsubmitted staging offers an explicit fresh review naming the original and replacement source. Saved or uncertain work always keeps its captured source.

## Receipts and recovery

Dismissal is queued and DB-first. It hides exactly the selected shown IDs and consumes only confirmed dismissal facts. Optional collection cleanup failure does not restore those inbox rows. Dismiss all freezes the shown selection, submits batches of at most 200 IDs and stops if an earlier receipt needs attention.

An unknown admission offers only an explicit retry of the same request key and body. A blocking operation ID is displayed separately and never becomes the original receipt. Known IDs continue observation independently of mounted tasks. Full server retry hints apply before another source submission.

A safely settled source failure or verified rollback can receive an explicit Finish review. It checks the saved target order and original source identity, reserves an unchanged body under a fresh explicit key and rebinds only unsubmitted dependents. An initial proved 400/401/403 refusal may use that same path when the registry confirms no accepted ID and no earlier ambiguity. Unknown, blocking, active and required-record attention receipts cannot use it.

Read-only inspection remains separate from the saved receipt. It cannot establish past attribution or settle uncertain writes. Verified source cleanup with failed local records keeps those known source facts and its claims. The shared owned-operation restoration entry is available only when the server offers a reviewed restoration.

A terminal unidentified creator can be acknowledged only through the registry's closed proof: original owned accepted ID, allowed creator kind, unknown creation, no known created ID and no started association. Explicit acknowledgement retains the orphan record and permits a separate explicit create or exact owned selection. A fresh playlist list never adopts a matching name.

## Permissions and local settings

Pair settings load before manual track editing, so auto-archive behavior is known. A failed read retains its claim and offers explicit retry. Changing the archive binding invalidates cached rows and cancels stale reads.

Collect holds inbox/source mutations while independent target draft work remains available. A live action that writes the target holds target editing. An exact running saved routine holds its edit/delete controls. Pause/Resume stays available. Local settings use their existing completed responses; an uncertain write requires an explicit current-settings read rather than replay. Per-source count edits survive task closure and revert to confirmed values on a known refusal.

Routines retain exact IDs, revision tokens, timing, registration and authoritative state labels. Run now reads a fresh server review and submits one parent operation. Results preserve partial child effects, unknown counts, history and actual origin; a successful status read is not itself a successful action.
