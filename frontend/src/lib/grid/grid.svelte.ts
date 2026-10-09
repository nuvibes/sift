/* The grid's state, kept out of the component so it can be tested without a browser. */

import { api, type ApiPath } from '$lib/api/client';
import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import type { components } from '$lib/api/schema';
import type { OpinionPatch } from '$lib/library/changes.svelte';
import { SvelteSet } from 'svelte/reactivity';
import {
	CAPTION_HEIGHT,
	PAGE_SCREENS,
	SIZE_STEPS,
	averageAspect,
	fillRows,
	fillRowsFromEnd,
	GRID_GUTTER,
	targetRowHeight as rowHeightFor,
	itemsToFill,
	rowsThatFit,
	snapRowHeight,
	wallOf,
	type LaidOutRow,
	type Layable,
	type SizeStep
} from './justify';
import { FAVORITED_ORDERS } from './sort-state.svelte';

/* LIVE: followed by lib/components/AssetGrid.svelte (the page on screen read again on the arrivals and library bells: catchUp) */

/** One row of a wall: a file, or a MOMENT of a file (Loops), as the server defines it. */
export type GridItem = components['schemas']['AssetSummary'] &
	Partial<
		Pick<
			components['schemas']['LoopSummary'],
			'asset_id' | 'still' | 'start_ms' | 'end_ms' | 'tags' | 'whole' | 'name'
		>
	>;

/**
 * The stretch a row names, for opening it, or nothing where the row IS its file: a loop cut as a
 * clip is the whole of itself, so it looks the same from Browse and from Loops.
 */
export function stretchOf(item: GridItem): { at?: number; until?: number } {
	if (item.whole === true) return {};
	return { at: item.start_ms, until: item.end_ms };
}

type AssetPage = Omit<components['schemas']['AssetPageResponse'], 'items'> & { items: GridItem[] };

/** The size beside a page's total; absent and null are both "not said". */
function bytesOf(page: object): number | null {
	const said = (page as { total_bytes?: number | null }).total_bytes;
	return typeof said === 'number' ? said : null;
}
export type FilterProblem = components['schemas']['FilterProblem'];
/** The username a page was filtered to with `?username=`, in the words its chip draws. */
export type NarrowedToUsername = components['schemas']['NarrowedToUsername'];

/* Storage may throw either way; the fallback is a grid that forgets. */
const SIZE_KEY = 'sift.grid.rowHeight';

function rememberedSize(): SizeStep | null {
	const held = Number(readStored(SIZE_KEY));
	return SIZE_STEPS.find((step) => step === held) ?? null;
}

export function rememberSize(height: SizeStep): void {
	writeStored(SIZE_KEY, String(height));
}

/* How big the tiles are, for the whole application: the slider is on the bar above every screen. */
class GridSize {
	/** The chosen step, or null for "whatever fits the window", which is the shipped behaviour. */
	step = $state<SizeStep | null>(rememberedSize());

	set(next: SizeStep): void {
		this.step = next;
		rememberSize(next);
	}
}

export const gridSize = new GridSize();

/* Whether previews play under the pointer alone, or everywhere they can be seen. Not kept. */
class GridAutoplay {
	mode = $state<'hover' | 'visible'>('hover');

	/* A count per wall, so a wall that goes away takes its share with it. */
	#walls = new SvelteSet<() => number>();

	toggle(): void {
		this.mode = this.mode === 'hover' ? 'visible' : 'hover';
	}

	/* How many previews are playing NOW, counted off the walls' slots, which follow the scroll. */
	get playing(): number {
		let total = 0;
		for (const moving of this.#walls) total += moving();
		return total;
	}

	/** Count one wall's moving previews for as long as it is drawn. Returns the way to stop. */
	counts(moving: () => number): () => void {
		this.#walls.add(moving);
		return () => this.#walls.delete(moving);
	}
}

/** Where the press goes first (a tooltip is read just before pressing), then how many move. */
export function previewWords(
	mode: 'hover' | 'visible',
	playing: number,
	cap: number
): { label: string; hint: string } {
	if (mode === 'hover') {
		const label = 'Preview everything visible';
		return { label, hint: label };
	}
	const label = 'Preview on hover';
	const now =
		playing === 0
			? 'No previews are playing now.'
			: playing === 1
				? '1 preview is playing now.'
				: `${playing} previews are playing now.`;
	const ceiling = playing >= cap ? ` Up to ${cap} play at a time, to keep the app responsive.` : '';
	return { label, hint: `${label}. ${now}${ceiling}` };
}

export const gridAutoplay = new GridAutoplay();

/* The server's page ceiling, mirrored so a larger request is split, not read as the end. */
export const SERVER_PAGE_CAP = 200;

/** The most "select all" picks in one press; `ActionBar` states the cap. */
export const MOST_SELECTED = 1000;

/* How many times a page may go back for more; the bound stops a bad estimate looping. */
const MAX_FETCHES = 3;

/* A page is sized for portrait clips, never off its own answer, which would resize and re-read it. */
const PLANNED_ASPECT = 9 / 16;

/* The first ask allows for files this narrow: a top-up costs a round trip, a surplus only bytes. */
const FIRST_ASK_ASPECT = 3 / 4;

/* Orders a page may be continued under by naming its last row: the server's offer, copied. */
const CONTINUABLE = new Set([
	'newest',
	'oldest',
	'name_az',
	'name_za',
	'longest',
	'shortest',
	'largest',
	'smallest',
	'random'
]);

/* WHAT A WALL ASKS FOR: a repeated parameter means "all of these" (`bothNarrowings`). */
type WallQuery = Record<string, string | readonly string[]>;

/** Whether a wall with this query can continue from a row rather than from an offset. */
export function continuable(query: WallQuery): boolean {
	// A pinned wall or a photo set's own order cannot continue from a row (the server answers 422).
	if (flagged(lastOf(query.pinned_first)) || query.photo_set) return false;
	return CONTINUABLE.has(lastOf(query.sort) ?? 'newest');
}

/* Only fields naming things a file carries several of ever repeat. */
function lastOf(value: string | readonly string[] | undefined): string | undefined {
	return Array.isArray(value) ? value[value.length - 1] : (value as string | undefined);
}

/** Whether a query flag is on, in any of the spellings the server accepts as true. */
function flagged(value: string | undefined): boolean {
	return value !== undefined && value !== '' && value !== '0' && value !== 'false';
}

/**
 * Where a page is read from. Two read BACKWARDS: a page holds however many files fill its rows, so
 * the page before is found only by filling backwards from where this one starts.
 */
export type PageStart =
	/** From a known offset: the beginning, or a jump to a position. */
	| { at: number }
	/**
	 * From the file an address names, or, when it is gone, from `near` (see `NEAR` in `./anchor`).
	 */
	| { from: string; near?: number | null }
	/** Backwards, so the page ENDS just before this offset. Going back one page. */
	| { endingAt: number }
	/** Backwards from the end, so the last file in the library lands at the bottom of the screen. */
	| { last: true };

/**
 * Where a grid reads its rows from: `/assets`, or any list in its shape. `anchored` says the server
 * can turn a row's id into a position here, so `from` is never sent where it is ignored.
 */
export interface RowSource {
	/** A real endpoint of the schema, so a typo is a build error. */
	path: ApiPath;
	anchored: boolean;
	/** The orders this list understands, or null for the whole vocabulary. */
	sorts: readonly string[] | null;
	/** Orders only a wall that asks for them offers (`WALL_ONLY_ORDERS`), where `sorts` is null. */
	adds?: readonly string[];
	/** Orders left out because they would sort every row equal. */
	drops?: readonly string[];
	/**
	 * What an order is CALLED on this wall, where the file wall's word would name the wrong thing.
	 */
	says?: Readonly<Record<string, string>>;
	/** Whether the query language reaches this list, or a sentence saying why not. */
	filterable: true | string;
	/**
	 * WHAT EVERY ROW'S FILE IS, in the query language, since the bar counts in the file language.
	 */
	files?: Readonly<Record<string, string>>;
	/**
	 * Whether each row draws a name under its tile; the line is part of its height
	 * (`CAPTION_HEIGHT`).
	 */
	captioned?: boolean;
	/** What the pager calls a row, where it is not a file. */
	noun?: string;
}

/** A wall of MOMENTS, for the Loops wall and the Loops tab of a tag, a person or a site. */
export const LOOP_SOURCE: RowSource = {
	path: '/loops',
	anchored: true,
	sorts: ['newest', 'oldest', 'edited', 'name_az', 'name_za', 'largest', 'smallest'],
	/* Named by what they order by here: when the mark was made, and its length. */
	says: {
		newest: 'Recently created',
		oldest: 'Oldest created',
		largest: 'Longest',
		smallest: 'Shortest'
	},
	filterable: true,
	files: { loops: 'any' },
	captioned: true,
	noun: 'loops'
};

export const ASSET_SOURCE: RowSource = {
	path: '/assets',
	anchored: true,
	sorts: null,
	filterable: true
};

/** The library as Favorites reads it, with the two orders about when a heart was pressed. */
export const FAVORITES_SOURCE: RowSource = {
	...ASSET_SOURCE,
	adds: FAVORITED_ORDERS,
	drops: ['favorite'],
	files: { fav: 'yes' }
};

/** What the answers so far say about the whole list. */
type Known = {
	total: number;
	totalBytes: number | null;
	complete: boolean;
	problems: FilterProblem[];
	username: NarrowedToUsername | null;
};

function knownFrom(answer: AssetPage): Known {
	return {
		total: answer.total,
		totalBytes: bytesOf(answer),
		complete: answer.complete ?? true,
		problems: answer.problems ?? [],
		username: answer.username ?? null
	};
}

/** A block's rows not already collected: a search by meaning can answer overlapping blocks. */
function takeFresh(
	items: GridItem[],
	taken: Set<string>,
	collected: GridItem[],
	fromEnd: boolean
): void {
	const fresh = items.filter((item) => !taken.has(item.id));
	for (const item of fresh) taken.add(item.id);
	if (fromEnd) collected.unshift(...fresh);
	else collected.push(...fresh);
}

/**
 * One request of a fill. The anchor is for the FIRST request only; a forward top-up continues after
 * the last row held where the order allows: one index seek, however deep the page.
 */
function askFor(
	query: WallQuery,
	start: PageStart,
	read: { attempt: number; fromEnd: boolean; size: number; at: number },
	collected: readonly GridItem[]
): Record<string, string | number | boolean | readonly string[]> {
	const { attempt, fromEnd, size, at } = read;
	const asked: Record<string, string | number | boolean | readonly string[]> = {
		...query,
		limit: size
	};
	const last = collected[collected.length - 1];
	if ('from' in start && attempt === 0) {
		asked.from = start.from;
		if (start.near !== undefined && start.near !== null) asked.near = start.near;
	} else if (!fromEnd && attempt > 0 && last !== undefined && continuable(query)) {
		asked.after = last.id;
	} else asked.offset = at;
	return asked;
}

/** Everything loaded so far, and the layout over it. */
export class Grid {
	/** Where the rows come from. See `RowSource`; the default is the library itself. */
	readonly source: RowSource;

	constructor(source: RowSource = ASSET_SOURCE) {
		this.source = source;
	}

	items = $state<GridItem[]>([]);
	total = $state(0);
	/** The bytes of the files `total` counts, or null where not known. */
	totalBytes = $state<number | null>(null);
	loading = $state(false);
	failed = $state<string | null>(null);

	/** Answered, and the answer was nothing: a re-measure of an empty page does not ask again. */
	get answeredEmpty(): boolean {
		return !this.loading && this.failed === null && this.total === 0 && this.items.length === 0;
	}

	/** How many files the page on screen is actually drawing. The pager's range reads off it. */
	loaded = $state(0);

	/** Where the page on screen begins: held, since a page has no number. */
	offset = $state(0);

	/**
	 * Where the page was ASKED to begin: files landing above raise `offset`; the gap is `newer`.
	 */
	askedAt = $state(0);

	/**
	 * Which page is on screen; the wall's entrance is keyed on it, so a catch-up never replays it.
	 */
	showing = $state<string | null>(null);

	/** Whether a request for this grid is out, however asked: `loading` a quiet catch-up leaves alone. */
	reading = $state(false);

	/** Whether the page on screen is its first block, the rows that top it up still on their way. */
	filling = $state(false);

	#said = { offset: 0, shown: 0, total: 0 };

	/** What the pager says: the page as it last landed whole, never a number it takes back. */
	readonly said = $derived.by(() => {
		const now = { offset: this.offset, shown: this.wall.tiles.length, total: this.total };
		if (!this.loading && !this.filling) this.#said = now;
		return this.#said;
	});

	/** Files arrived above the page on screen, derived so it cannot drift from what is shown. */
	readonly newer = $derived(Math.max(0, this.offset - this.askedAt));

	/** The total when the page was asked for: `newer` with no growth since moved, never arrived. */
	askedTotal = $state(0);
	readonly newerArrived = $derived(this.total > this.askedTotal);
	readonly newerSaid = $derived(this.newerArrived ? 'new' : 'moved');

	/** One screenful of the grid area; the row height and page size come off this and the width. */
	screenHeight = $state(0);

	/* False only when a search by meaning stopped before filling the page. */
	complete = $state(true);
	/** Filters whose value could not be read; each matches nothing. */
	problems = $state<FilterProblem[]>([]);
	/** The username this page is filtered to, as the server named it, for the bar's chip. */
	username = $state<NarrowedToUsername | null>(null);

	/* Which query the answers coming back belong to, so an older one cannot overwrite the grid. */
	#generation = 0;
	#quietOwed = false;

	containerWidth = $state(0);
	/**
	 * Null until somebody moves the size control: a window onto `gridSize`, so every grid follows
	 * the slider. Not the account's: a late server value would lay the grid out twice.
	 */
	get sizeOverride(): number | null {
		return gridSize.step;
	}

	set sizeOverride(next: number | null) {
		if (next === null) return;
		gridSize.set(next as SizeStep);
	}

	gutter = GRID_GUTTER;

	/**
	 * Whether the screen asked for files a product gave up on, so each row's line is laid out
	 * early.
	 */
	leftOutAsked = $state(false);

	/* The typed `left_out:` is free text the client may not take apart, so the rows say it. */
	readonly #rowsSayWhy = $derived(this.items.some((item) => Boolean(item.left_out)));

	/**
	 * The line under every row's tiles: a wall of moments, or rows saying why they were left out.
	 */
	get caption(): number {
		return this.source.captioned || this.leftOutAsked || this.#rowsSayWhy ? CAPTION_HEIGHT : 0;
	}

	/** True while the page on screen was filled BACKWARDS. Declared before `filled`, which reads it. */
	#fromEnd = $state(false);

	/** The height the size control asked for, before it is made to divide the screen evenly. */
	readonly targetRowHeight = $derived(rowHeightFor(gridSize.step, this.containerWidth));

	/** The chosen size nudged to divide one screenful into whole rows (`snapRowHeight`). */
	readonly rowHeight = $derived(
		snapRowHeight(this.targetRowHeight, this.screenHeight, this.gutter, this.caption)
	);

	/** How many whole rows one screenful holds, a captioned row counted with its line. */
	readonly rowsPerScreen = $derived(
		rowsThatFit(this.screenHeight, this.rowHeight + this.caption, this.gutter)
	);

	/** Rows in one PAGE: whole screenfuls, fewer where `MAX_FETCHES` could not fill it. */
	readonly pageRows = $derived.by(() => {
		const perScreen = this.rowsPerScreen;
		for (let screens = PAGE_SCREENS; screens > 1; screens -= 1) {
			if (this.#estimateFor(perScreen * screens, PLANNED_ASPECT) <= SERVER_PAGE_CAP * MAX_FETCHES) {
				return perScreen * screens;
			}
		}
		return perScreen;
	});

	/**
	 * The page laid out in exactly `pageRows` complete rows; files fetched past them are the next
	 * page's. One row left at the end of the list is drawn here, never as a page of its own.
	 */
	readonly filled = $derived.by(() => {
		const shape = {
			containerWidth: this.containerWidth,
			targetHeight: this.rowHeight,
			gutter: this.gutter,
			rows: this.pageRows
		};
		if (this.#fromEnd) return fillRowsFromEnd(this.items, shape);
		const page = fillRows(this.items, shape);
		const atTheEnd = this.complete && this.offset + this.items.length >= this.total;
		if (!atTheEnd || page.used === this.items.length) return page;
		const tail = fillRows(this.items, { ...shape, rows: shape.rows + 1 });
		return tail.used === this.items.length ? tail : page;
	});

	readonly rows: LaidOutRow[] = $derived(this.filled.rows);

	/** One flat list, so a tile changing row is moved, not rebuilt (`wallOf`). */
	readonly wall = $derived(wallOf(this.rows, this.gutter, this.caption));

	/** Where the NEXT page begins: this page's surplus is its first row. */
	readonly nextOffset = $derived(this.offset + this.filled.used);

	readonly byId = $derived(new Map(this.items.map((item) => [item.id, item])));

	/** The server's heart and stars for one item, so a re-render does not draw the old ones. */
	setState(id: string, state: OpinionPatch, stillBelongs?: (item: GridItem) => boolean): void {
		this.items = this.items.map((item) =>
			item.id === id
				? {
						...item,
						favorite: state.favorite,
						rating: state.rating,
						views: state.views ?? item.views
					}
				: item
		);

		/* A row the screen is not about any more leaves (a heart taken off on Favorites). */
		if (!stillBelongs) return;
		const updated = this.byId.get(id);
		if (updated && !stillBelongs(updated)) {
			this.items = this.items.filter((item) => item.id !== id);
			this.total = Math.max(0, this.total - 1);
			this.askedTotal = Math.max(0, this.askedTotal - 1);
			this.totalBytes = null;
		}
	}

	/** Roughly how many files it takes to fill this many rows, at the geometry on screen now. */
	#estimateFor(rows: number, aspect: number): number {
		return itemsToFill(rows, {
			containerWidth: this.containerWidth,
			rowHeight: this.rowHeight,
			averageAspect: aspect
		});
	}

	/**
	 * One block of the same list, read WITHOUT changing this grid: for a run played past the page
	 * on screen. Empty on any failure: either way there is nothing more to play.
	 */
	async blockAt(query: WallQuery, offset: number, limit: number): Promise<GridItem[]> {
		try {
			return await this.#block(query, offset, limit);
		} catch {
			return [];
		}
	}

	/* Letting a failure through: select all must not take a failed request as the end. */
	async #block(query: WallQuery, offset: number, limit: number): Promise<GridItem[]> {
		const answer = await api.get<AssetPage>(this.source.path, {
			query: { ...query, offset, limit: Math.min(limit, SERVER_PAGE_CAP) }
		});
		return answer.items;
	}

	/** Every ROW this query matches, up to `MOST_SELECTED`; throws rather than answer short. */
	async everyRow(query: WallQuery): Promise<GridItem[]> {
		const rows: GridItem[] = [];
		// One block past the count tells an exact multiple of the page size from a list that goes
		// on.
		const most = Math.min(this.total + SERVER_PAGE_CAP, MOST_SELECTED);
		for (let at = 0; at < most; at += SERVER_PAGE_CAP) {
			const block = await this.#block(query, at, Math.min(SERVER_PAGE_CAP, MOST_SELECTED - at));
			for (const one of block) rows.push(one);
			if (block.length < SERVER_PAGE_CAP) break;
		}
		return rows.slice(0, MOST_SELECTED);
	}

	/** A page somebody asked for has landed: where it begins, and what the wall is now showing. */
	#show(query: WallQuery, offset: number, total: number): void {
		this.askedAt = offset;
		this.askedTotal = total;
		this.showing = `${JSON.stringify(query)}\n${offset}`;
	}

	/** Where one row sits in this query, or null (a wall of moments answers null). */
	async positionOf(query: WallQuery, id: string): Promise<number | null> {
		try {
			const answer = await api.get<AssetPage>(this.source.path, {
				query: { ...query, from: id, limit: 1 }
			});
			return answer.items[0]?.id === id ? answer.offset : null;
		} catch {
			return null;
		}
	}

	/** The page at `start`, or with `sizeOnly` the page on screen fitted to a new page size. */
	fit(query: WallQuery, start: PageStart, sizeOnly: boolean): Promise<void> {
		return sizeOnly ? this.#refit(query, start) : this.loadAt(query, start);
	}

	/* A read on its way takes the new size; only a page grown past what it holds is read. */
	async #refit(query: WallQuery, start: PageStart): Promise<void> {
		if (this.reading) return;
		const held = { total: this.total, complete: this.complete };
		if (this.#stillWanted(this.items, this.offset, held, this.#fromEnd) <= 0) return;
		await this.loadAt(query, this.#fromEnd ? start : { at: this.offset }, { quiet: true });
	}

	/** Load the page that begins here; the generation check keeps an older answer from landing. */
	async loadAt(
		query: WallQuery,
		start: PageStart,
		options: { quiet?: boolean } = {}
	): Promise<void> {
		// A catch-up nobody asked for never replaces a page somebody did: it waits for that page.
		if (this.#holdsBack(options)) return;
		const generation = ++this.#generation;
		if (!options.quiet) this.loading = true;
		this.reading = true;
		this.failed = null;

		const endAt = this.#endOf(start);
		const fromEnd = endAt > 0;

		const collected: GridItem[] = [];
		const taken = new Set<string>();
		let began = 'at' in start ? Math.max(0, start.at) : 0;
		let known = {
			total: this.total,
			totalBytes: this.totalBytes,
			complete: true,
			problems: [] as FilterProblem[],
			username: null as NarrowedToUsername | null
		};

		try {
			for (let attempt = 0; attempt < MAX_FETCHES; attempt += 1) {
				const wanted = this.#stillWanted(collected, began, known, fromEnd);
				if (wanted <= 0) break;

				// Backwards, the block just before what is held, clamped: the first can be short.
				const size = Math.min(wanted, SERVER_PAGE_CAP);
				const at = fromEnd
					? Math.max(0, endAt - collected.length - size)
					: began + collected.length;

				const asked = askFor(query, start, { attempt, fromEnd, size, at }, collected);

				const answer = await api.get<AssetPage>(this.source.path, { query: asked });
				if (generation !== this.#generation) return;

				known = knownFrom(answer);
				if ('from' in start && attempt === 0) began = answer.offset;
				/* Said as each answer lands, so it never describes the last question. */
				this.total = known.total;
				this.totalBytes = known.totalBytes;
				takeFresh(answer.items, taken, collected, fromEnd);

				// A short answer from the SERVER is the end; a full block of duplicates is not.
				if (answer.items.length < size) break;
				if (fromEnd && at === 0) break;
				this.#drawFirst({ attempt, fromEnd, quiet: options.quiet }, query, collected, began, known);
			}

			if (generation !== this.#generation) return;

			// A page past the end, reached by a file that is gone: `near` can be the list's length.
			if ('from' in start && collected.length === 0 && began > 0) {
				await this.#stepBack(query, known.total, generation, options);
				return;
			}

			const landed = this.#landedAt(fromEnd, endAt, collected, began);

			const answer: AssetPage = {
				items: collected,
				total: known.total,
				limit: collected.length,
				offset: landed,
				complete: known.complete,
				problems: known.problems,
				username: known.username,
				total_bytes: known.totalBytes
			};
			this.#land(query, answer, known, { collected, fromEnd, quiet: options.quiet });
		} catch (error) {
			if (generation !== this.#generation) return;
			this.#fail(error, options);
		} finally {
			if (generation === this.#generation) {
				this.loading = false;
				this.reading = false;
				this.filling = false;
				this.#payOwed(query, options);
			}
		}
	}

	/*
	 * Back to the last page when an anchor lands past the end; a count of 0 there may be windowed.
	 */
	async #stepBack(
		query: WallQuery,
		knownTotal: number,
		generation: number,
		options: { quiet?: boolean }
	): Promise<void> {
		if (knownTotal <= 0) {
			const front = await api.get<AssetPage>(this.source.path, {
				query: { ...query, limit: 1, offset: 0 }
			});
			if (generation !== this.#generation) return;
			this.total = front.total;
			this.totalBytes = bytesOf(front);
		}
		await this.loadAt(query, this.total > 0 ? { last: true } : { at: 0 }, options);
	}

	/* Reading backwards, the surplus sits at the FRONT (`skipped`). */
	#landedAt(fromEnd: boolean, endAt: number, collected: GridItem[], began: number): number {
		const base = fromEnd ? Math.max(0, endAt - collected.length) : began;
		return (
			base +
			(fromEnd
				? fillRowsFromEnd(collected, {
						containerWidth: this.containerWidth,
						targetHeight: this.rowHeight,
						gutter: this.gutter,
						rows: this.pageRows
					}).skipped
				: 0)
		);
	}

	/* A quiet load that failed leaves the screen as it was. */
	#fail(error: unknown, options: { quiet?: boolean }): void {
		if (options.quiet) return;
		this.items = [];
		this.failed = error instanceof Error ? error.message : 'That did not work.';
	}

	#land(
		query: WallQuery,
		answer: AssetPage,
		known: Known,
		read: { collected: GridItem[]; fromEnd: boolean; quiet?: boolean }
	): void {
		/*
		 * Nothing changed, so change nothing: a new equal array would redraw every tile. The total
		 * is taken, not compared; the offset is not compared either.
		 */
		if (this.#fromEnd === read.fromEnd && this.#same(answer)) {
			this.offset = answer.offset;
			if (!read.quiet) this.#show(query, answer.offset, known.total);
			this.total = known.total;
			this.totalBytes = known.totalBytes;
			this.complete = known.complete;
			this.problems = known.problems;
			this.username = known.username;
			return;
		}
		this.#fromEnd = read.fromEnd;
		this.items = read.collected;
		this.offset = answer.offset;
		if (!read.quiet) this.#show(query, answer.offset, known.total);
		this.loaded = read.collected.length;
		this.total = known.total;
		this.totalBytes = known.totalBytes;
		this.complete = known.complete;
		this.problems = known.problems;
		this.username = known.username;
	}

	/* The first block on screen before its top-up is read. */
	#drawFirst(
		read: { attempt: number; fromEnd: boolean; quiet?: boolean },
		query: WallQuery,
		collected: readonly GridItem[],
		began: number,
		known: Known
	): void {
		if (read.attempt > 0 || read.fromEnd || read.quiet || collected.length === 0) return;
		this.#fromEnd = false;
		this.items = [...collected];
		this.offset = began;
		this.#show(query, began, known.total);
		this.loaded = collected.length;
		this.total = known.total;
		this.totalBytes = known.totalBytes;
		this.complete = known.complete;
		this.problems = known.problems;
		this.username = known.username;
		this.loading = false;
		this.filling = true;
	}

	/* `last` ends at the end of the library, a page back where this page begins. */
	#endOf(start: PageStart): number {
		return 'last' in start ? this.total : 'endingAt' in start ? start.endingAt : -1;
	}

	#holdsBack(options: { quiet?: boolean }): boolean {
		if (!options.quiet || !this.loading) return false;
		this.#quietOwed = true;
		return true;
	}

	#payOwed(query: WallQuery, options: { quiet?: boolean }): void {
		if (!this.#quietOwed || options.quiet || this.failed !== null) return;
		this.#quietOwed = false;
		void this.loadAt(query, { at: this.offset }, { quiet: true });
	}

	/** How many more files this page needs, estimated from the shapes in hand. */
	#shortfall(collected: readonly GridItem[], fromEnd: boolean): number {
		if (collected.length === 0) {
			const aspect = Math.min(averageAspect(this.items), FIRST_ASK_ASPECT);
			return this.#estimateFor(this.pageRows, aspect);
		}

		const laid = (fromEnd ? fillRowsFromEnd : fillRows)(collected, {
			containerWidth: this.containerWidth,
			targetHeight: this.rowHeight,
			gutter: this.gutter,
			rows: this.pageRows
		});
		if (laid.full) return 0;

		const missing = Math.max(1, this.pageRows - laid.rows.filter((row) => row.filled).length);
		return itemsToFill(missing, {
			containerWidth: this.containerWidth,
			rowHeight: this.rowHeight,
			averageAspect: averageAspect(collected)
		});
	}

	/* What the page still needs, or the files after it when they are a row or less. */
	#stillWanted(
		collected: readonly GridItem[],
		began: number,
		known: { total: number; complete: boolean },
		fromEnd: boolean
	): number {
		const short = this.#shortfall(collected, fromEnd);
		const rest = known.total - began - collected.length;
		if (short > 0 || fromEnd || !known.complete || rest <= 0) return short;
		const row = itemsToFill(1, {
			containerWidth: this.containerWidth,
			rowHeight: this.rowHeight,
			averageAspect: averageAspect(collected)
		});
		return rest <= row ? rest : 0;
	}

	/* The same files, in order, saying the same things, the picture included; not the total. */
	#same(answer: AssetPage): boolean {
		if (answer.items.length !== this.items.length) return false;
		return answer.items.every((fresh, index) => {
			const shown = this.items[index];
			return (
				shown !== undefined &&
				shown.id === fresh.id &&
				shown.favorite === fresh.favorite &&
				shown.rating === fresh.rating &&
				shown.concealed === fresh.concealed &&
				shown.thumb === fresh.thumb &&
				shown.art === fresh.art &&
				shown.preview === fresh.preview &&
				shown.shared === fresh.shared &&
				shown.restricted === fresh.restricted &&
				shown.shared_here === fresh.shared_here &&
				shown.restricted_here === fresh.restricted_here &&
				shown.hidden === fresh.hidden &&
				shown.width === fresh.width &&
				shown.height === fresh.height &&
				shown.duration_ms === fresh.duration_ms
			);
		});
	}

	/* The optimistic echo of a delete; the next load reconciles. */
	forget(id: string): void {
		const remaining = this.items.filter((item) => item.id !== id);
		if (remaining.length === this.items.length) return;
		this.items = remaining;
		this.loaded = remaining.length;
		this.total = Math.max(0, this.total - 1);
		this.totalBytes = null;
	}
}
