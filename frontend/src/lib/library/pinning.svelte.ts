/*
 * Keeping a thing at the top of its wall.
 *
 * Named things AND files: a Person, a Site, a collection, a tag, a photo set, and a file. What is
 * shared between the two halves is not a call site: it is the ANSWER to "what address is written,
 * with what body, and what does a mixed set mean". Both halves read it here; each keeps the policy
 * that belongs to its own screen, because a wall with a selection and a right-click menu with none
 * are genuinely different questions.
 *
 * ## Why this is one file and not five
 *
 * A pin is the same write on all five walls (one address, one boolean, one answer), and the only
 * thing that differs is the word in the path. The heart beside it is written FOUR different ways
 * across those same walls (two through a store's private helper, one through a store method, one
 * inline on the page), and the four differ in whether they put a failed write back, whether they
 * say anything, and whether they clear the selection afterwards. That drift is invisible until
 * somebody presses the one that behaves differently.
 *
 * So the write lives here once and the walls hand it the one thing only they know: how to put the
 * new value onto the row they are holding. What is shared is the ANSWER to "how does a pin get
 * written and what happens when it does not", not a call site anybody rides along on.
 *
 * ## Why the whole selection gets one target state
 *
 * The same rule the file grid's heart follows, and the entity verb says so too: over a selection
 * where some are pinned and some are not, toggling each into whatever it was not leaves the
 * selection MORE mixed than it started, and there is no sentence that describes what the row did.
 * The verb decides `pinned` once, from what they all share, and every row is written to it.
 */

import { api, type ApiPath } from '$lib/api/client';
import { announceRefusal, overChunks, type BulkWriteDone } from '$lib/library/bulk';
import type { components } from '$lib/api/schema';

/**
 * The six walls a pin exists on, spelled as they are in the address.
 *
 * Written out rather than derived, so a seventh cannot arrive by accident: a username carries no pin
 * for the same reason it carries no heart (a Person is the identity and a username is one of the
 * names they post under), and loops, files, Favorites and Hidden are walls of media rather than of
 * named things.
 */
export type Pinnable = 'people' | 'sites' | 'collections' | 'tags' | 'photo-sets' | 'songs';

/** Put the new value onto whichever row the wall is holding for this id. */
type SettlePin = (id: string, pinned: boolean) => void;

/**
 * Pin or unpin one thing. Answers what the server ended up holding.
 *
 * Optimistic, like every other opinion in Sift: the mark moves immediately and the server's own
 * answer replaces the guess when it lands. A control that waits for a round trip before it changes
 * reads as broken, and the round trip is nearly always a success.
 */
async function setPinned(kind: Pinnable, id: string, pinned: boolean): Promise<boolean> {
	const held = await api.put<components['schemas']['PinView']>(`/${kind}/${id}/pin` as ApiPath, {
		body: { pinned }
	});
	return held.pinned;
}

/**
 * Pin or unpin one FILE. Answers the whole opinion the server ended up holding.
 *
 * The one place that knows a file's pin address and body, so the grid's own actions and any screen
 * drawing files outside the grid cannot come to write it two different ways. Nothing in production
 * draws through this one-file form (a collection's hand-arranged wall takes the shared verbs and
 * their selection-wide pin), and it stays as the one place the address and body are written. It
 * answers the WHOLE opinion rather than just the pin because that is what the route replies with,
 * and it is the same shape this account's other tabs are told: one description, so the tab that
 * pressed the control and the tab that did not are served by one piece of code. See
 * `changes.AssetOpinion`.
 *
 * No toast and no rollback here. Those are the screen's, and the two screens want different ones.
 */
export async function setFilePinned(
	id: string,
	pinned: boolean
): Promise<components['schemas']['AssetOpinion']> {
	return await api.put<components['schemas']['AssetOpinion']>(`/assets/${id}/pin` as ApiPath, {
		body: { pinned }
	});
}

/**
 * Pin or unpin a whole SELECTION of files, in one request per five hundred of them.
 *
 * **Not one request per file.** A hundred and thirty-four pins would be a hundred and thirty-four
 * round trips, each awaited before the next began and each its own write on the server; the work is
 * never the cost. `setFilePinned` above is what one tile uses: a right-click on a single row has
 * nothing to batch and settles on that file's own opinion.
 *
 * Answers what the server did with the selection: how many rows moved, how many it left alone and
 * why. It names no rows, and cannot (see `BulkWriteDone`), so a caller that has to know which
 * files moved re-reads instead of editing tiles in place.
 *
 * A selection larger than one request is split by `overChunks`, which is also what turns a chunk
 * that failed outright into skipped-with-a-reason rather than a throw over writes that did land.
 */
export async function setFilesPinned(
	ids: readonly string[],
	pinned: boolean
): Promise<BulkWriteDone> {
	return await overChunks(ids, (chunk) =>
		api.post<BulkWriteDone>('/assets/pin', { body: { asset_ids: chunk, pinned } })
	);
}

/**
 * Pin or unpin a whole selection, putting every row back if any of them fails.
 *
 * **All or nothing on the screen, and deliberately.** A partial failure leaves some rows written
 * and some not, and a wall showing three of five pinned after one press is a wall nobody can reason
 * about. So what is on screen goes back to what it was and the message says so. The rows that DID
 * land on the server stay landed; the next read shows them, which is the honest state rather than a
 * second write undoing somebody's data because a third one timed out.
 *
 * Returns whether all of them landed, so a caller can clear the selection only when they did.
 */
export async function pinAll(
	kind: Pinnable,
	ids: string[],
	pinned: boolean,
	settle: SettlePin
): Promise<boolean> {
	for (const id of ids) settle(id, pinned);
	try {
		for (const id of ids) settle(id, await setPinned(kind, id, pinned));
		return true;
	} catch (caught) {
		for (const id of ids) settle(id, !pinned);
		announceRefusal(caught);
		return false;
	}
}

/**
 * What the verb should offer over a set of rows: `true` only when every one of them is pinned.
 *
 * Mixed reads as NOT pinned, so the verb says "Pin" and pins the lot. The other reading (unpin
 * because one of them is pinned) takes away something somebody set, from a press whose label
 * said nothing about it.
 */
export function allPinned(pinnedOf: (boolean | undefined)[]): boolean {
	return pinnedOf.length > 0 && pinnedOf.every(Boolean);
}
