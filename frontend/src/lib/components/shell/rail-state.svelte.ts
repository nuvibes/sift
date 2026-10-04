/* How the rail is arranged, and where that arrangement is kept.
 *
 * ## The arrangement follows the ACCOUNT; the width follows the BROWSER
 *
 * Those are two different kinds of fact and they are stored in two different places.
 *
 * Where somebody put their rows, and which rows they put away, are decisions about how they want
 * Sift laid out. They are theirs, and they should be the same wherever they sign in: arranging
 * the rail on the desktop and finding it unarranged on the laptop is the arrangement not having
 * been remembered, as far as anybody using it is concerned.
 *
 * Whether the rail is showing labels is not that. It is a fact about the window in front of them:
 * the same account at a laptop and at a wide monitor wants two different answers and the account
 * can only hold one. It is also the one that is changed by pressing a button rather than by
 * deciding something. So it stays in the browser, where the tile size already is.
 *
 * ## The browser's copy is a CACHE, not the truth
 *
 * The account's copy arrives from the server a moment after the page does. Rather than draw the
 * default and jump, the last arrangement this browser saw is kept locally and drawn immediately,
 * and the server's answer replaces it when it lands, which on a machine somebody actually uses
 * is the same arrangement, so nothing moves.
 *
 * The cache records WHICH account it belongs to. Two people sharing a machine must not see each
 * other's rail even for one frame, so a cache belonging to somebody else is thrown away the moment
 * we learn who is signed in, rather than left up until the server replies.
 *
 * ## What is stored
 *
 * Absent means the default in every case, so an account that has never arranged anything gets what
 * Sift ships with, and keeps getting it as the shipped arrangement changes in later versions.
 * Anything unreadable reads as absent rather than as an error.
 *
 * Reading and writing go through `remembered.svelte`, which is where the "storage may refuse"
 * handling lives, once for every remembered preference.
 */

/* The file is `rail-state`, not `rail`, and that is not a preference.
 *
 * `Rail.svelte` already has a test at `Rail.svelte.test.ts`, and a store called `rail.svelte.ts`
 * would want `rail.svelte.test.ts`: the same path with one letter in a different case. On Linux
 * those are two files; on macOS or Windows they are ONE, and
 * whichever loses is silently absent with the suite still green. The hygiene gate refuses the pair
 * outright, for exactly that reason.
 */

import { api } from '$lib/api/client';
import { settingChanges } from '$lib/library/changes.svelte';
import { clearStored, readStored, writeStored } from '$lib/shell/remembered.svelte';

import { DEFAULT_RAIL_ORDER, navItem, RAIL_DIVIDER } from './nav';
import type { components } from '$lib/api/schema';

/** The three places a row can be dropped into rather than beside something. */
export type RailRegion = 'above' | 'middle' | 'below';

const COLLAPSED_KEY = 'sift.rail.collapsed';
const ORDER_KEY = 'sift.rail.order';
const HIDDEN_KEY = 'sift.rail.hidden';
/** Whose cached arrangement is in this browser. See the note above about a shared machine. */
const ACCOUNT_KEY = 'sift.rail.account';

/* What the account's copy is called on the server. The same two names the cache uses, so there is
   one vocabulary rather than a mapping in the middle to get wrong. */
const REMOTE_ORDER = 'rail.order';
const REMOTE_HIDDEN = 'rail.hidden';

/* The width at which there is no room for labels whatever the button says.
 *
 * The same band the rail's own stylesheet uses, written here as well because two things need the
 * answer and only one of them is CSS. Kept as one string so the pair cannot drift into disagreeing
 * about where the band starts.
 */
const NO_ROOM_FOR_LABELS = '(min-width: 768px) and (max-width: 1023px)';

function read(key: string): string | null {
	return readStored(key);
}

function write(key: string, value: string | null): void {
	if (value === null) clearStored(key);
	else writeStored(key, value);
}

/** A stored list, from either copy: comma separated, blanks dropped. */
function listed(value: string | null): string[] {
	return (value ?? '')
		.split(',')
		.map((part) => part.trim())
		.filter((part) => part.length > 0);
}

function readList(key: string): string[] {
	return listed(read(key));
}

/**
 * The arrangement to actually draw, given whatever was found in storage.
 *
 * Storage is old data by definition: it was written by whichever version of Sift the browser last
 * ran, and the list of destinations moves between versions. So this is not a validity check that
 * throws the whole thing away on a surprise: it repairs.
 *
 *   - An id this version does not have is dropped. It is a destination that was removed, and a rail
 *     row pointing at nothing is worse than a missing arrangement.
 *   - An id this version has and the stored order does not is put back, next to the neighbour it
 *     ships beside. An update that adds a destination must not leave it invisible to everyone who
 *     ever touched their rail, and appending it to the end would put a new way of looking at the
 *     library underneath Profile.
 *   - The rule appears exactly once. Absent it goes back where it ships; repeated, the extras go.
 *   - A repeated id is kept once, at its first position.
 *   - An id that was RENAMED is followed rather than dropped. Dropping it would be repair doing
 *     the wrong thing well: the destination is still here, so the row would be put back at its
 *     shipped position and somebody who had dragged it somewhere would find it moved.
 */

/**
 * Rail ids that changed their word, and what they are called now. `platforms` became `sites`; a
 * stored arrangement is written by whichever version last ran, so older rails hold the old id.
 */
const RENAMED: Readonly<Record<string, string>> = {
	platforms: 'sites'
};

function arrange(stored: string[]): string[] {
	const known = new Set(DEFAULT_RAIL_ORDER);
	const seen = new Set<string>();
	const order: string[] = [];

	for (const was of stored) {
		const id = RENAMED[was] ?? was;
		if (!known.has(id) || seen.has(id)) continue;
		seen.add(id);
		order.push(id);
	}

	// Whatever this version has and the stored list did not. Walked in default order so several new
	// destinations at once land in the order they ship in rather than in map order.
	for (const id of DEFAULT_RAIL_ORDER) {
		if (seen.has(id)) continue;

		// Behind the nearest thing it ships after that is actually on this rail. That keeps a new
		// destination beside the ones it belongs with even on a rail that has been rearranged around
		// it; with nothing above it left, it goes to the top, which is where it ships.
		let at = 0;
		for (let step = DEFAULT_RAIL_ORDER.indexOf(id) - 1; step >= 0; step -= 1) {
			const above = order.indexOf(DEFAULT_RAIL_ORDER[step]);
			if (above !== -1) {
				at = above + 1;
				break;
			}
		}
		order.splice(at, 0, id);
		seen.add(id);
	}

	return order;
}

/** Everything that can be put away. Settings cannot: it is where putting things back happens. */
function hideable(id: string): boolean {
	const item = navItem(id);
	return item !== undefined && !item.fixed;
}

class RailState {
	/** True when the rail is showing icons alone. */
	collapsed = $state(read(COLLAPSED_KEY) === '1');

	/** The rows, in the order they are drawn. Contains the rule as one of its entries. */
	order = $state<string[]>(arrange(readList(ORDER_KEY)));

	/** The rows that have been put away. Still real destinations; just not on the rail. */
	hidden = $state<string[]>(readList(HIDDEN_KEY).filter(hideable));

	/*
	 * Whether the rail is being rearranged right now.
	 *
	 * Not stored, and that is the point of having a mode at all. Dragging is off until somebody
	 * says otherwise, so a slightly slow click on a nav row is a click on a nav row: picking one
	 * up by accident on the way to a screen is the failure this mode exists to prevent. It ends
	 * when they say so, and it ends by itself when the page changes underneath it.
	 */
	editing = $state(false);

	/* Whether the WINDOW is forcing icons, regardless of the button.
	 *
	 * The stylesheet knows this too. It matters here because the tooltips
	 * only exist while the labels are gone, and the labels go for two different reasons: somebody
	 * pressed the button, or the window has no room. Reading only the button would leave the narrow
	 * band with icons that are unnamed on screen.
	 */
	narrow = $state(false);

	/* Whether the account's own arrangement has arrived yet.
	 *
	 * Until it has, what is on screen is this browser's cached copy, which is a good guess and still
	 * a guess. Saving before it lands would write that guess over whatever the account really has,
	 * which is how an arrangement made on one machine gets wiped by opening Sift on another.
	 */
	#hydrated = false;

	/* Whether somebody arranged something before the account's copy arrived.
	 *
	 * Vanishingly rare: the rail is not drawn until the session is known, and hydrating starts at
	 * the same moment. But the answer has to be decided rather than raced. A change somebody just
	 * made is newer than anything the server is about to say, so it wins and is saved.
	 */
	#arrangedEarly = false;

	constructor() {
		// Server rendering is off for this application, so `matchMedia` is here. The guard is for the
		// unit environment, where jsdom has it but a bare Node context would not.
		if (typeof matchMedia !== 'function') return;
		const query = matchMedia(NO_ROOM_FOR_LABELS);
		this.narrow = query.matches;
		query.addEventListener('change', (event) => (this.narrow = event.matches));
	}

	/**
	 * Take up the arrangement belonging to the account that has just signed in.
	 *
	 * Called once the shell knows who is looking, which is before the rail is drawn at all. Two
	 * things happen, in this order, and the order is the point:
	 *
	 *   1. A cache belonging to somebody else is dropped at once, before any request is made.
	 *      Waiting for the server would leave one person's rail on screen while another person's
	 *      account was being fetched.
	 *   2. The account's own copy replaces what is on screen when it arrives.
	 *
	 * A failure is not worth a message. What is drawn is this browser's last copy of the same
	 * account's arrangement, or what Sift ships with, and both are usable, so the rail works and
	 * this sitting is simply not remembered.
	 */
	async hydrate(accountId: string): Promise<void> {
		const owner = read(ACCOUNT_KEY);
		if (owner !== null && owner !== accountId) {
			this.order = arrange([]);
			this.hidden = [];
			write(ORDER_KEY, null);
			write(HIDDEN_KEY, null);
		}
		write(ACCOUNT_KEY, accountId);

		/* The cache is never carried up: the shipped rail is stored as nothing, so an older copy
		 * sent over "nothing" brings back what somebody undid. Only a change made here first is. */
		let carryUp = false;

		try {
			const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
			const order = answer.state[REMOTE_ORDER] ?? null;
			const hidden = answer.state[REMOTE_HIDDEN] ?? null;

			if (this.#arrangedEarly) {
				carryUp = true;
			} else {
				this.order = arrange(listed(order));
				this.hidden = listed(hidden).filter(hideable);
				write(ORDER_KEY, order);
				write(HIDDEN_KEY, hidden);
			}
		} catch {
			// Kept as it is, and NOT sent up: what the account holds is unknown, and writing over an
			// unknown with a guess is the one outcome worse than not having remembered this sitting.
		} finally {
			this.#hydrated = true;
			this.#arrangedEarly = false;
			if (carryUp) this.#remember();
		}
	}

	/** Stop saving until the next account answers. Whose cache it is stays written down: this runs
	 *  on every page load, and the next sign-in needs it to tell its own cache from another's. */
	release(): void {
		this.#hydrated = false;
		this.#arrangedEarly = false;
	}

	/* Write the arrangement down: this browser's copy at once, the account's a moment later.
	 *
	 * The local write is what makes the next page load on THIS machine draw the right thing
	 * immediately. The remote one is what makes the next machine draw it. A failed remote write is
	 * not put back on screen: the arrangement somebody just made is what they asked for, and
	 * snapping it back because a request failed would be worse than not having remembered it.
	 */
	#remember(): void {
		/* An arrangement that is the shipped one is stored as nothing at all, not as today's list
		 * written out. Otherwise pressing reset would freeze the account on whatever Sift shipped
		 * the day it was pressed, and an update that adds a destination or regroups the rail would
		 * never reach anybody who had ever reset, which is the opposite of what reset means. */
		const arranged = this.order.join(',');
		const order = arranged === DEFAULT_RAIL_ORDER.join(',') ? '' : arranged;
		const hidden = this.hidden.join(',');
		write(ORDER_KEY, order || null);
		write(HIDDEN_KEY, hidden || null);

		if (!this.#hydrated) {
			this.#arrangedEarly = true;
			return;
		}
		this.#writing += 1;
		void api
			.put('/settings/interface', {
				body: { state: { [REMOTE_ORDER]: order || null, [REMOTE_HIDDEN]: hidden || null } }
			})
			.catch(() => {
				// Remembered here, not there. The rail works either way.
			})
			.finally(() => (this.#writing -= 1));
	}

	/* How many of this window's own arrangements are on their way to the account. While one is,
	   a re-read could hand back the arrangement from before it, so none is made. */
	#writing = 0;

	/**
	 * Take up an arrangement made in another window, once this one has hydrated.
	 *
	 * The server says an arrangement moved on the settings bell, to this account only. The account's
	 * copy replaces what is drawn, the same way `hydrate` takes it up, and this browser's copy with
	 * it. Nothing is read while this window's own write is still on its way (see `#writing`), and
	 * that write's own announcement brings the next read.
	 */
	async follow(): Promise<void> {
		if (!this.#hydrated || this.#writing > 0) return;
		try {
			const answer = await api.get<components['schemas']['InterfaceState']>('/settings/interface');
			if (this.#writing > 0) return;
			const order = answer.state[REMOTE_ORDER] ?? null;
			const hidden = answer.state[REMOTE_HIDDEN] ?? null;
			this.order = arrange(listed(order));
			this.hidden = listed(hidden).filter(hideable);
			write(ORDER_KEY, order);
			write(HIDDEN_KEY, hidden);
		} catch {
			// What is drawn stands; the next move says so again.
		}
	}

	/** Whether the labels are off screen, for either reason. What the tooltips key on. */
	get iconsOnly(): boolean {
		return this.collapsed || this.narrow;
	}

	toggle(): void {
		this.collapsed = !this.collapsed;
		write(COLLAPSED_KEY, this.collapsed ? '1' : null);
	}

	/** Whether this destination is on the rail at the moment. */
	shows(id: string): boolean {
		return !this.hidden.includes(id);
	}

	/** Take a row off the rail. Settings is refused: it is the way back. */
	hide(id: string): void {
		if (!hideable(id) || this.hidden.includes(id)) return;
		this.hidden = [...this.hidden, id];
		this.#remember();
	}

	/** Put a row back. */
	show(id: string): void {
		if (!this.hidden.includes(id)) return;
		this.hidden = this.hidden.filter((each) => each !== id);
		this.#remember();
	}

	/* Put the row back into the order at `to`, and remember it.
	 *
	 * `rest` is the order with the row already taken out, and `to` is an index into THAT, which is
	 * the whole reason this is private and the two callers below say where they mean in words
	 * instead. Taking a row out shifts everything after it up one, so an index worked out against
	 * the order as it looks now is off by one for every row that started above the target. That is
	 * not a subtlety anybody should have to hold in their head at a drop handler: dropping a row
	 * into the empty space above the rule would put it BELOW the rule, with arithmetic that looks right.
	 */
	#place(rest: string[], id: string, to: number): void {
		const next = [...rest];
		next.splice(Math.max(0, Math.min(to, next.length)), 0, id);
		if (next.length === this.order.length && next.every((each, at) => each === this.order[at])) {
			return;
		}

		this.order = next;
		this.#remember();
	}

	/**
	 * Put `id` immediately before or after `reference`.
	 *
	 * Said as a relationship rather than as a position, because a position is the thing that goes
	 * wrong. The rule counts as a reference like any other row, which is what lets something be
	 * dragged from one end of the rail to the other: it simply ends up on the far side of it.
	 *
	 * The rule itself cannot be moved: it has no meaning of its own to place, it only marks where
	 * the bottom of the rail begins.
	 */
	/**
	 * Whether placing `id` there would actually change the arrangement.
	 *
	 * "After A" and "before B" are the SAME landing when A and B sit next to each other, and a drag
	 * along the seam between two rows alternates between those two spellings on sub-pixel movement.
	 * Keyed on the words, each flip would look like a new instruction and re-run a move that
	 * reorders nothing, which is not merely wasted work: the reflow it starts blocks the next REAL
	 * move, so the drag stalls and then jumps when one finally gets through.
	 *
	 * Answered by arithmetic on the index rather than by trying it and looking, because trying it is
	 * the expensive half. `rest` is the order without the row being carried, so putting it back at
	 * the index it came from reproduces the order exactly, which makes "would this change
	 * anything" the question of whether the landing index differs from where it already is.
	 */
	wouldMove(id: string, reference: string, side: 'before' | 'after'): boolean {
		if (id === RAIL_DIVIDER || id === reference) return false;
		const from = this.order.indexOf(id);
		if (from === -1) return false;

		const rest = this.order.filter((each) => each !== id);
		const at = rest.indexOf(reference);
		if (at === -1) return false;

		return (side === 'after' ? at + 1 : at) !== from;
	}

	placeBy(id: string, reference: string, side: 'before' | 'after'): void {
		if (id === RAIL_DIVIDER || id === reference) return;
		if (!this.order.includes(id)) return;

		const rest = this.order.filter((each) => each !== id);
		const at = rest.indexOf(reference);
		if (at === -1) return;

		this.#place(rest, id, side === 'after' ? at + 1 : at);
	}

	/** Put `id` at the end of the rail's top or bottom half. What a drop on empty space means. */
	placeInRegion(id: string, region: RailRegion): void {
		if (id === RAIL_DIVIDER || !this.order.includes(id)) return;

		const rest = this.order.filter((each) => each !== id);
		const divider = rest.indexOf(RAIL_DIVIDER);
		/* Three landings, and each one is a different place.
		 *
		 * `above` is the end of the top half, `middle` is the far side of the rule (the FIRST row
		 * below it) and `below` is the end of the bottom half. The middle one is why the band
		 * between the two groups exists at all: without it, "just under the rule" could only be
		 * reached by aiming at whichever row happened to be sitting there.
		 */
		const to =
			divider === -1
				? rest.length
				: region === 'above'
					? divider
					: region === 'middle'
						? divider + 1
						: rest.length;

		this.#place(rest, id, to);
	}

	/**
	 * Move `id` one place up or down, past anything in the way.
	 *
	 * What the keyboard and the menu use. It steps over a hidden row rather than swapping with it:
	 * a row nobody can see is not a position, and pressing "move up" twice to visibly move once is
	 * the control not working.
	 */
	nudge(id: string, direction: -1 | 1): void {
		const drawn = this.order.filter((each) => each === RAIL_DIVIDER || this.shows(each));
		const at = drawn.indexOf(id);
		if (at === -1) return;

		const landing = drawn[at + direction];
		if (landing === undefined) return;

		this.placeBy(id, landing, direction === 1 ? 'after' : 'before');
	}

	/** Back to what Sift ships with: the order it ships in, and nothing put away.
	 *
	 * Stored as nothing rather than as the current default, on the account as well as here. An
	 * account carrying today's default written out would keep it through an update that changes what
	 * Sift ships with, which is the opposite of what pressing reset means.
	 */
	reset(): void {
		this.order = [...DEFAULT_RAIL_ORDER];
		this.hidden = [];
		this.#remember();
	}

	/* What the rail was before somebody started moving things, so it can be put back.
	 *
	 * Taken when arranging begins rather than tracked as a list of undoable steps: what Cancel means
	 * is "as it was", not "one fewer move than I made". Both halves, because putting a row away is
	 * as much a change as moving one and a Cancel that restored the order and kept the row hidden
	 * would be a Cancel that did half of what it says.
	 */
	#before: { order: string[]; hidden: string[] } | null = null;

	/** Remember the arrangement, for a Cancel that has to put it back. */
	hold(): void {
		this.#before = { order: [...this.order], hidden: [...this.hidden] };
	}

	/** Put back what `hold` remembered. Nothing happens if there is nothing held. */
	revert(): void {
		if (this.#before === null) return;
		this.order = [...this.#before.order];
		this.hidden = [...this.#before.hidden];
		this.#before = null;
		this.#remember();
	}

	/** Keep what was done, and forget the way back. */
	settle(): void {
		this.#before = null;
	}
}

export const rail = new RailState();

settingChanges.subscribe(() => void rail.follow());
