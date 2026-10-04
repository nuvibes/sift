// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Deleting loops, over a selection or over one.
 *
 * ## Why this is a module and not a function inside the screen
 *
 * Two of the three things here are decisions rather than plumbing (what the verb is CALLED for a
 * given number of rows, and what is said afterwards), and both can be reached by a test here,
 * where inside a menu row they could not. The screen keeps the part that is genuinely about the
 * screen: handing the verb to the grid.
 *
 * ## Why it is one request and not one per mark
 *
 * `DELETE /loops/{loop_id}` exists and is right for a single row. A selection is a different act:
 * one request per mark would mean a hundred round trips each announcing to every screen that the
 * library had changed. `POST /loops/forget` answers the same `BulkWriteDone` every other bulk write
 * in Sift answers, so what was left out is said by the shared sentence rather than by a second one
 * written here.
 */

import { counted } from '$lib/entity/entity-counts';
import { api } from '$lib/api/client';
import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
import { toasts } from '$lib/shell/toasts.svelte';

/**
 * What the verb says it will do, for this many rows.
 *
 * Delete, because the loop stops existing, and it names the LOOP, which is the whole of the wording
 * problem on this wall: the shared file verb is hidden here because it removes the video, and a
 * bare "Delete" beside a picture of a video would read as removing that.
 */
export function forgetLabel(count: number): string {
	return count === 1 ? 'Delete this loop' : `Delete ${counted(count)} loops`;
}

/**
 * What is said once they are gone.
 *
 * "No file was touched" is not reassurance, it is the fact somebody needs in order to press the verb
 * again: a loop made by Save as Loop IS a file, so "delete the loop" and "delete the file" are two
 * different acts on this wall and only one of them is on offer.
 */
export function forgottenWords(count: number): string {
	return count === 1
		? 'Loop deleted. No file was touched.'
		: `${counted(count)} loops deleted. No file was touched.`;
}

/**
 * Delete a selection of loops, in one request, and say what happened.
 *
 * The rows are taken off the screen on the SERVER's word rather than optimistically: a mark somebody
 * else made is skipped rather than removed, so dropping its tile first would leave the wall showing
 * one fewer row than the library holds until something re-read it.
 *
 * `announceSkipped` carries the exception, so this writes no sentence of its own for it. That is a
 * dozen screens' worth of wording settled in one place, including the Unlock button none of them
 * would remember to offer.
 */
export async function forgetLoops(ids: string[], gone: (id: string) => void): Promise<void> {
	try {
		const done = await api.post<BulkWriteDone>('/loops/forget', { body: { loop_ids: ids } });
		for (const id of ids) gone(id);
		if (done.changed > 0) toasts.show(forgottenWords(done.changed), { tone: 'success' });
		announceSkipped(done, 'loop');
	} catch {
		toasts.show("Those loops couldn't be deleted", { tone: 'error' });
	}
}
