// SPDX-License-Identifier: AGPL-3.0-or-later
/* Deleting loops, over a selection or one, in one request for the lot. */

import { counted } from '$lib/entity/entity-counts';
import { api } from '$lib/api/client';
import { announceSkipped, type BulkWriteDone } from '$lib/library/bulk';
import { toasts } from '$lib/shell/toasts.svelte';

/** Names the LOOP: a bare "Delete" beside a video would read as removing the file. */
export function forgetLabel(count: number): string {
	return count === 1 ? 'Delete this loop' : `Delete ${counted(count)} loops`;
}

/** A loop saved as a file is a file too, so the words say no file was touched. */
export function forgottenWords(count: number): string {
	return count === 1
		? 'Loop deleted. No file was touched.'
		: `${counted(count)} loops deleted. No file was touched.`;
}

/**
 * Rows leave the screen on the server's word: a loop somebody else made is skipped, not removed.
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
