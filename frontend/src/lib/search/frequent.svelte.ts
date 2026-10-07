/*
 * What somebody picked LAST, so the last few come first the next time a picker opens.
 *
 * A library can have a handful of collections and a thousand people, and the same six of each are
 * what a given person files things under all week. An alphabetical list of a thousand is a list
 * where the answer is always somewhere in the middle, and the flyout on the file menu (which
 * shows a handful of rows at a time) is where that costs the most.
 *
 * So this records picks, per kind, newest first, and `remembered` hands back the newest
 * `RECENT_SHOWN` of them. Everything the picker draws under those is alphabetical: a PAGE from the
 * server in `PickMenu`, and the same order applied by `$lib/search/name-order` in `PickDialog`.
 *
 * ## Recency, not a count
 *
 * A count would leave the collection picked a moment ago under one picked five times last month, so
 * a pick would visibly NOT move it to the top; and drawing every remembered row in count order in
 * front of the page would make the list read as out of order well past the first five. The count is
 * still written beside each pick (`used`), because the account's copy is validated in that shape
 * and an older client reads it; nothing orders by it.
 *
 * ## It follows the ACCOUNT, not the browser
 *
 * It is an arrangement of one person's own screen, and so is the rail, which lives on the account
 * for exactly that reason: an arrangement that does not follow the person is one they made once and
 * then lost by opening Sift on the other machine. So the record is the account's own interface
 * state (the same table, route and shape the rail uses).
 *
 * ## The ID is what is remembered, and the name is read live
 *
 * A picker asks the server for a page; the page is alphabetical and filtered by what is typed, so a
 * remembered row is very often not in it. Such a row is drawn under the name the thing has NOW,
 * asked for all the remembered rows of a kind in one request as the picker opens
 * (`$lib/entity/names-now`). The record still carries a name beside each id, because the account's copy
 * is validated in that shape and an older client reads it; nothing draws it. Drawn from that copy,
 * a tag renamed on its own page would go on being offered under its old name until picked again.
 *
 * ## Why it is capped
 *
 * Without a cap the record grows for as long as the library does: every collection ever picked,
 * for ever, in a value that is read and rewritten on every pick. Fifty is far more than the five a
 * picker draws, and the fifty-first row is by definition the one picked longest ago. The server
 * refuses a longer list than this, for the same reason. In recency order the new pick is the HEAD
 * and the cap drops the tail, so the id just picked can never be the one dropped.
 */
import { api } from '$lib/api/client';
import { interfaceState, rereadInterfaceState } from '$lib/shell/interface-state.svelte';

/**
 * The kinds of thing a picker chooses from, each counted separately.
 *
 * A union rather than a free string: the key these are stored under is built from it, so a typo
 * would silently be a fresh, empty record that never fills up and never says why. And the server
 * refuses a key it has not registered, so it would be a 400 nothing on screen could explain.
 *
 * `tag` is one of them: a tag is chosen out of a list exactly as a collection is, and a picker of
 * its own (a different order, a different cap and a different memory) is what the single
 * mechanism exists to avoid.
 */
export type FrequentKind = 'collection' | 'photo_set' | 'person' | 'site' | 'tag' | 'song';

/** Every one of them, for the one read that fills every kind in one go. */
const FREQUENT_KINDS: readonly FrequentKind[] = [
	'collection',
	'photo_set',
	'person',
	'site',
	'tag',
	'song'
];

/**
 * How many ids one kind may remember. The server refuses a longer list, with the same figure.
 *
 * More than are drawn, deliberately: the record is the account's, and the number SHOWN is a
 * presentation choice that may move without every account having to earn its history again.
 */
export const FREQUENT_KEPT = 50;

/**
 * How many of the most recent picks a picker draws in front of the alphabetical list: the top five.
 * One number for every picker of every kind: `PickMenu` and `PickDialog` both take what
 * `remembered` hands them and never cut it again, so moving this moves all of them together.
 */
export const RECENT_SHOWN = 5;

/**
 * How many rows one page of a picker holds.
 *
 * Here rather than in each store because every kind pages the same way, and one number moving moves
 * every picker in the application together.
 *
 * Sixty, because it is past the size of the lists this application actually holds (the walls page
 * at sixty for the same reason), so the common library is one page and the ceiling line is drawn
 * for the libraries that genuinely have more. The row that says what did not fit is reached by
 * typing, which is what it says to do, not by scrolling to it; a small page would leave a library
 * with forty tags with fifteen of them behind a box somebody has to guess the first letters of. A
 * ceiling must not be the ordinary case.
 */
export const PICK_PAGE = 60;

/** Anything with a name, which is all the ordering needs to know about a choice. */
export interface Named {
	id: string;
	name: string;
}

/** One remembered pick, as it is stored. The LIST order is the recency, newest first; `used` is
 *  how many times, kept for the stored shape and not read for ordering. */
interface Picked extends Named {
	used: number;
}

/** What the account's own copy is called on the server, per kind. Registered there too. */
function keyFor(kind: FrequentKind): string {
	return `frequent.${kind}`;
}

/**
 * What is remembered, by kind, once it has been read.
 *
 * `$state` so that a list drawn from a `$derived` re-orders the moment something is picked. A kind
 * that has never been read is simply absent, and `remembered` then answers with nothing, which is
 * why `recallPicks` is awaited where a picker opens rather than read lazily from the ordering
 * itself. A lazy read would be a state write inside a `$derived`, which Svelte refuses outright.
 */
const picks = $state<Record<string, Picked[]>>({});

/**
 * The one request, shared.
 *
 * Every key lives in ONE document (the account's interface state), so five pickers opening in
 * one session is one GET and not five. Held as the promise rather than as a flag so two flyouts
 * opened in the same tick both wait on the same answer instead of racing two requests.
 */
let reading: Promise<void> | null = null;

/** What came back, or nothing. Every value is checked rather than trusted: this is text another
 *  version of Sift wrote, and a count that is not a positive number would sort a list into an
 *  order nobody could explain. */
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
		// Not JSON at all. Treated as never written, which is the same honest fallback as the
		// request having failed.
		return [];
	}
}

/**
 * Read what this account remembers, once, for every kind at the same time.
 *
 * Idempotent, and awaited where a picker opens, which is "this list is about to be shown". A
 * second call hands back the same promise, so what a pick wrote during this session is never
 * overwritten by what the server said before it.
 *
 * A failure is not worth a message. The picker still works: the page from the server is the whole
 * list, alphabetical, and this sitting is simply not remembered.
 */
export function recallPicks(): Promise<void> {
	if (reading) return reading;
	reading = (async () => {
		let state: Record<string, string> = {};
		try {
			// The one read of the account's document, shared with every other key on it.
			state = await interfaceState();
		} catch {
			// Nothing remembered this sitting, and nothing written over either.
			for (const kind of FREQUENT_KINDS) picks[kind] ??= [];
			return;
		}
		for (const kind of FREQUENT_KINDS) {
			// Kept in the order it was stored in, which IS the recency. A record written in count
			// order is read as recency once: the first pick after it puts the truly newest at the
			// head.
			if (picks[kind] === undefined) picks[kind] = parse(state[keyFor(kind)]);
		}
	})();
	return reading;
}

/**
 * One more pick of this thing, name and all, which puts it at the HEAD of the record.
 *
 * Written through immediately: the next visit is the point, and it may be on another machine. The
 * cap drops from the tail, which is the pick made longest ago. So the one just made can never be
 * the one lost, which a cap by count would have to protect by hand.
 */
export function noteUse(kind: FrequentKind, choice: Named): void {
	const held = picks[kind] ?? [];
	const before = held.find((one) => one.id === choice.id);
	const next: Picked = {
		id: choice.id,
		// The freshest name wins, and an empty one never overwrites a name already held: a caller
		// that only knows the id (a row stored with no name) must not blank
		// what somebody else's pick already wrote in.
		name: choice.name || (before?.name ?? ''),
		used: (before?.used ?? 0) + 1
	};
	const kept = [next, ...held.filter((one) => one.id !== choice.id)].slice(0, FREQUENT_KEPT);
	picks[kind] = kept;

	void api
		.put('/settings/interface', { body: { state: { [keyFor(kind)]: JSON.stringify(kept) } } })
		.catch(() => {
			// Remembered here, not there. The picker works either way, and the next pick tries again.
		});
}

/**
 * The `RECENT_SHOWN` things this account picked most recently, newest first.
 *
 * Cut HERE, once, so no picker can draw a different number of them. Everything a picker draws BELOW
 * these is alphabetical: nothing a picker shows is ever ordered by how big a thing is (see
 * `$lib/search/name-order`).
 *
 * READ AT A MOMENT, NEVER WATCHED. `picks` is state and `noteUse` writes it on every press, so this
 * is a reactive read, and both callers want a snapshot rather than a live list, because a list
 * that re-ranks itself under the pointer sends the second click to whatever slid into the first
 * one's place. Each takes it inside an `untrack`. Calling this from a tracked `$effect` without one
 * makes that effect re-run on every press, and a bulk sheet could not tick anything.
 */
export function remembered(kind: FrequentKind): readonly Named[] {
	return (picks[kind] ?? []).slice(0, RECENT_SHOWN);
}

/**
 * Forget everything remembered about one kind, in this session AND on the account.
 *
 * The in-memory entry is REMOVED rather than emptied, so the next read takes what the account holds
 * instead of keeping an empty record that nothing would ever refill.
 */
export function forgetUse(kind: FrequentKind): void {
	delete picks[kind];
	reading = null;
	rereadInterfaceState();
	void api
		.put('/settings/interface', { body: { state: { [keyFor(kind)]: null } } })
		.catch(() => {});
}
