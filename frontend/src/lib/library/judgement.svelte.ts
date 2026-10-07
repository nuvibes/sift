/* The heart and the stars: one piece of state, wherever they are drawn.
 *
 * The same two facts appear on a tile, on the asset's own screen and on the player's controls, and
 * they are the same two facts, so a heart filled in one place is filled in all of them,
 * immediately, without anything being reloaded. Three copies of this logic is three chances for one
 * of them to hold a value the others do not, and the person looking at two of them at the same time
 * is the one who finds out.
 *
 * What it does:
 *
 *   - shows the choice immediately, before the server has answered, because a heart that waits for a
 *     round trip feels broken
 *   - keeps whatever the server ended up holding, rather than dropping back to what was passed in
 *   - puts it back and says so if the write was refused, because a heart showing a state the server
 *     never accepted is a lie with nothing on screen to reveal it
 *   - tells every other screen drawing the same file, and listens for the same from them
 */

import { untrack } from 'svelte';

import { api, type ApiPath } from '$lib/api/client';
import { announceRefusal } from '$lib/library/bulk';

import { assetState, onAssetStateChange } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { AssetOpinion } from '$lib/library/changes.svelte';

/** What a file is to you. */
export type Judgement = Pick<AssetOpinion, 'favorite' | 'rating'>;

/** What the caller knows: which file, and what the server last said about it. */
export interface Subject extends Judgement {
	id: string;
}

/** The heart and the stars for one file, and the two ways to change them. */
interface Judged {
	readonly favorite: boolean;
	readonly rating: number | null;
	setFavorite(next: boolean): void;
	setRating(next: number | null): void;
}

/**
 * Call during component setup, handing in a function that reads the current file and its state.
 *
 * A function rather than a value, because the component drawing this is reused: a tile is redrawn
 * for whatever scrolls into it and the player is pointed at the next clip without being rebuilt. A
 * value read once would leave the previous file's stars on the new one.
 */
export function judge(subject: () => Subject): Judged {
	/* What the person just chose, held until it is either confirmed or put back. Two states rather
	 * than one copy kept in step, because a copy carries the last file's answers across. */
	let pending = $state<Judgement | null>(null);

	/*
	 * Which file the held answer belongs to.
	 *
	 * Compared rather than simply reacted to, and that is not tidiness. Reading the subject reads
	 * all three of its fields, so this effect runs again whenever the props move, which they do the
	 * moment a write lands anywhere on the screen. Clearing on every one of those would throw away
	 * the answer a request in flight is about to confirm, and the heart would flick back to what it
	 * was and then forward again.
	 */
	let held = untrack(() => subject().id);

	$effect(() => {
		const now = subject().id;
		untrack(() => {
			if (now === held) return;
			// A different file is a different set of answers.
			held = now;
			pending = null;
		});
	});

	// Somebody changed the same file somewhere else on the screen. This is the whole reason the two
	// can never disagree: they are not kept in step, they are told.
	onAssetStateChange((state) => {
		if (state.asset_id !== subject().id) return;
		pending = { favorite: state.favorite, rating: state.rating };
	});

	/* What is drawn right now. A rating the server left out and a rating it sent as null say the
	   same thing to a row of stars, so the two are one answer from here down. */
	const shown = $derived({
		favorite: (pending ?? subject()).favorite,
		rating: (pending ?? subject()).rating ?? null
	});

	/*
	 * `address` is handed the whole path rather than the last word of one, and that is not style.
	 *
	 * The gate that proves every route has something calling it blanks a `${...}`, so an address
	 * assembled here as `/assets/${id}${path}` would be compared as `/assets/**` against
	 * `/assets/*\/favorite` and these two callers would be reported as dead. Spelling the path out
	 * keeps them visible to the gate.
	 */
	async function write(
		address: (id: string) => ApiPath,
		body: Record<string, unknown>,
		optimistic: Judgement
	) {
		const id = subject().id;
		pending = optimistic;
		try {
			const state = await api.put<AssetOpinion>(address(id), { body });
			// The server's answer rather than what was asked for, and kept rather than dropped: what
			// was passed in is a snapshot from whenever the screen last loaded.
			pending = state;
			// And every other screen on this tab follows immediately. The connection carries the
			// same answer to this account's other browsers a moment later, a different route to the
			// same place, so both ends read one shape rather than two.
			assetState.changed(state);
		} catch (caught) {
			pending = null;
			announceRefusal(caught);
		}
	}

	return {
		get favorite() {
			return shown.favorite;
		},
		get rating() {
			return shown.rating;
		},
		setFavorite(next: boolean) {
			void write(
				(id) => `/assets/${id}/favorite`,
				{ favorite: next },
				{ favorite: next, rating: shown.rating }
			);
		},
		setRating(next: number | null) {
			void write(
				(id) => `/assets/${id}/rating`,
				{ rating: next },
				{ favorite: shown.favorite, rating: next }
			);
		}
	};
}

/** Which file, and what its tally stood at when the screen last read it. */
export interface Counted {
	id: string;
	o_count: number;
}

/** The O counter for one file, and the three things that can be done to it. */
interface Tallied {
	readonly count: number;
	more(): void;
	less(): void;
	clear(): void;
}

/**
 * The O counter's state, beside the heart and the stars because it is the same kind of thing: one
 * number this account holds about one file, shown immediately and settled onto whatever the server
 * ended up with.
 *
 * A HOOK OF ITS OWN and not a fourth field on `judge` above, and the reason is the write rather
 * than tidiness. A heart and a rating are written as VALUES (the control knows what it wants the
 * answer to be and says so), and a tally is written as an ACT: the browser must not send the
 * number it is holding, because two tabs pressing at the same time would both send the same one and
 * one of the presses would vanish. So the optimistic value and the request are worked out
 * differently here, and folding the two together would mean `judge` carrying a branch for whichever
 * kind of write it was making.
 *
 * What it shares is the CHANGE BUS, which is the part that matters: a press here reaches every
 * other screen drawing the file by the same route a heart does, and a press made in another tab
 * arrives here the same way.
 */
export function tally(subject: () => Counted): Tallied {
	/* What the person just did, held until the server confirms it or it is put back. The same two
	 * states `judge` keeps, for the same reason: one copy kept in step carries the previous file's
	 * number across. */
	let pending = $state<number | null>(null);

	/* Which file the held number belongs to. Compared rather than reacted to, exactly as above:
	 * reading the subject reads both its fields, so this effect runs whenever either moves. */
	let held = untrack(() => subject().id);

	$effect(() => {
		const now = subject().id;
		untrack(() => {
			if (now === held) return;
			held = now;
			pending = null;
		});
	});

	// Pressed somewhere else on this screen, or in another browser of this account's.
	onAssetStateChange((state) => {
		if (state.asset_id !== subject().id) return;
		pending = state.o_count;
	});

	const shown = $derived(pending ?? subject().o_count);

	/*
	 * `address` takes the whole path rather than the last word of one, for the reason `judge`
	 * records: the gate that proves every route has a caller blanks a `${...}`, and an address
	 * assembled from a base and a suffix reads to it as `/assets/**`.
	 */
	async function press(change: 'up' | 'down' | 'reset', optimistic: number) {
		const id = subject().id;
		pending = optimistic;
		try {
			const state = await api.put<AssetOpinion>(`/assets/${id}/o-count`, { body: { change } });
			// The server's number, not the guess: the arithmetic happens in SQL, so what comes back
			// is the only thing that has counted every press made anywhere.
			pending = state.o_count;
			assetState.changed(state);
		} catch (caught) {
			pending = null;
			announceRefusal(caught);
		}
	}

	return {
		get count() {
			return shown;
		},
		more() {
			void press('up', shown + 1);
		},
		less() {
			// Never below nothing, here as well as in the statement. The optimistic number is what
			// is on screen for the moment before the answer lands, and a screen showing -1 is a
			// screen telling a lie the server is about to correct.
			void press('down', Math.max(0, shown - 1));
		},
		clear() {
			void press('reset', 0);
		}
	};
}
