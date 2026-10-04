// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Which Site marks the pack does not have, remembered for the page.
 *
 * Every row from one host draws the same address, and a host the pack does not know answers 404.
 * A browser keeps no failed picture, so each row drawn later would ask again and the console
 * would say so again. Once one row's picture has failed, no row asks for that address again.
 */

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
