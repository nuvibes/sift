/*
 * What goes in an entity page's one door, and the one row no page declares for itself.
 *
 * A rule small enough to inline and worth keeping out here anyway: it is the only thing standing
 * between "this page can be deleted" and "this page offers Delete", and it has two conditions that
 * are easy to get subtly wrong. Out here it can be read, and broken on purpose to prove a test
 * notices.
 */

import type { Verb } from '$lib/components/common/verbs';

/**
 * The page's verbs, with Delete on the end where one is offered.
 *
 * **Appended rather than declared by each page.** Every entity page wants it, worded and coloured
 * the same way, and one that forgot would leave the thing it is about with no way to be deleted at
 * all.
 *
 * **Left out while the form is up**, keeping the rule the row already had: a screen offering Save
 * and Delete side by side is a screen where the wrong one gets pressed. The rest of the verbs stay,
 * because taking them away makes the row change width under a pointer already reaching for it.
 */
export function withDelete(
	options: readonly Verb[] | undefined,
	offered: boolean,
	run: () => void
): Verb[] {
	const listed = [...(options ?? [])];
	if (offered) {
		listed.push({ id: 'delete', label: 'Delete', icon: 'delete', destructive: true, run });
	}
	return listed;
}
