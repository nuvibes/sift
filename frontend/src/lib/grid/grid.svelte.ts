/*
 * The grid's state: what has been loaded, how it is laid out, and which rows are worth rendering.
 * Kept out of the component so it can be tested without a browser.
 */

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

/**
 * One row of a wall, taken from the server's own definitions rather than described again here.
 *
 * Rows are files on nearly every screen and MOMENTS of a file (Loops) on one, so a row is the
 * file's shape plus the fields a moment adds, rather than a second grid that would stop getting
 * this one's fixes.
 */
export type GridItem = components['schemas']['AssetSummary'] &
	Partial<
		Pick<
			components['schemas']['LoopSummary'],
			'asset_id' | 'still' | 'start_ms' | 'end_ms' | 'tags' | 'whole' | 'name'
		>
	>;

/**
 * The stretch a row names, for opening it, or nothing, where the row IS its file.
 *
 * A mark needs its bounds so the viewer stops at its end. A loop cut as a clip is the whole of
 * itself, and naming its bounds would draw it as carved from something longer, so the same file
 * would look different from Browse and from Loops. A rule about what a row IS, so it lives here.
 */
export function stretchOf(item: GridItem): { at?: number; until?: number } {
	if (item.whole === true) return {};
	return { at: item.start_ms, until: item.end_ms };
}

type AssetPage = Omit<components['schemas']['AssetPageResponse'], 'items'> & { items: GridItem[] };

/**
 * The size beside a page's total, where the answer carries one. Read off any answer shaped like a
 * page (a stand-in in a test may leave it out); absent and null are both "not said".
 */
function bytesOf(page: object): number | null {
	const said = (page as { total_bytes?: number | null }).total_bytes;
	return typeof said === 'number' ? said : null;
}
export type FilterProblem = components['schemas']['FilterProblem'];
/** The username a page was filtered to with `?username=`, in the words its chip draws. */
export type NarrowedToUsername = components['schemas']['NarrowedToUsername'];

/* Where the tile size is kept between screens. Storage may throw either way, and the fallback is
 * a grid that works and forgets. */
const SIZE_KEY = 'sift.grid.rowHeight';

function rememberedSize(): SizeStep | null {
	const held = Number(readStored(SIZE_KEY));
	/* Only a value the slider could have produced; anything else reads as "not set". Membership in
	 * `SIZE_STEPS` (`as const`) also narrows the type. */
	return SIZE_STEPS.find((step) => step === held) ?? null;
}

export function rememberSize(height: SizeStep): void {
	writeStored(SIZE_KEY, String(height));
}

/*
 * How big the tiles are, for the whole application rather than for one grid: the slider is on the
 * bar above every screen, so the number is kept here once.
 */
class GridSize {
	/** The chosen step, or null for "whatever fits the window", which is the shipped behaviour. */
	step = $state<SizeStep | null>(rememberedSize());

	set(next: SizeStep): void {
		this.step = next;
		rememberSize(next);
	}
}

export const gridSize = new GridSize();

/*
 * Whether previews play under the pointer alone, or everywhere they can be seen. Shared, as the
 * size is; not written down, since playing everything is a mode for skimming one page.
 */
class GridAutoplay {
	mode = $state<'hover' | 'visible'>('hover');

	/* A count per wall, so a wall that goes away takes its share with it. */
	#walls = new SvelteSet<() => number>();

	toggle(): void {
		this.mode = this.mode === 'hover' ? 'visible' : 'hover';
	}

	/*
	 * How many previews are playing NOW, on every wall on screen: counted off the walls' slots,
	 * which follow the scroll, rather than said as the ceiling.
	 */
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

/**
 * What the preview control says: first where the press goes (a tooltip is read just before
 * pressing), then how many are moving, and the ceiling only when it is what holds the rest still.
 */
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

/*
 * The most rows the server will hand back in one answer: the kernel's ceiling, mirrored so a
 * larger request is split rather than a short page read as the end of the library.
 */
export const SERVER_PAGE_CAP = 200;

/**
 * The most "select all" will pick in one press.
 *
 * Uncapped, one press on a large library is a selection of every id and every write from it
 * chunked many times over: not a gesture to make by accident. `ActionBar` announces the cap
 * ("Select 1,000 of 9,000"), so the short answer is a stated limit, not a silent one.
 */
export const MOST_SELECTED = 1000;

/*
 * How many times a page may go back for more before it draws what it has. Three covers very tall
 * clips and a very large monitor at the smallest tiles; the bound stops a bad estimate looping.
 */
const MAX_FETCHES = 3;

/* A page is sized for portrait clips, never off its own answer, which would resize and re-read it. */
const PLANNED_ASPECT = 9 / 16;

/* The first ask allows for files this narrow: a top-up costs a round trip, a surplus only bytes. */
const FIRST_ASK_ASPECT = 3 / 4;

/* The orders a page may be continued under by naming its last row: the server's offer, copied.
   Relevance, similarity and last-viewed order by something outside the row. */
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

/*
 * WHAT A WALL ASKS FOR: the query language's parameters, with a repeated one where two filters
 * apply to the same field at the same time (`bothNarrowings`), since the server reads a repeat as
 * "all of these", each value whole.
 */
type WallQuery = Record<string, string | readonly string[]>;

/** Whether a wall with this query can continue from a row rather than from an offset. */
export function continuable(query: WallQuery): boolean {
	// The pin flag is '1' from the grid or 'true' from a typed address. A pinned wall or a photo
	// set's own order cannot continue from a row (the server answers 422).
	if (flagged(lastOf(query.pinned_first)) || query.photo_set) return false;
	return CONTINUABLE.has(lastOf(query.sort) ?? 'newest');
}

/* One value out of a parameter that MAY have been given more than once: only fields naming things
 * a file carries several of ever repeat. */
function lastOf(value: string | readonly string[] | undefined): string | undefined {
	return Array.isArray(value) ? value[value.length - 1] : (value as string | undefined);
}

/** Whether a query flag is on, in any of the spellings the server accepts as true. */
function flagged(value: string | undefined): boolean {
	return value !== undefined && value !== '' && value !== '0' && value !== 'false';
}

/**
 * Where a page is being read from, as the caller asked for it.
 *
 * Two read FORWARDS and two BACKWARDS: a page holds however many files fill its rows, so the page
 * before is found only by filling backwards from where this one starts.
 */
export type PageStart =
	/** From a known offset: the beginning, or a jump to a position. */
	| { at: number }
	/**
	 * From the file an address names, wherever the server says that now sits, or, when that file
	 * is gone, from `near`, the offset it was at (see `NEAR` in `./anchor`).
	 */
	| { from: string; near?: number | null }
	/** Backwards, so the page ENDS just before this offset. Going back one page. */
	| { endingAt: number }
	/** Backwards from the end, so the last file in the library lands at the bottom of the screen. */
	| { last: true };

/**
 * Where a grid reads its rows from: `/assets`, or any list answering in its shape and taking
 * `limit` and `offset`. `anchored` says the server can turn a row's id back into a position here,
 * so `from` is never sent to a list that would ignore it and answer page one.
 */
export interface RowSource {
	/** A real endpoint of this server's own schema, so a typo is a build error rather than a
	 *  screen that loads nothing. */
	path: ApiPath;
	anchored: boolean;
	/**
	 * The orders this list understands, or null for the whole vocabulary. The order is on the bar
	 * across every screen, so a wall offers only what its source accepts, never a refused word.
	 */
	sorts: readonly string[] | null;
	/**
	 * The orders this wall offers on top of the whole vocabulary, where `sorts` is null: the ones
	 * only a wall that asks for them offers (`WALL_ONLY_ORDERS`). The Favorites wall's two.
	 */
	adds?: readonly string[];
	/** The orders this wall leaves out of the whole vocabulary, since they would sort every row equal. */
	drops?: readonly string[];
	/**
	 * What an order is CALLED on this wall, where the file wall's words would name the wrong thing.
	 * A wall of marks orders `newest` by when each mark was made, so it says so.
	 */
	says?: Readonly<Record<string, string>>;
	/**
	 * Whether the query language reaches this list, or a sentence saying why it does not. True for
	 * the library, and for the wall of moments, which `/loops` filters with the same engine.
	 */
	filterable: true | string;
	/**
	 * WHAT EVERY ROW'S FILE IS on this list, in the query language, or nothing for the library. The
	 * bar counts in the FILE language, so a wall of marks says `loops: any` there, never to its own
	 * route.
	 */
	files?: Readonly<Record<string, string>>;
	/**
	 * Whether each row carries a name of its own, drawn under its tile: a mark's name is what tells
	 * two marks of one video apart. The line is part of every row's height (`CAPTION_HEIGHT`).
	 */
	captioned?: boolean;
	/** What the pager calls a row, where it is not a file. */
	noun?: string;
}

/**
 * A wall of MOMENTS: every stretch somebody marked, declared once for the Loops wall and the Loops
 * tab of a tag, a person or a site (filtered by `tag`, `person`, `site` in the grid's `query`).
 * The bar filters it further by its files' facets.
 */
export const LOOP_SOURCE: RowSource = {
	path: '/loops',
	/* `/loops` resolves a mark into its place in this same scoped, ordered, filtered list. */
	anchored: true,
	sorts: ['newest', 'oldest', 'edited', 'name_az', 'name_za', 'largest', 'smallest'],
	/* `newest`/`oldest` order by the mark's id, minted when it was made, and `largest`/`smallest`
	   by its length, so they are named that way here. */
	says: {
		newest: 'Recently created',
		oldest: 'Oldest created',
		largest: 'Longest',
		smallest: 'Shortest'
	},
	/* The bar's filters filter the marks to those cut from matching files; see `filterable`. */
	filterable: true,
	files: { loops: 'any' },
	captioned: true,
	noun: 'loops'
};

/** The ordinary one: the library, where a row is a file and its own id names it. */
export const ASSET_SOURCE: RowSource = {
	path: '/assets',
	anchored: true,
	sorts: null,
	filterable: true
};

/** The library as the Favorites wall reads it: every order, and the two about when a heart was
 *  pressed, which only this wall offers. Every file on it is hearted, which the bar is told the
 *  way the wall of moments says its files have a Loop (see `files`). */
export const FAVORITES_SOURCE: RowSource = {
	...ASSET_SOURCE,
	adds: FAVORITED_ORDERS,
	drops: ['favorite'],
	files: { fav: 'yes' }
};

/** Everything loaded so far, and the layout over it. */
export class Grid {
	/** Where the rows come from. See `RowSource`; the default is the library itself. */
	readonly source: RowSource;

	constructor(source: RowSource = ASSET_SOURCE) {
		this.source = source;
	}

	items = $state<GridItem[]>([]);
	total = $state(0);
	/**
	 * How big the files `total` counts are, in bytes (`total_bytes`), or null where not known,
	 * including after a file left the wall. The header says it beside the count.
	 */
	totalBytes = $state<number | null>(null);
	loading = $state(false);
	failed = $state<string | null>(null);

	/**
	 * Whether the question on screen has been answered, and the answer was nothing at all. No
	 * window size changes that, so a re-measure of an empty page does not ask again (which would loop).
	 */
	get answeredEmpty(): boolean {
		return !this.loading && this.failed === null && this.total === 0 && this.items.length === 0;
	}

	/** How many files the page on screen is actually drawing. The pager's range reads off it. */
	loaded = $state(0);

	/**
	 * Where the page on screen begins, counting from zero: held, since a page holds as many files
	 * as fill its rows and there is no page number. The pager and the next page read it.
	 */
	offset = $state(0);

	/**
	 * Where the page was ASKED to begin. Files arriving above it on a newest-first wall raise
	 * `offset`, and the difference is how many are new. Written only by a load somebody asked for.
	 */
	askedAt = $state(0);

	/**
	 * Which page is on screen, as the question and the place asked for, or null. The wall's
	 * entrance is keyed on it, so a catch-up never replays the arrival.
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

	/**
	 * How many files have arrived above the page on screen: derived from where the page now sits,
	 * so it cannot drift from what is shown.
	 */
	readonly newer = $derived(Math.max(0, this.offset - this.askedAt));

	/**
	 * The height the rows have to fit into: one screenful of the grid area, given by whatever draws
	 * the grid. The row height and page size come off this and the width.
	 */
	screenHeight = $state(0);

	/*
	 * Whether the server reached the end of what it was looking through. False only when a search
	 * by meaning stopped before filling the page, since it filters as well as ranks; otherwise a
	 * short page would look like an empty library.
	 */
	complete = $state(true);
	/** The filters on this page whose value could not be read. Each matches nothing, so a page
	 *  carrying one is empty for a reason the caption can say. */
	problems = $state<FilterProblem[]>([]);
	/** The username this page is filtered to, as the server named it: null on every page not filtered
	 *  to one, and on one filtered to a username this viewer may not be shown. What the bar's chip reads. */
	username = $state<NarrowedToUsername | null>(null);

	/*
	 * Which query the answers coming back belong to, so a request from an earlier question cannot
	 * overwrite the grid.
	 */
	#generation = 0;
	#quietOwed = false;

	containerWidth = $state(0);
	/**
	 * Null until somebody moves the size control; then it overrides the breakpoint. A window onto
	 * the one shared choice (`gridSize`), so every grid follows the slider and the choice outlives
	 * the screen. Local, not the account's: it is about this window, and a late server value would
	 * lay the grid out twice.
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
	 * Whether the screen asked for the files a product gave up on, so every row carries WHY. The
	 * screen says so before the first page, so the line under each tile is laid out from the start.
	 */
	leftOutAsked = $state(false);

	/* Whether any row on hand says why it was left out: the typed `left_out:` is free text the
	   client may not take apart, so the rows say it. */
	readonly #rowsSayWhy = $derived(this.items.some((item) => Boolean(item.left_out)));

	/**
	 * The line under every row's tiles, or nothing on a wall whose rows are files. A wall of files
	 * takes it too where its rows say why each was left out, and it is part of every row's height.
	 */
	get caption(): number {
		return this.source.captioned || this.leftOutAsked || this.#rowsSayWhy ? CAPTION_HEIGHT : 0;
	}

	/** True while the page on screen was filled BACKWARDS. Declared before `filled`, which reads it. */
	#fromEnd = $state(false);

	/** The height the size control asked for, before it is made to divide the screen evenly. */
	readonly targetRowHeight = $derived(rowHeightFor(gridSize.step, this.containerWidth));

	/**
	 * The height rows are actually drawn at: the chosen size nudged to divide one screenful into
	 * whole rows, so no dead strip or half row shows (`snapRowHeight`).
	 */
	readonly rowHeight = $derived(
		snapRowHeight(this.targetRowHeight, this.screenHeight, this.gutter, this.caption)
	);

	/** How many whole rows one screenful holds, a captioned row counted with its line. */
	readonly rowsPerScreen = $derived(
		rowsThatFit(this.screenHeight, this.rowHeight + this.caption, this.gutter)
	);

	/**
	 * How many rows one PAGE holds: a whole number of screenfuls, brought down towards one where a
	 * page could not be fetched in `MAX_FETCHES` requests.
	 */
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
	 * The page, laid out: exactly `pageRows` complete rows. Files fetched past the last row are the
	 * next page's and are not drawn; asking generously is what makes every page the same shape.
	 * One row left at the end of the list is drawn here, never as a page of its own.
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

	/**
	 * The same tiles as one flat list, each knowing where it sits: what the wall renders from, so a
	 * tile changing row is moved, not rebuilt (`wallOf`).
	 */
	readonly wall = $derived(wallOf(this.rows, this.gutter, this.caption));

	/** Where the NEXT page begins. The surplus this page did not draw is the next page's first row. */
	readonly nextOffset = $derived(this.offset + this.filled.used);

	/* The items by id, so a tile is not a linear scan of the whole page on every render. */
	readonly byId = $derived(new Map(this.items.map((item) => [item.id, item])));

	/**
	 * Record what the server ended up holding for one item's heart and stars, so a re-render does
	 * not redraw the tile from the page it was fetched with.
	 */
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

		/*
		 * A row the screen is not about any more leaves immediately (a heart taken off on Favorites).
		 * Only the screen can judge that, so it passes the test; the total comes down with the row.
		 */
		if (!stillBelongs) return;
		const updated = this.byId.get(id);
		if (updated && !stillBelongs(updated)) {
			this.items = this.items.filter((item) => item.id !== id);
			this.total = Math.max(0, this.total - 1);
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
	 * One block of the same list, read WITHOUT changing anything on this grid: for a run that has
	 * played past the page on screen, which must not move under the panel. Here because this class
	 * knows the source and the page's shape. Empty on any failure: the end of the list and a failed
	 * request both mean nothing more to play.
	 */
	async blockAt(query: WallQuery, offset: number, limit: number): Promise<GridItem[]> {
		try {
			return await this.#block(query, offset, limit);
		} catch {
			return [];
		}
	}

	/* The same read, letting a failure through: selecting a whole list must not take a failed
	 * request halfway as the end and hand back half of what was asked for. */
	async #block(query: WallQuery, offset: number, limit: number): Promise<GridItem[]> {
		const answer = await api.get<AssetPage>(this.source.path, {
			query: { ...query, offset, limit: Math.min(limit, SERVER_PAGE_CAP) }
		});
		return answer.items;
	}

	/**
	 * Every ROW this query matches, up to `MOST_SELECTED`, without moving the wall: what "select all"
	 * means. Throws rather than return a short answer, which would look like a small library.
	 */
	async everyRow(query: WallQuery): Promise<GridItem[]> {
		const rows: GridItem[] = [];
		// Up to the reported count plus one block, which tells an exact multiple of the page size
		// from a list that goes on; capped at `MOST_SELECTED`.
		const most = Math.min(this.total + SERVER_PAGE_CAP, MOST_SELECTED);
		for (let at = 0; at < most; at += SERVER_PAGE_CAP) {
			const block = await this.#block(query, at, Math.min(SERVER_PAGE_CAP, MOST_SELECTED - at));
			for (const one of block) rows.push(one);
			if (block.length < SERVER_PAGE_CAP) break;
		}
		return rows.slice(0, MOST_SELECTED);
	}

	/** A page somebody asked for has landed: where it begins, and what the wall is now showing. */
	#show(query: WallQuery, offset: number): void {
		this.askedAt = offset;
		this.showing = `${JSON.stringify(query)}\n${offset}`;
	}

	/** Where one row sits in this query, or null when the query does not hold it (a wall of
	   moments answers null: its rows are moments, not files, so a file keeps its stand-in place). */
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

	/**
	 * Load the page that begins here, replacing what is shown. A loop, because how many files fill
	 * a row depends on their shapes; the generation check keeps an older answer from being written.
	 */
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
		/*
		 * WHAT HAS ALREADY BEEN COLLECTED, SO A FILE CANNOT BE TAKEN TWICE.
		 *
		 * A search by meaning answers a deeper request from a larger set of neighbours, so two blocks
		 * of one fill can overlap, and a repeated id in the keyed wall throws and freezes the render.
		 * Refused here: the honest page is the rest of what came back.
		 */
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

				/*
				 * Where this request reads from: backwards, the block just before what is held,
				 * clamped because the first block can be shorter than the ask.
				 */
				const size = Math.min(wanted, SERVER_PAGE_CAP);
				const at = fromEnd
					? Math.max(0, endAt - collected.length - size)
					: began + collected.length;

				const asked: Record<string, string | number | boolean | readonly string[]> = {
					...query,
					limit: size
				};
				// The anchor is for the FIRST request only. A forward top-up continues after the
				// last row held where the order allows: one index seek, however deep the page.
				const last = collected[collected.length - 1];
				if ('from' in start && attempt === 0) {
					asked.from = start.from;
					if (start.near !== undefined && start.near !== null) asked.near = start.near;
				} else if (!fromEnd && attempt > 0 && last !== undefined && continuable(query)) {
					asked.after = last.id;
				} else asked.offset = at;

				const answer = await api.get<AssetPage>(this.source.path, { query: asked });
				if (generation !== this.#generation) return;

				known = {
					total: answer.total,
					totalBytes: bytesOf(answer),
					complete: answer.complete ?? true,
					problems: answer.problems ?? [],
					username: answer.username ?? null
				};
				if ('from' in start && attempt === 0) began = answer.offset;
				/* The count is said as each answer lands, so it never describes the last question;
				   not `offset`, which describes the tiles still on screen. */
				this.total = known.total;
				this.totalBytes = known.totalBytes;
				const fresh = answer.items.filter((item) => !taken.has(item.id));
				for (const item of fresh) taken.add(item.id);
				if (fromEnd) collected.unshift(...fresh);
				else collected.push(...fresh);

				// A short answer from the SERVER is the end, in either direction; a full block that
				// was all duplicates is not.
				if (answer.items.length < size) break;
				if (fromEnd && at === 0) break;
				// The first block is drawn as it lands; the rows that top the page up follow it.
				this.#drawFirst({ attempt, fromEnd, quiet: options.quiet }, query, collected, began, known);
			}

			if (generation !== this.#generation) return;

			/*
			 * A PAGE PAST THE END, reached by a file that is gone: `near` can be the length of the
			 * list, which answers no files. Step back to the last page, as `CardPaging` does. A
			 * count of 0 there may be a windowed count, so one file from the front says the real one.
			 */
			if ('from' in start && collected.length === 0 && began > 0) {
				if (known.total <= 0) {
					const front = await api.get<AssetPage>(this.source.path, {
						query: { ...query, limit: 1, offset: 0 }
					});
					if (generation !== this.#generation) return;
					this.total = front.total;
					this.totalBytes = bytesOf(front);
				}
				await this.loadAt(query, this.total > 0 ? { last: true } : { at: 0 }, options);
				return;
			}

			/*
			 * Where the page actually begins: reading backwards, the surplus sits at the FRONT
			 * (`skipped`), or the range would describe the fetch instead of the screen.
			 */
			const base = fromEnd ? Math.max(0, endAt - collected.length) : began;
			const landed =
				base +
				(fromEnd
					? fillRowsFromEnd(collected, {
							containerWidth: this.containerWidth,
							targetHeight: this.rowHeight,
							gutter: this.gutter,
							rows: this.pageRows
						}).skipped
					: 0);

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
			/*
			 * Nothing on screen has changed, so change nothing on screen: a new equal array would
			 * redraw every tile. Most catch-ups take this free path. The TOTAL is not compared (it
			 * moves every second of an import) but taken; nor the OFFSET, which an anchored catch-up
			 * moves by design and which the layout does not depend on.
			 */
			if (this.#fromEnd === fromEnd && this.#same(answer)) {
				this.offset = answer.offset;
				// A page asked for resets the drift mark even when it holds the same files.
				if (!options.quiet) this.#show(query, answer.offset);
				this.total = known.total;
				this.totalBytes = known.totalBytes;
				this.complete = known.complete;
				this.problems = known.problems;
				this.username = known.username;
				return;
			}
			this.#fromEnd = fromEnd;
			this.items = collected;
			this.offset = answer.offset;
			if (!options.quiet) this.#show(query, answer.offset);
			this.loaded = collected.length;
			this.total = known.total;
			this.totalBytes = known.totalBytes;
			this.complete = known.complete;
			this.problems = known.problems;
			this.username = known.username;
		} catch (error) {
			if (generation !== this.#generation) return;
			// A quiet load that failed leaves the screen as it was: a few seconds old beats blank.
			if (options.quiet) return;
			this.items = [];
			this.failed = error instanceof Error ? error.message : 'That did not work.';
		} finally {
			if (generation === this.#generation) {
				this.loading = false;
				this.reading = false;
				this.filling = false;
				this.#payOwed(query, options);
			}
		}
	}

	/* A page's first block on screen before its top-up is read: the old page leaves on one answer. */
	#drawFirst(
		read: { attempt: number; fromEnd: boolean; quiet?: boolean },
		query: WallQuery,
		collected: readonly GridItem[],
		began: number,
		known: {
			total: number;
			totalBytes: number | null;
			complete: boolean;
			problems: FilterProblem[];
			username: NarrowedToUsername | null;
		}
	): void {
		if (read.attempt > 0 || read.fromEnd || read.quiet || collected.length === 0) return;
		this.#fromEnd = false;
		this.items = [...collected];
		this.offset = began;
		this.#show(query, began);
		this.loaded = collected.length;
		this.total = known.total;
		this.totalBytes = known.totalBytes;
		this.complete = known.complete;
		this.problems = known.problems;
		this.username = known.username;
		this.loading = false;
		this.filling = true;
	}

	/*
	 * Reading backwards to a boundary: `last` ends at the end of the library, a page back ends
	 * where this page begins. `last` before any load falls through to the beginning.
	 */
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

	/**
	 * How many more files this page needs, or zero when it is full: laid out from what arrived, and
	 * estimated from the shapes in hand, which is why a second round is usually enough.
	 */
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

	/* What the page still needs, or else the files left after it when they are a row or less,
	   fetched so `filled` can draw them on this page. */
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

	/*
	 * Whether the fresh answer holds the same files, in the same order, saying the same things.
	 * Not the total, which an import moves. The picture is part of what a tile says (`art`,
	 * `preview`), or a clip built on request would be thrown away here as nothing new.
	 */
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

	/* Take one item out, because it is not in the library any more: the optimistic echo of a
	 * delete, with the total and `loaded` following; the next load reconciles. */
	forget(id: string): void {
		const remaining = this.items.filter((item) => item.id !== id);
		if (remaining.length === this.items.length) return;
		this.items = remaining;
		this.loaded = remaining.length;
		this.total = Math.max(0, this.total - 1);
		this.totalBytes = null;
	}
}
