/*
 * The orders one Theater cell can be put in: the file wall's, less Closest match, which needs words
 * to be close to. Random is kept: a seed held for the life of a cell's run is as steady as the
 * address bar's is for a visit.
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

/** Closest match, and the orders only the wall that asks for them offers. */
const NOT_IN_A_CELL: ReadonlySet<string> = new Set([RELEVANCE, ...WALL_ONLY_ORDERS]);

export const CELL_ORDERS: readonly { value: string; label: string }[] = SORT_OPTIONS.filter(
	(option) => !NOT_IN_A_CELL.has(option.value)
).map((option) => ({ value: option.value, label: option.label }));

/** What a cell is in when nothing has said otherwise. The same default the file wall opens in. */
export const CELL_DEFAULT_ORDER = 'newest';

/** The rows the Sort control shows for a cell; Random adds `Shuffle again`, a different draw. */
export function cellOrders(sort: string | null, source = ''): readonly SortChoice[] {
	/* A cell's words are a Smart Search in this order, or the file a `like:` names. */
	const rows = CELL_ORDERS.map((row) =>
		row.value === SIMILARITY ? similarityChoice(similarToQuery(source), row.label) : row
	);
	return sort === RANDOM ? [...rows, RESHUFFLE_OPTION] : rows;
}

/** What a press on that control means as an order: `Shuffle again` is Random, never stored. */
export function orderPressed(next: string): string {
	return next === RESHUFFLE ? RANDOM : next;
}

/** Its own sentence: on a wall of files there is plainly something to order. */
export const NO_CELL_TO_ORDER = 'Choose a cell to order what it shows';

/** What `Cell` does to be ordered, so a press can be checked without a wall behind it. */
export interface Orderable {
	orderBy(next: string | null): void;
	reorder(): Promise<void>;
	restart(): Promise<void>;
}

/**
 * A Sort press on the addressed cells: the order goes on each and only the run is rebuilt, except
 * Random, which restarts the cell, since a reshuffle behind the playing file would show nothing.
 */
export function applyOrder(cells: Iterable<Orderable>, next: string): void {
	const order = orderPressed(next);
	for (const cell of cells) {
		cell.orderBy(order);
		void (order === RANDOM ? cell.restart() : cell.reorder());
	}
}

/** The drawer's Shuffle: Random over the whole source with a fresh seed, or back in order. */
export function pressShuffle(cells: Iterable<Orderable>, shuffling: boolean): void {
	for (const cell of cells) {
		cell.orderBy(shuffling ? null : RANDOM);
		void cell.reorder();
	}
}

/**
 * The order a shuffled one-page source goes round in the second time: reversed or rotated so it
 * differs from the round just shown and does not open on the file that just ended (with two files
 * not repeating the clip wins). Judged on the playable files as they will be shown; the passed ones
 * go last.
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
 * The order a cell will show a dealt run in, the rule `Cell.#nextPlayable` plays by: of the next
 * `tries`, the first played as it is, else the first; an unknown file counts as direct.
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
