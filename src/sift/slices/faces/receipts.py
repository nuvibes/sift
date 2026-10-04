# SPDX-License-Identifier: AGPL-3.0-or-later
"""The names a face decision is recorded under in History, and the act written into each receipt.

The service writes these receipts and the queues read them back, so both take the words from here
and a receipt is never written under one spelling and read under another.
"""

from __future__ import annotations

#: What the set-aside pile is called wherever a decision about it is recorded.
FACE_QUEUE = "ignored"

#: The act of a receipt under `IDENTIFIED_QUEUE` where somebody agreed with a run of matches; it is
#: undone back to matches, while a receipt with no act (Sift's own run) comes off the file. See
#: `IdentifiedQueue.reverse`.
AGREED_WITH_MATCHES = "agreed-with-matches"

#: Somebody agreeing with every proposal for one person in one press: undone back to waiting to be
#: answered, since a proposal was Sift asking.
AGREED_WITH_PROPOSALS = "agreed-with-proposals"

#: Somebody refusing a run of faces for one person in one press. One act for proposals and matches
#: alike; the state each face goes back to is in the payload per face, because one press can refuse
#: a mixture.
REFUSED_FACES = "refused-faces"

#: A re-match withdrawing questions whose likeness fell under the asking line, the one act nobody
#: pressed. Undo puts each back at the confidence it had.
STOPPED_ASKING = "stopped-asking"

#: Yes on a "these groups may be her" card: undone by taking the name off every face of those groups
#: and grouping them again (`FaceService.unname_groups`).
NAMED_GROUPS = "named-groups"

#: No on that card. Not `REFUSED_FACES`, whose undo puts a name back: these faces carried nobody, so
#: undo only forgets the refusals (`FaceService.unrefuse_groups`).
REFUSED_GROUPS = "refused-groups"

#: The record of who Sift decided somebody is. Here because the service writes these receipts and
#: the queue reads them back, and the queue imports this module.
IDENTIFIED_QUEUE = "identified"

#: The one-time reconcile of names a question put on a file: its own name because this record is
#: final, unlike `IDENTIFIED_QUEUE`'s. `jobs.AskedOnlyRecords` answers for it.
ASKED_ONLY_QUEUE = "asked-only"

#: What a run of the starters work (`FACE_STARTERS`) is recorded under in History;
#: `jobs.StarterRecords` takes one back.
STARTERS_QUEUE = "starters"

#: A name a pass filed that somebody took off because the face is not hers: the Disagreements tab's
#: queue, which takes it back (`DisagreementsQueue.reverse`).
DISAGREEMENTS_QUEUE = "disagreements"

#: A stash-box asking whether a file's one face is somebody. Final: a question is taken back by
#: answering it; `service_box.BoxQuestionRecords` answers for it.
BOX_QUESTIONS_QUEUE = "box-questions"

#: A held entry of facial fingerprints given to a person or made into one
#: (`FingerprintsMixin.recognize_from_fingerprints`); `service_fingerprints.FingerprintRecords` takes one back.
FINGERPRINTS_QUEUE = "fingerprints"

#: A waiting entry of facial fingerprints somebody removed (`WaitingMixin.remove_waiting`). Final:
#: what it held came from a file or a folder, which can be imported again.
FINGERPRINTS_REMOVED_QUEUE = "fingerprints-removed"
