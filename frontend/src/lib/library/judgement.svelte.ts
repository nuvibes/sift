/* The heart and the stars: one piece of state, wherever they are drawn. */

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

/** Call during component setup, handing in a function that reads the current file and its state. */
export function judge(subject: () => Subject): Judged {
	/* What the person just chose, held until it is either confirmed or put back. */
	let pending = $state<Judgement | null>(null);

	/* Which file the held answer belongs to. Compared rather than simply reacted to, and that is
	 * not tidiness. */
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

	// Somebody changed the same file somewhere else on the screen.
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

	/* `address` is handed the whole path rather than the last word of one, and that is not
	 * style. */
	async function write(
		address: (id: string) => ApiPath,
		body: Record<string, unknown>,
		optimistic: Judgement
	) {
		const id = subject().id;
		pending = optimistic;
		try {
			const state = await api.put<AssetOpinion>(address(id), { body });
			// The server's answer rather than what was asked for, and kept rather than dropped:
			// what was passed in is a snapshot from whenever the screen last loaded.
			pending = state;
			// And every other screen on this tab follows immediately.
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

/** The O counter's state, beside the heart and the stars because it is the same kind of thing:
 * one number this account holds about one file, shown immediately and settled onto whatever the
 * server ended up with. */
export function tally(subject: () => Counted): Tallied {
	/* What the person just did, held until the server confirms it or it is put back. */
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

	/* `address` takes the whole path rather than the last word of one, for the reason `judge`
	 * records: the gate that proves every route has a caller blanks a `${...}`, and an address
	 * assembled from a base and a suffix reads to it as `/assets/**`. */
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
			// Never below nothing, here as well as in the statement.
			void press('down', Math.max(0, shown - 1));
		},
		clear() {
			void press('reset', 0);
		}
	};
}
