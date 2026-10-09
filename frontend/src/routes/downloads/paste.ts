// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a paste that lands on the downloads screen means. */

import { lines } from './queue.svelte';

/** Whether a pasted lump of text holds an address at all. */
export function holdsALink(text: string): boolean {
	return lines(text).some((one) => /^https?:\/\//i.test(one));
}

/** Whether the screen may take a paste that landed here, or whether it belongs to a control. */
export function forTheScreen(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) return true;
	return target.closest('input, textarea') === null && !target.isContentEditable;
}

/** The box's new contents: what is in it, and the pasted link on a line of its own under it. */
export function intoBox(already: string, pasted: string): string {
	const kept = already.trim();
	const link = pasted.trim();
	return kept ? `${kept}\n${link}` : link;
}
