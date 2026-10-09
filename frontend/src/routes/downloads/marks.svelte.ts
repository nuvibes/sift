// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which Site marks the pack does not have, remembered for the page. */

import { SvelteSet } from 'svelte/reactivity';

const missing = new SvelteSet<string>();

/** The mark's address to draw, or null where the pack is known not to have it. */
export function markOf(address: string | null | undefined): string | null {
	return address && !missing.has(address) ? address : null;
}

/** Remember that the pack has no picture at this address. */
export function markMissing(address: string | null | undefined): void {
	if (address) missing.add(address);
}
