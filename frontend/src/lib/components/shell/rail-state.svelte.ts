/* How the rail is arranged, and where that is kept. The arrangement follows the account; whether
 * labels show follows the browser. This browser's copy is a cache, drawn immediately and dropped
 * on sight when it belongs to another account. Absent means the default, so an untouched rail
 * follows what Sift ships. */

// Not `rail`: `rail.svelte.test.ts` would differ from `Rail.svelte.test.ts` only in case.

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
/** Whose cached arrangement is in this browser. */
const ACCOUNT_KEY = 'sift.rail.account';

// The account's copy, under the same names the cache uses.
const REMOTE_ORDER = 'rail.order';
const REMOTE_HIDDEN = 'rail.hidden';

// No room for labels whatever the button says; the same band as the rail's stylesheet.
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

/** Rail ids that changed their word; older stored rails hold the old id. */
const RENAMED: Readonly<Record<string, string>> = {
	platforms: 'sites'
};

/**
 * Repairs a stored arrangement from any version: unknown ids dropped, renamed ids followed,
 * repeats kept once, the rule once, and missing ids put back beside their shipped neighbour.
 */
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

	// Missing ids, walked in default order so several new ones land in shipped order.
	for (const id of DEFAULT_RAIL_ORDER) {
		if (seen.has(id)) continue;

		// Behind the nearest row it ships after, or at the top.
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

	// Not stored: dragging is off until asked, so a slow click is never a pick-up.
	editing = $state(false);

	// The window forcing icons; the tooltips need it as well as the button.
	narrow = $state(false);

	// Until then the screen shows a cache, and saving would write a guess over the account.
	#hydrated = false;

	// A change made before the account's copy arrived is newer, so it wins and is saved.
	#arrangedEarly = false;

	constructor() {
		// For a bare Node context; jsdom and the browser have `matchMedia`.
		if (typeof matchMedia !== 'function') return;
		const query = matchMedia(NO_ROOM_FOR_LABELS);
		this.narrow = query.matches;
		query.addEventListener('change', (event) => (this.narrow = event.matches));
	}

	/** Take up the signed-in account's arrangement; another account's cache goes first. */
	async hydrate(accountId: string): Promise<void> {
		const owner = read(ACCOUNT_KEY);
		if (owner !== null && owner !== accountId) {
			this.order = arrange([]);
			this.hidden = [];
			write(ORDER_KEY, null);
			write(HIDDEN_KEY, null);
		}
		write(ACCOUNT_KEY, accountId);

		// Only an early change is carried up: the cache could undo a reset stored as nothing.
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
			// Not sent up: writing a guess over an unknown is worse than forgetting.
		} finally {
			this.#hydrated = true;
			this.#arrangedEarly = false;
			if (carryUp) this.#remember();
		}
	}

	/** Stop saving until the next account answers; whose cache it is stays written. */
	release(): void {
		this.#hydrated = false;
		this.#arrangedEarly = false;
	}

	// This browser's copy immediately, the account's after; a failed write stays on screen.
	#remember(): void {
		// The shipped rail is stored as nothing, so a reset keeps following later versions.
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

	// While this window's own write is in flight, a re-read could hand back the older one.
	#writing = 0;

	/** Take up an arrangement made in another window, announced on the settings bell. */
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

	// `to` indexes `rest` (the row taken out), so callers say where in words, not an index.
	#place(rest: string[], id: string, to: number): void {
		const next = [...rest];
		next.splice(Math.max(0, Math.min(to, next.length)), 0, id);
		if (next.length === this.order.length && next.every((each, at) => each === this.order[at])) {
			return;
		}

		this.order = next;
		this.#remember();
	}

	/** Whether the move changes anything: a drag on a seam flips between two same landings. */
	wouldMove(id: string, reference: string, side: 'before' | 'after'): boolean {
		if (id === RAIL_DIVIDER || id === reference) return false;
		const from = this.order.indexOf(id);
		if (from === -1) return false;

		const rest = this.order.filter((each) => each !== id);
		const at = rest.indexOf(reference);
		if (at === -1) return false;

		return (side === 'after' ? at + 1 : at) !== from;
	}

	/** Put `id` immediately before or after `reference`; the rule itself never moves. */
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
		// `middle` is the first row below the rule, reachable without aiming at a row.
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

	/** Move `id` one visible place up or down, stepping over hidden rows. */
	nudge(id: string, direction: -1 | 1): void {
		const drawn = this.order.filter((each) => each === RAIL_DIVIDER || this.shows(each));
		const at = drawn.indexOf(id);
		if (at === -1) return;

		const landing = drawn[at + direction];
		if (landing === undefined) return;

		this.placeBy(id, landing, direction === 1 ? 'after' : 'before');
	}

	/** Back to what Sift ships with, stored as nothing so later versions still reach it. */
	reset(): void {
		this.order = [...DEFAULT_RAIL_ORDER];
		this.hidden = [];
		this.#remember();
	}

	// Both halves: Cancel restores the order and the hidden rows.
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
