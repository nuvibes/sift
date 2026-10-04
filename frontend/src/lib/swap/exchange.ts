// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What a guest picked in swap mode before it pasted an Exchange token, kept for the session it
 * joined.
 *
 * In an exchange the guest chooses what it sends beside the code, on the Swap page. Somebody who
 * picked People and files in swap mode and then pasted the token in the drawer meant those picks:
 * leaving the mode takes them away (`swapMode.leave`), so they are put down here first, under the
 * session's id, and the page takes them up once, as the pickers' starting picks. Nothing leaves
 * this device until They match is pressed with them.
 *
 * In this tab's session storage, as the mode itself is, and taken (read and removed) on the first
 * read: a reload of the code step starts from what the pickers hold then, never from the drawer's
 * picks a second time.
 */

import type { components } from '$lib/api/schema';
import type { SwapKind } from './mode.svelte';

/** One pick, in the server's own shape for a chosen thing, narrowed to what swap mode picks and
 *  named as the drawer named it. */
export type ExchangePick = Omit<components['schemas']['Chosen'], 'kind' | 'name'> & {
	kind: SwapKind;
	name: string;
};

const KEY = 'sift.swap-exchange-picks';

/** What a pick can be: swap mode's kinds. */
const KINDS: ReadonlySet<string> = new Set<SwapKind>([
	'asset',
	'person',
	'site',
	'tag',
	'collection',
	'photo_set',
	'song'
]);

/** Put the picks down for the session just joined. */
export function keepExchangePicks(sessionId: string, picks: readonly ExchangePick[]): void {
	try {
		sessionStorage.setItem(KEY, JSON.stringify({ session: sessionId, picks }));
	} catch {
		// Storage refused (a private window, a full quota): the pickers start empty.
	}
}

/** Take up the picks kept for this session, once; nothing for any other session. */
export function takeExchangePicks(sessionId: string): ExchangePick[] {
	let raw: string | null;
	try {
		raw = sessionStorage.getItem(KEY);
	} catch {
		return [];
	}
	if (!raw) return [];
	try {
		const kept = JSON.parse(raw) as { session?: unknown; picks?: unknown };
		if (kept.session !== sessionId || !Array.isArray(kept.picks)) return [];
		sessionStorage.removeItem(KEY);
		// Storage is the page's to read but anybody's to write: every pick is checked for its shape.
		return kept.picks.filter(
			(one): one is ExchangePick =>
				typeof one === 'object' &&
				one !== null &&
				KINDS.has(one.kind) &&
				typeof one.id === 'string' &&
				typeof one.name === 'string'
		);
	} catch {
		return [];
	}
}
