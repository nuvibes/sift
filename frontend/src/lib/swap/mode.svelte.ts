// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Swap mode: what is being picked for a swap, while the person walks the library picking it.
 *
 * One store for the whole window, because the picking happens on every wall (files, People,
 * Sites, tags, Collections, Photo Sets, Music) and the picks outlive a move between them: the point of a
 * mode is that somebody can pick three people here and two files there and build one offer. The
 * drawer lists the picks and starts the swap from them; a tile or a card reads `has` to draw its
 * mark and calls `toggle` instead of opening.
 *
 * Kept in this tab's session storage as well, so a full page load (an address typed into the bar,
 * a reload) comes back in the mode with the same picks. Kept for the admin who made them: it is
 * taken up only when that same account is signed in (`adopt`), and signing out or leaving the mode
 * throws it away. Closing the tab ends it, as it ends everything else in session storage. Nothing
 * leaves this device until Start is pressed and the codes match.
 */

import type { components } from '$lib/api/schema';
import type { Chosen } from '$lib/components/swap/swap';

/** The kinds of thing a swap can offer by picking: every chosen kind but a saved filter. */
export type SwapKind = Exclude<Chosen['kind'], 'filter'>;

/** One pick, in the server's own shape for a chosen thing, narrowed to what a pick can be and
 *  always named: what the drawer calls it. */
type SwapPick = Omit<components['schemas']['Chosen'], 'kind' | 'name'> & {
	kind: SwapKind;
	name: string;
};

/** How many things one swap can be built from: the server's own ceiling (`MAX_CHOSEN`). */
export const MOST_PICKS = 500;

const keyOf = (kind: SwapKind, id: string) => `${kind}:${id}`;

/** Where the mode waits across a full page load, in this tab only. */
export const KEPT_AS = 'sift.swap-mode';

const KINDS: ReadonlySet<string> = new Set<SwapKind>([
	'asset',
	'person',
	'site',
	'tag',
	'collection',
	'photo_set',
	'song'
]);

interface Kept {
	user: string;
	picks: SwapPick[];
}

/** What was kept for this account, or nothing where nothing usable was: storage is the page's to
 *  read but anybody's to write, so every pick is checked for its shape before it is believed. */
function keptFor(user: string): SwapPick[] | null {
	let raw: string | null;
	try {
		raw = sessionStorage.getItem(KEPT_AS);
	} catch {
		return null;
	}
	if (!raw) return null;
	try {
		const kept = JSON.parse(raw) as Partial<Kept>;
		if (kept?.user !== user || !Array.isArray(kept.picks)) return null;
		return kept.picks
			.filter(
				(one): one is SwapPick =>
					typeof one === 'object' &&
					one !== null &&
					KINDS.has(one.kind) &&
					typeof one.id === 'string' &&
					typeof one.name === 'string'
			)
			.map((one) => ({ kind: one.kind, id: one.id, name: one.name }))
			.slice(0, MOST_PICKS);
	} catch {
		return null;
	}
}

function keep(kept: Kept | null): void {
	try {
		if (kept === null) sessionStorage.removeItem(KEPT_AS);
		else sessionStorage.setItem(KEPT_AS, JSON.stringify(kept));
	} catch {
		// Storage refused (a private window, a full quota): the mode still works, for this page.
	}
}

class SwapMode {
	/** Whether the walls pick for a swap rather than open what is pressed. */
	on = $state(false);
	/** What is picked, in the order it was picked. */
	picks = $state<SwapPick[]>([]);

	#keys = $derived(new Set(this.picks.map((one) => keyOf(one.kind, one.id))));
	/** The admin the mode belongs to, once the page knows who is signed in. */
	#user: string | null = null;

	/**
	 * Who is signed in now, said by the shell whenever that changes. For the admin whose mode was
	 * kept, it comes back as it was; for anybody else, or nobody, there is no mode and nothing
	 * kept, so a picks list never outlives the account that made it.
	 */
	adopt(user: string | null, admin: boolean): void {
		const who = user && admin ? user : null;
		if (who === this.#user) return;
		this.#user = who;
		const kept = who === null ? null : keptFor(who);
		if (kept === null) {
			this.on = false;
			this.picks = [];
			keep(null);
			return;
		}
		this.on = true;
		this.picks = kept;
	}

	enter(): void {
		this.on = true;
		this.#keep();
	}

	/** Leave the mode. The picks go with it: a mode left is an offer abandoned. */
	leave(): void {
		this.on = false;
		this.picks = [];
		keep(null);
	}

	has(kind: SwapKind, id: string): boolean {
		return this.#keys.has(keyOf(kind, id));
	}

	/** Pick it, or put it back. False when the pick would pass the ceiling and was not made. */
	toggle(pick: SwapPick): boolean {
		if (this.has(pick.kind, pick.id)) {
			this.drop(pick.kind, pick.id);
			return true;
		}
		if (this.picks.length >= MOST_PICKS) return false;
		this.picks = [...this.picks, pick];
		this.#keep();
		return true;
	}

	drop(kind: SwapKind, id: string): void {
		this.picks = this.picks.filter((one) => !(one.kind === kind && one.id === id));
		this.#keep();
	}

	/** Write the mode down for a full page load, while it is on and somebody it belongs to is known. */
	#keep(): void {
		if (this.#user === null || !this.on) return;
		keep({ user: this.#user, picks: this.picks.map(({ kind, id, name }) => ({ kind, id, name })) });
	}

	/** What the server is sent to build the offer from. */
	chosen(): Chosen[] {
		return this.picks.map((one) => ({ kind: one.kind, id: one.id }));
	}
}

export const swapMode = new SwapMode();
