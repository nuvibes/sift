// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a guest picked in swap mode before pasting an Exchange token, kept in session storage
 * under the joined session's id and taken once as the Swap page pickers' starting picks. */

import type { components } from '$lib/api/schema';
import type { SwapKind } from './mode.svelte';

/** One pick, in the server's shape for a chosen thing, named as the drawer named it. */
export type ExchangePick = Omit<components['schemas']['Chosen'], 'kind' | 'name'> & {
	kind: SwapKind;
	name: string;
};

const KEY = 'sift.swap-exchange-picks';

const KINDS: ReadonlySet<string> = new Set<SwapKind>([
	'asset',
	'person',
	'site',
	'tag',
	'collection',
	'photo_set',
	'song'
]);

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
