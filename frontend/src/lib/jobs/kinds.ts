// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * THE LIST'S TYPE CHOICE: which kinds of task it offers. A chosen kind's tallies are the server's
 * (`tallies` on its page), never added up here.
 *
 * The choices are the kinds the queue holds, each by the name its handler declares (`names` on the
 * page), so a kind is offered exactly while there is a row of it to show, and the one chosen stays
 * offered after its last row goes rather than the control forgetting what it is showing.
 *
 * ONLY THE KINDS THE SERVER NAMES. A kind an older release left in the table has no handler and so
 * no name; offered by its stored id it would read `face_asked_only` among the words. Every such kind is
 * ONE choice, "Older tasks" (`older` on the page, asked as `?older=true`), after the named ones. A
 * chosen kind the page stops naming (its last row went) keeps its place under the name an earlier
 * page gave it (`remembered`), never under its id.
 */
import type { JobsPage } from './family';
import { OLDER_KINDS } from './queue.svelte';

interface KindChoice {
	value: string;
	label: string;
}

/** Every choice of the Type list after `everything`, which the caller places first. */
export function kindChoices(
	page: JobsPage | null,
	chosen: string | null,
	older: string,
	remembered: Readonly<Record<string, string>> = {}
): KindChoice[] {
	const names = { ...(page?.names ?? {}) };
	if (chosen !== null && chosen !== OLDER_KINDS && !(chosen in names) && chosen in remembered) {
		names[chosen] = remembered[chosen];
	}
	const named = Object.keys(names)
		.map((kind) => ({ value: kind, label: names[kind] }))
		.sort((a, b) => a.label.localeCompare(b.label));
	const anyOlder = (page?.older ?? []).length > 0 || chosen === OLDER_KINDS;
	return [...named, ...(anyOlder ? [{ value: OLDER_KINDS, label: older }] : [])];
}
