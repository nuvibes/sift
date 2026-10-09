/*
 * What somebody picked LAST, per kind, put first the next time a picker opens; the rest is
 * alphabetical. Recency, on the ACCOUNT; the id is kept and the name read live. Capped at the tail.
 */
import { api } from '$lib/api/client';
import { interfaceState, rereadInterfaceState } from '$lib/shell/interface-state.svelte';

/** The stored key is built from it: a typo would be an empty record or a 400. */
export type FrequentKind = 'collection' | 'photo_set' | 'person' | 'site' | 'tag' | 'song';

const FREQUENT_KINDS: readonly FrequentKind[] = [
	'collection',
	'photo_set',
	'person',
	'site',
	'tag',
	'song'
];

export const FREQUENT_KEPT = 50;

/** How many recent picks a picker draws in front of the list; cut once, in `remembered`. */
export const RECENT_SHOWN = 5;

/** Past the size of most lists, so a ceiling is not the common case. */
export const PICK_PAGE = 60;

export interface Named {
	id: string;
	name: string;
}

/** One remembered pick; the LIST order is the recency. */
interface Picked extends Named {
	used: number;
}

function keyFor(kind: FrequentKind): string {
	return `frequent.${kind}`;
}

/**
 * `$state` so a list drawn from a `$derived` re-orders on a pick; `recallPicks` is awaited where a
 * picker opens, because a lazy read inside a `$derived` would be a state write Svelte refuses.
 */
const picks = $state<Record<string, Picked[]>>({});

/** The one request, shared: every kind is in one document, so one GET however many pickers. */
let reading: Promise<void> | null = null;

function parse(raw: string | undefined): Picked[] {
	if (raw === undefined || raw === '') return [];
	try {
		const read: unknown = JSON.parse(raw);
		if (!Array.isArray(read)) return [];
		const kept: Picked[] = [];
		for (const one of read) {
			if (one === null || typeof one !== 'object' || Array.isArray(one)) continue;
			const entry = one as Record<string, unknown>;
			const id = entry.id;
			const name = entry.name ?? '';
			const used = entry.used ?? 1;
			if (typeof id !== 'string' || id === '') continue;
			if (typeof name !== 'string') continue;
			if (typeof used !== 'number' || !Number.isFinite(used) || used < 1) continue;
			kept.push({ id, name, used: Math.floor(used) });
		}
		return kept;
	} catch {
		// Not JSON: treated as never written.
		return [];
	}
}

/** Read once for every kind; a second call shares the promise. A failure is not worth a message. */
export function recallPicks(): Promise<void> {
	if (reading) return reading;
	reading = (async () => {
		let state: Record<string, string> = {};
		try {
			state = await interfaceState();
		} catch {
			for (const kind of FREQUENT_KINDS) picks[kind] ??= [];
			return;
		}
		for (const kind of FREQUENT_KINDS) {
			// Kept in stored order, which IS the recency.
			if (picks[kind] === undefined) picks[kind] = parse(state[keyFor(kind)]);
		}
	})();
	return reading;
}

/** One more pick, put at the HEAD and written through; the cap drops the oldest. */
export function noteUse(kind: FrequentKind, choice: Named): void {
	const held = picks[kind] ?? [];
	const before = held.find((one) => one.id === choice.id);
	const next: Picked = {
		id: choice.id,
		// An empty name never overwrites one already held.
		name: choice.name || (before?.name ?? ''),
		used: (before?.used ?? 0) + 1
	};
	const kept = [next, ...held.filter((one) => one.id !== choice.id)].slice(0, FREQUENT_KEPT);
	picks[kind] = kept;

	void api
		.put('/settings/interface', { body: { state: { [keyFor(kind)]: JSON.stringify(kept) } } })
		.catch(() => {
			// Remembered here, not there; the next pick tries again.
		});
}

/**
 * The `RECENT_SHOWN` most recent picks, newest first. READ AT A MOMENT, inside an `untrack`: a list
 * that re-ranks under the pointer sends the second click to the wrong row.
 */
export function remembered(kind: FrequentKind): readonly Named[] {
	return (picks[kind] ?? []).slice(0, RECENT_SHOWN);
}

/** Forget one kind here AND on the account; removed, not emptied, so the next read refills it. */
export function forgetUse(kind: FrequentKind): void {
	delete picks[kind];
	reading = null;
	rereadInterfaceState();
	void api
		.put('/settings/interface', { body: { state: { [keyFor(kind)]: null } } })
		.catch(() => {});
}
