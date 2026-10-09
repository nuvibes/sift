// SPDX-License-Identifier: AGPL-3.0-or-later
/* THE LIST'S TYPE CHOICE: which kinds of task it offers. */
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
