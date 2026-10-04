/*
 * The orders one Theater cell can be put in, and why they are a subset.
 *
 * A cell draws from a query of its own, so Sort on this screen acts on the CELL rather than on the
 * screen. See `+page.svelte`. What it may offer is therefore the file wall's own vocabulary, less
 * the one order that cannot mean anything in a cell:
 *
 *   - **Closest match** needs words to be close TO. The file wall offers it only where the query has
 *     some, and a cell's query is usually a kept search or nothing at all; an order that silently
 *     does nothing is worse than one that is not on the list.
 *
 * **RANDOM IS ON THE LIST.** A shuffle is a draw held by a SEED, and a cell's saved shape carries a
 * sort and not a seed, which does not matter: what has to hold a seed steady is the RUN, not
 * the saved shape: a cell fills its run a page at a time while it is on screen, so a seed held in
 * memory for the life of that run is exactly as steady as the address bar's seed is for the life of
 * a visit, and neither survives being put away and taken out again, which is what asking to be
 * shuffled means both times. `Cell.seed` is minted beside the order and is deliberately not saved;
 * see `cell.svelte.ts`.
 *
 * The drawer's Shuffle is this same order under another name (`Cell.ordering` reads it off `sort`),
 * so a shuffled cell draws from the whole of its source, not a jumble of one page of sixty.
 *
 * Taken out of `SORT_OPTIONS` by key rather than written again, for the reason the universal six
 * are: `Newest first` here and `Newest first` on Browse are then one string, not two that happen to
 * match today.
 */

import {
	RANDOM,
	RELEVANCE,
	RESHUFFLE,
	RESHUFFLE_OPTION,
	SIMILARITY,
	similarityChoice,
	similarToQuery,
	SORT_OPTIONS,
	WALL_ONLY_ORDERS
} from '$lib/grid/sort-state.svelte';
import type { SortChoice } from '$lib/components/shell/screen-bar.svelte';

/** The keys a cell cannot use: Closest match (see the note above), and the orders only the wall
 *  that asks for them offers (`WALL_ONLY_ORDERS`). */
const NOT_IN_A_CELL: ReadonlySet<string> = new Set([RELEVANCE, ...WALL_ONLY_ORDERS]);

export const CELL_ORDERS: readonly { value: string; label: string }[] = SORT_OPTIONS.filter(
	(option) => !NOT_IN_A_CELL.has(option.value)
).map((option) => ({ value: option.value, label: option.label }));

/** What a cell is in when nothing has said otherwise. The same default the file wall opens in. */
export const CELL_DEFAULT_ORDER = 'newest';

/**
 * The rows the Sort control shows for a cell already in a given order.
 *
 * One more while that order is Random, and here rather than in `+page.svelte` for the reason
 * `$lib/theater/narrowing` is not in the screen either: a decision written inside a route file is
 * a decision nothing can test. The screen reads it and draws it.
 *
 * Why the extra row exists at all: asking for the order you are already in is a press the chooser
 * refuses to deliver (it will not re-select, which is right, because an order is not a thing you
 * switch off), and for this one order that press means something, since a shuffle is a draw and
 * asking for it again is asking for a DIFFERENT one. The file wall has exactly this arrangement;
 * the row itself is taken from there rather than written again.
 */
export function cellOrders(sort: string | null, source = ''): readonly SortChoice[] {
	/* Similarity is close to what the cell draws from: its words, which in a cell are a Smart
	   Search the moment it is in this order (the order is how a cell carries one), or the file a
	   `like:` names. A cell with neither draws it dimmed with the reason. */
	const rows = CELL_ORDERS.map((row) =>
		row.value === SIMILARITY ? similarityChoice(similarToQuery(source), row.label) : row
	);
	return sort === RANDOM ? [...rows, RESHUFFLE_OPTION] : rows;
}

/**
 * What a press on that control MEANS, as an order a cell can be put in.
 *
 * `Shuffle again` is not an order and nothing may store it as one (see `RESHUFFLE`), so it is
 * turned back into Random here, on the way in, at the one place that reads the press. What makes
 * the second press do something is `Cell.orderBy`, which mints a fresh shuffle every time it is
 * handed Random.
 */
export function orderPressed(next: string): string {
	return next === RESHUFFLE ? RANDOM : next;
}

/**
 * What the Sort control says when it cannot act HERE.
 *
 * Its own sentence rather than the general one, because "nothing to order on this screen" is false
 * on a wall of files: there is plainly something to order, and the reason the control is dim is
 * that nothing has been chosen to order. A dimmed control saying something untrue about the screen
 * reads as the application being broken.
 */
export const NO_CELL_TO_ORDER = 'Choose a cell to order what it shows';

/**
 * A cell this screen can put in an order.
 *
 * The three things `Cell` already does rather than `Cell` itself, so that what a press MEANS can be
 * checked without a wall, a router and a mounted screen behind it.
 */
export interface Orderable {
	orderBy(next: string | null): void;
	reorder(): Promise<void>;
	restart(): Promise<void>;
}

/**
 * WHAT A PRESS ON THE SORT CONTROL DOES TO THE CELLS THE BAR HAS ADDRESSED.
 *
 * The order goes on every one of them and only the RUN is rebuilt, which is this screen's rule: an
 * order is a fact about what comes next, and cutting off the file somebody is watching to prove
 * that the order changed is the fault `Cell.reorder` exists to avoid.
 *
 * **A DRAW IS THE EXCEPTION, and that is arithmetic rather than a preference.** Under a rebuild, a
 * press can land on exactly the addressed cells, mint a fresh seed for each and bring the run back
 * shuffled, and nothing on screen moves, because the file playing in a cell came out of the run
 * the shuffle has just replaced.
 *
 * For `Newest first` the rule is right and it is checkable: the very next file the cell plays is
 * the newest one. For Random it is neither. `Shuffle again` does not change the order at all
 * (only WHICH shuffle), so under a rebuild that leaves the front of the run alone it cannot change
 * anything anybody can see, ever. A control that is unobservable by construction is a control that
 * does nothing.
 *
 * So a press asking for a draw DELIVERS one: the addressed cells go to the front of the fresh
 * permutation now. `Cell.restart` rather than something written for this, because it is already
 * what this codebase means by somewhere else out of what a cell draws from (it is the casino
 * control in the cell's own drawer), and shuffling a cell is playing something else out of a deck
 * that has just been re-cut. Every other order keeps `reorder`, and the reasoning above it stands.
 *
 * Both shuffle rows arrive here as Random (see `orderPressed`), and each of the two calls below
 * mints a shuffle of its own: `orderBy` because a cell entering Random order has to HAVE one, and
 * `restart` because a new run is a new draw. So the second press is a second draw rather than an
 * order of its own, and the casino control, which restarts without reordering, is a fresh
 * permutation each time too.
 */
export function applyOrder(cells: Iterable<Orderable>, next: string): void {
	const order = orderPressed(next);
	for (const cell of cells) {
		cell.orderBy(order);
		void (order === RANDOM ? cell.restart() : cell.reorder());
	}
}

/**
 * WHAT A PRESS ON THE DRAWER'S SHUFFLE DOES: Random over the whole source, or back in order.
 *
 * Turning it on is `orderBy(RANDOM)`, which mints a fresh seed on every press, so pressing it
 * again after turning it off is a different shuffle, not the old one from its top. Only the run is
 * rebuilt: the file on screen plays on, and the casino beside it is the control that jumps.
 */
export function pressShuffle(cells: Iterable<Orderable>, shuffling: boolean): void {
	for (const cell of cells) {
		cell.orderBy(shuffling ? null : RANDOM);
		void cell.reorder();
	}
}

/**
 * THE ORDER A SHUFFLED CELL GOES ROUND IN THE SECOND TIME, where its whole source is in one page.
 *
 * A fresh seed at the wrap is a fresh shuffle, and on a large source that is all it takes: the
 * chance of the same order twice is nil. On a small one it is not. Two files have two orders, so a
 * fresh seed gives back the order just played half the time, and a two-file wall then plays the
 * same order twice. So where the whole round is known (it fitted one page), the
 * new round is held to differ from the one before it, and, where the source allows that too, not
 * to open on the file that has just ended.
 *
 * Reversal and rotation rather than asking the server again: one of the four always differs from
 * the round before (a run of distinct ids is never its own reverse), so this cannot loop, costs no
 * request, and is still the server's shuffle: turned round, not jumbled. With two files the two
 * wishes cannot both hold: the only different order opens on the file that just ended, and the
 * only order that does not is the one just played. Not repeating the clip wins, so a two-file cell
 * alternates for ever; a viewer sees the same order again and never the same clip twice running.
 * From three files up one of the four choices always satisfies both.
 *
 * Judged on the files the cell PLAYS, not on the page. `passed` is what the cell stepped over last
 * time round (a file whose only copy is missing, one not read yet), and a page holding those
 * can differ from the last one only where nothing is shown: turning it round could put a gone
 * file first and the file that just ended straight after it, which is the file opening the round
 * again. So the order is chosen over the playable files, and the passed ones go last, where they
 * are asked about again without standing in front of anything.
 *
 * AND ON THE ORDER THEY WILL BE SHOWN IN, which is not always the order they are dealt in.
 * `before` is the round as it was SHOWN, and `showing` says how a dealt run will be shown: a cell
 * plays a file this browser takes as it is ahead of one the install has to convert (see
 * `showingOrder`). Judged on the deal alone, a round dealt [converted, direct, direct] and one
 * dealt [direct, converted, direct] are two orders that SHOW as one, and a round whose deal
 * opened on something else could still open, on screen, on the file that had just ended.
 */
export function anotherRound<T extends { id: string }>(
	before: readonly string[],
	fresh: readonly T[],
	ended: string | null,
	passed: ReadonlySet<string> = new Set(),
	showing: (ids: readonly string[]) => readonly string[] = (ids) => ids
): T[] {
	const played = before.filter((id) => !passed.has(id));
	const playable = fresh.filter((file) => !passed.has(file.id));
	const gone = fresh.filter((file) => passed.has(file.id));
	if (playable.length < 2) return [...fresh];
	const shown = (run: readonly T[]) => showing(run.map((file) => file.id));
	const same = (run: readonly T[]) => {
		const order = shown(run);
		return order.length === played.length && order.every((id, at) => id === played[at]);
	};
	const opensOnEnded = (run: readonly T[]) => shown(run)[0] === ended;
	const turned = [...playable].reverse();
	const rotated = (run: readonly T[]) => [...run.slice(1), run[0]];
	const choices = [[...playable], turned, rotated(playable), rotated(turned)];
	const chosen =
		choices.find((run) => !same(run) && !opensOnEnded(run)) ??
		choices.find((run) => !opensOnEnded(run)) ??
		choices.find((run) => !same(run)) ??
		playable;
	return [...chosen, ...gone];
}

/**
 * THE ORDER A CELL WILL SHOW A DEALT RUN IN, from what it knows of each file.
 *
 * The rule `Cell.#nextPlayable` plays by, written once where it can be predicted: of the next
 * `tries` files, the first one this browser plays as it is (`direct`), or the first of them where
 * none does, and the ones passed over wait at the front of the run for the next pick rather
 * than being dropped from it. `avoid` is the file on screen when the run starts, which opens it
 * only where there is nothing else to open on.
 *
 * `direct` answers for a file the cell has not asked about yet as well, and an unknown file is
 * best counted as direct: that is what a file is until the server says otherwise.
 */
export function showingOrder(
	ids: readonly string[],
	direct: (id: string) => boolean,
	tries: number,
	avoid: string | null = null
): string[] {
	const left = [...ids];
	const shown: string[] = [];
	let last = avoid;
	while (left.length > 0) {
		const open = left.filter((id) => id !== last);
		const within = (open.length > 0 ? open : left).slice(0, Math.max(1, tries));
		const pick = within.find(direct) ?? within[0];
		left.splice(left.indexOf(pick), 1);
		shown.push(pick);
		last = pick;
	}
	return shown;
}
