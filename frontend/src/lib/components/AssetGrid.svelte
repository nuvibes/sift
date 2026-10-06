<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * The grid, which for most people most of the time is Sift: a wall that stays fluid while
	 * somebody scrolls past thousands of files. Rows are justified (`justify.ts`), the question and
	 * its order are `WallOrder`, and what the tiles draw and play is `WallMedia`. A component rather
	 * than a page, because Browse, Favorites, a collection and a person are all walls of files, and
	 * differ in one query and the sentence for an empty wall.
	 */
	import type { Snippet } from 'svelte';
	import { untrack } from 'svelte';
	import { page as address } from '$app/state';
	import Tile from '$lib/components/Tile.svelte';
	import TileControls from '$lib/components/TileControls.svelte';
	import { startAssign } from '$lib/components/common/drag-assign.svelte';
	import { anchorIn, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { WalkBack } from '$lib/grid/walk-back';
	import { openAsset, type Continues, type RunOrder } from '$lib/player/asset-view';
	import { toasts } from '$lib/shell/toasts.svelte';
	import {
		ActionBar,
		Button,
		Empty,
		ContextMenu,
		FileVerbs,
		Problem,
		Selection,
		TILE_ID,
		TileGesture,
		Tooltip,
		VerbButtons,
		VerbMenuItems,
		VerbMore
	} from '$lib/components/common';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { wallHeld } from '$lib/components/entity/TabLayer.svelte';
	import type { IconName } from '$lib/design/icons';
	import HiddenDialog from '$lib/components/HiddenDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
	import { coverage } from '$lib/jobs/semantic-runs.svelte';
	import { libraryChanges, onAssetStateChange, whenChanged } from '$lib/library/changes.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { swapMode } from '$lib/swap/mode.svelte';
	import { sayFileRefused } from '$lib/swap/refused';
	import { SwapSelection } from '$lib/swap/sweep.svelte';
	import { live, liveWords } from '$lib/shell/live.svelte';
	import { capture } from '$lib/capture/capture.svelte';
	import { dragOut } from '$lib/capture/copy-out';
	import { imports } from '$lib/library/imports.svelte';
	import ImportSkeleton from '$lib/components/ImportSkeleton.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import {
		ASSET_SOURCE,
		Grid,
		LOOP_SOURCE,
		stretchOf,
		type GridItem,
		type PageStart,
		type RowSource
	} from '$lib/grid/grid.svelte';
	import { barShape } from '$lib/components/common/verbs';
	import { withoutVerbs, type Verb, type VerbId } from '$lib/grid/verbs';
	import { RANDOM } from '$lib/grid/sort-state.svelte';
	import { asksWhyLeftOut } from '$lib/library/left-out';
	import { WallOrder } from './wall-order.svelte';
	import { WallMedia, fileArriving, stillFor, stillMissing } from './wall-media.svelte';
	import { WallCatchUp } from './wall-catch-up.svelte';

	interface Props {
		/** What to ask the server for. Everything the screen means, and nothing about how it looks. */
		query: Record<string, string>;
		/** The screen's own heading. */
		title: string;
		/** What to say when the answer is empty: only the screen knows which fact that is. */
		empty: string;
		/** Something to offer under that sentence, for a screen whose empty state has an answer. */
		emptyAction?: Snippet;
		/** Anything else that belongs in the toolbar, for a screen with a control of its own. */
		tools?: Snippet;
		/** A sentence under the title, for a screen whose name does not explain it. */
		lede?: Snippet;
		/** A passing fact for the header's status place, beside the wall's own lines there. */
		notice?: Snippet;
		/** A band before the title naming the page (a person's identity), fixed furniture in the
		 *  frame's header so the page keeps one scrolling region. */
		above?: Snippet;
		/** A band after the title that belongs to the WALL (Browse's folder view), same rules. */
		beneath?: Snippet;
		/** What right-clicking the wall's empty ground offers, as `ContextMenuItem`s. */
		background?: Snippet;
		/** A control that belongs to the TITLE, drawn right after it. See `PageHeader.beside`. */
		beside?: Snippet;
		/** The trail above the title, drawn by `PageFrame`; one crumb draws nothing. */
		crumbs?: Crumb[];
		/** Let `beside` name the section instead of the title. See `PageHeader.titleHidden`. */
		titleHidden?: boolean;
		/** An order this screen IS (Recently viewed), so no orders are offered to the bar. */
		fixedSort?: string;
		/** Extra `ContextMenuItem`s for one tile, handed the tile and a way to take its row off. */
		menuExtra?: Snippet<[GridItem, { forget: (id: string) => void }]>;
		/** `menuExtra` draws its rows in their own groups. See `VerbMenuItems.extraGrouped`. */
		menuExtraGrouped?: boolean;
		/** Verbs only this wall has, on the bar and the menu, handed ROWS: two marks of one video
		 *  are two things to forget. */
		extraVerbs?: (rowIds: string[], grid: { forget: (id: string) => void }) => Verb[];
		/** Where the rows come from, when not the library itself. See `RowSource`. */
		source?: RowSource;
		/** The screen's constraint in the query language, for the bar, where `query` is spelled in
		 *  a route's own words. */
		filesQuery?: Record<string, string>;
		/** Where the wall's box keeps its words when that is not `q`, for the bar's chip. */
		words?: string;
		/** Verbs this screen does not offer, as a subtraction from the one list. */
		hideVerbs?: readonly VerbId[];
		/** Whether files here can be kept at the top: a wall somebody curates. See `FileVerbs.pinnable`. */
		pinnable?: boolean;
		/** Which heading the title is: `2` under something that already named the page. */
		titleLevel?: 1 | 2;
		/** The rail's glyph for this screen. Absent when the grid is embedded under something else. */
		icon?: IconName;
		/** Told what this wall found, for a tab strip that draws the number from this very listing. */
		oncount?: (total: number) => void;
	}

	let {
		query,
		title,
		empty,
		emptyAction,
		tools,
		lede,
		notice,
		above,
		beneath,
		background,
		beside,
		crumbs,
		titleHidden = false,
		fixedSort,
		titleLevel = 1,
		icon,
		oncount,
		menuExtra,
		menuExtraGrouped = false,
		extraVerbs,
		source = ASSET_SOURCE,
		filesQuery,
		words,
		hideVerbs = [],
		pinnable = false
	}: Props = $props();

	/* Whether this screen is the inside of Hidden. Only `hidden=true` reaches concealed rows at all,
	 * so it is the one honest signal that everything drawn here is already hidden. */
	const showingHidden = $derived(query.hidden === 'true');

	/* Why one file is hidden, from the crossed-out eye on its tile: its own panel, because the
	   sharing sheet answers who else may see it, not what keeps it off this screen. */
	let hiddenAbout = $state<ShareTarget | null>(null);
	let hiddenOpen = $state(false);

	function askAboutHidden(id: string) {
		// By its filename: a ULID tells nobody which file they pressed.
		hiddenAbout = { type: 'item', id, label: grid.byId.get(id)?.original_filename ?? 'this file' };
		hiddenOpen = true;
	}

	const grid = new Grid(source);

	/* The files a product gave up on, asked for by a link (the Importing pane's "24 files"): the
	   wall takes the line under each tile saying why, from its first page. See `asksWhyLeftOut`.
	   Before the load effect below, so the first page is measured with the line already in it. */
	$effect.pre(() => {
		grid.leftOutAsked = asksWhyLeftOut(query);
	});

	/* The FILE a row is about: the row itself, or on a wall of moments the video it was cut from,
	   while the row keeps its own identity so two moments of one video are two tiles. */
	function fileOf(item: GridItem): string {
		return item.asset_id ?? item.id;
	}

	/* The verb host takes FILE ids and this screen holds rows: a heart set on a loop must land on
	   its video, and a deleted file must take every mark of it off the screen. */
	/** Every row on screen that is about one file. More than one only on a wall of moments. */
	function rowsAbout(fileId: string): string[] {
		return grid.items.filter((each) => fileOf(each) === fileId).map((each) => each.id);
	}

	/** The rows a set of files covers, which is what a verb's answer has to be applied to. */
	function rowFor(fileId: string): GridItem | undefined {
		return grid.items.find((each) => fileOf(each) === fileId);
	}

	/* The verbs this screen offers, applied to the bar and the menu from one place. */
	function offered(all: Verb[]): Verb[] {
		return withoutVerbs(all, hideVerbs);
	}

	/* Whether to say why this wall may not be keeping up. See the header's `status` snippet. */
	const liveLine = $derived(liveWords(live.standing));

	/** The frame's scrolling body, handed over by `PageFrame` so this can reset it and read its height. */
	let scroller = $state<HTMLElement | null>(null);
	/** The box INSIDE it that the rows are laid out in, which is the one carrying the page's inset. */
	let contentBox = $state<HTMLElement | null>(null);

	/* Picking several things: holding a tile starts a selection, and from then on a click adds and
	   removes; Ctrl and Shift work from the first click. Clearing it is the way out. */
	const selection = new Selection();
	const selecting = $derived(selection.count > 0);
	/* The press-and-click rules, shared with every other screen made of tiles. See TileGesture:
	 * the long-press-then-click interaction is the part worth having in one place. */
	const gesture = new TileGesture(selection, () => grid.items.map((each) => each.id));

	/* Swap mode's run of the same gesture picks FILES for the swap (`$lib/swap/sweep.svelte`). */
	const swapPicks = new SwapSelection(() =>
		grid.items.map((item) => ({
			id: item.id,
			file: fileOf(item),
			name: item.original_filename ?? 'A file',
			concealed: Boolean(item.concealed),
			refused: refusedInSwaps(item)
		}))
	);
	/* Whether no swap will send this file: the tile's own fact (`swap_refused`, Kept local or "Don't
	   swap" on it or above it). */
	function refusedInSwaps(item: GridItem): boolean {
		return item.swap_refused === true;
	}
	const swapGesture = new TileGesture(swapPicks, () => grid.items.map((each) => each.id));
	const swapping = $derived(swapMode.on && session.isAdmin);

	const pressStart = (id: string, event: PointerEvent) => {
		if (!swapping) {
			gesture.pressStart(id, event);
			return;
		}
		// What the swap holds now, before the press measures a run against it: a pick taken out
		// in the drawer since the last press must not be painted back.
		swapPicks.takeUp();
		swapGesture.pressStart(id, event);
	};
	const pressEnd = () => {
		gesture.pressEnd();
		swapGesture.pressEnd();
	};
	const tileClicked = (id: string, event: MouseEvent) => gesture.clicked(id, event);

	/* In swap mode a press picks the tile's file for the swap rather than opening it. The swap's
	   gesture sees the click first; a Hidden tile is not picked, as the server would leave it out. */
	function swapClicked(item: GridItem, event: MouseEvent): void {
		// A file that will not go is never picked: the press says why, with the way to change it.
		if (refusedInSwaps(item)) {
			event.preventDefault();
			event.stopPropagation();
			void sayFileRefused(fileOf(item));
			return;
		}
		swapGesture.clicked(item.id, event);
		const taken = event.defaultPrevented;
		event.preventDefault();
		event.stopPropagation();
		if (taken || item.concealed) return;
		swapPicks.toggle(item.id);
	}

	/* Hearts and stars set elsewhere land at once; `stillBelongs` drops a row that no longer fits. */
	onAssetStateChange((state) => {
		// By FILE: a heart reaches every row about it, several on a wall of moments.
		for (const row of rowsAbout(state.asset_id)) {
			grid.setState(
				row,
				{ favorite: state.favorite, rating: state.rating, views: state.views },
				stillBelongs
			);
		}
		// A file this wall does not hold may now answer it: Favorites gaining a heart.
		if (order.hangsOnOpinions) void current.catchUp();
	});

	/* Escape lets go of a selection from wherever the focus is, on the window, and only while
	 * something is picked, since every dialog and menu wants Escape too. */
	function onEscape(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED; no data changes. See `TileGesture.undoKeys`.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}

	/* The file each picked row belongs to, for rows beyond the loaded page: on a wall of moments a
	   whole-query pick would otherwise send mark ids to routes that take file ids. A plain `let`,
	   filled before the pick that moves the screen. See `picked`. */
	let filesBeyond = new Map<string, string>();

	/* Pick every file the question matches, not just the loaded page, without moving the wall.
	 * Through `pickWholeQuery`, so every reader knows the pick reaches past the screen. A failure
	 * picks nothing and says so: half a library picked silently is the outcome to refuse. */
	async function pickTheWholeQuery(): Promise<void> {
		try {
			const rows = await grid.everyRow(order.fullQuery);
			filesBeyond = new Map(rows.map((each) => [each.id, fileOf(each)]));
			selection.pickWholeQuery(rows.map((each) => each.id));
		} catch {
			toasts.show("Sift couldn't read the whole list, so nothing was picked", {
				tone: 'error'
			});
		}
	}

	/* Forget anything that has left the grid, so a verb never acts on rows nobody can see. */
	$effect(() => {
		const ids = grid.items.map((each) => each.id);
		untrack(() => selection.retain(ids));
	});

	/* The question this wall asks and its order. See `WallOrder`. */
	const order = new WallOrder({
		query: () => query,
		source: () => source,
		fixedSort: () => fixedSort,
		pinnable: () => pinnable
	});

	/* Keeping the page current in place, and what the tiles draw and play. See `WallCatchUp` and
	   `WallMedia`. */
	const current = new WallCatchUp({
		grid,
		order,
		source: () => source,
		media: () => media,
		remember: () => rememberWhereWeAre()
	});
	const media = new WallMedia(grid, fileOf, () => current.letThemIn());

	/* What this account may see has moved: re-read in place, not reset to the top. */
	whenChanged(libraryChanges, () => void current.catchUp());

	/* Where the page on screen begins: a new question goes back to the start, a change holds it. */
	let start = $state<PageStart>({ at: 0 });
	const walk = new WalkBack();

	/* How much of the library a search by meaning can reach, asked only by a wall drawing one. */
	$effect(() => {
		if (order.describedMatters) void coverage.load();
	});

	const mine = Symbol('asset-grid');
	const leaving = wallHeld();

	/* What this wall is, in the words the bar reads; the rows' route is asked `query`. */
	const barQuery = $derived<Record<string, string>>({
		...(source.files ?? {}),
		...(filesQuery ?? query)
	});

	$effect(() => {
		if (leaving()) return;
		screenBar.publish(mine, {
			query: barQuery,
			/* What every row here is by definition (a Loop, a heart): one value, so no column. */
			fixed: Object.keys(source.files ?? {}),
			count: grid.loaded > 0 || !grid.loading ? grid.total : undefined,
			filterable: source.filterable,
			/* The username `?username=` filters to, as the server named it: the bar's chip for that
			   parameter reads it, because the id in the address says nothing anybody can read. */
			username: grid.username,
			words,
			sorts: fixedSort ? [] : order.sortOptions,
			sort: order.sort,
			onSort: (next) => order.choose(next),
			resizable: true,
			playable: true
		});
	});

	$effect(() => () => screenBar.release(mine));

	/* Whether the anchor in the address is ours to honour: only on arrival at a link. */
	let arriving = true;

	/*
	 * A different question starts at the beginning. A library change is not one (it is served by
	 * `catchUp`), and `vault.generation` is not read: the vault rings `libraryChanges` both ways.
	 */
	$effect(() => {
		void order.asked;
		void order.askedOrder;
		untrack(() => {
			walk.forget();
			// Arriving at a link: the address named a file and it belongs to this question.
			const named = arriving && source.anchored ? anchorIn(address.url) : null;
			arriving = false;
			if (named) {
				start = { from: named.from, near: named.near };
				return;
			}
			start = { at: 0 };
			// ...and the stale one comes out of the address, or a link copied from the bar would
			// carry a position belonging to a question nobody is asking.
			forgetTheAnchor();
		});
	});

	/* Load the page showing whenever its start, the question or how many rows a page holds changes;
	   the last is what makes a resize fetch more from the same place. The load is untracked, or it
	   would depend on what it writes. The last load's question and start tell a new measure from a
	   new question (`Grid.answeredEmpty`). */
	let loadedFor = '';
	let loadedFrom: PageStart | null = null;

	$effect(() => {
		void order.asked;
		void order.askedOrder;
		void grid.pageRows;
		const at = start;
		untrack(() => {
			const question = `${order.asked}\n${order.askedOrder}`;
			const measureOnly = question === loadedFor && at === loadedFrom;
			loadedFor = question;
			loadedFrom = at;
			// A page that answered nothing answers nothing at every size.
			if (measureOnly && grid.answeredEmpty) return;
			void grid.loadAt(order.fullQuery, at).then(rememberWhereWeAre);
			// Back to the top for a new page: its previous scroll position means nothing.
			if (scroller) scroller.scrollTop = 0;
		});
	});

	/* The address's name for the file a page starts at, shared with the walls of cards
	   (`$lib/grid/anchor`), whose path guard keeps a refresh from leaving an open file. */
	function forgetTheAnchor() {
		if (!source.anchored) return;
		forgetAnchor(address.url, path);
	}

	function rememberWhereWeAre() {
		// Only where the server can turn an id back into a position in this list.
		if (!source.anchored) return;
		rememberAnchor(address.url, path, grid.items[0]?.id, grid.offset);
	}

	/** The route this grid belongs to, captured once so a navigation away can be told from a reload. */
	const path = address.url.pathname;

	function turnTo(next: PageStart) {
		walk.forget();
		start = next;
	}

	function measure() {
		if (!scroller || !contentBox) return;
		/* A box with no size yet is the frame before layout, not a measurement. */
		if (scroller.clientHeight === 0 || contentBox.clientWidth === 0) return;
		/* The inset content box: `clientWidth` includes padding and excludes the scrollbar. */
		const style = getComputedStyle(contentBox);
		const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
		const vertical = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
		grid.containerWidth = Math.max(0, contentBox.clientWidth - padding);
		/* One screenful, from the scrolling box: a page holds a whole number of these. */
		grid.screenHeight = Math.max(0, scroller.clientHeight - vertical);
	}

	/* Re-measure whenever the boxes change: a scrollbar arriving or the rail collapsing moves the
	   width without a window resize. */
	function watchSize(parts: { scroller: HTMLElement; content: HTMLElement }): () => void {
		scroller = parts.scroller;
		contentBox = parts.content;
		measure();
		if (typeof ResizeObserver === 'undefined') return () => {};
		// Both, because they change for different reasons: the scroller when the window or the rail
		// does, the inset box when the frame's own padding responds to a breakpoint.
		const observer = new ResizeObserver(() => measure());
		observer.observe(parts.scroller);
		observer.observe(parts.content);
		return () => observer.disconnect();
	}

	/* What is on screen, in order, for the file about to be opened over it. */
	function neighbours() {
		return grid.items.map(asNeighbour);
	}

	/* One row of the wall as one step of a run, for the page in hand and the blocks past it. */
	function asNeighbour(each: GridItem) {
		return {
			id: fileOf(each),
			runs: each.media_type === 'video' || each.media_type === 'gif'
		};
	}

	/* How a run reads past the end of the page it was opened from, through the same query and
	   source, and never through `grid.loadAt`, which would page the wall behind the panel. Shuffle
	   reads the same list under the run's seed, less the pin. */
	function runReader(): Continues {
		return {
			from: grid.offset,
			total: grid.total,
			shuffles: order.offers(RANDOM),
			locate: (id: string) => grid.positionOf(order.fullQuery, id),
			async fetch(offset: number, limit: number, by?: RunOrder) {
				const query = by === undefined ? order.fullQuery : inShuffle(by.seed);
				return (await grid.blockAt(query, offset, limit)).map(asNeighbour);
			}
		};
	}

	/* This screen's query, arranged by one seeded shuffle instead of its own order. */
	function inShuffle(seed: number) {
		const { pinned_first: _pinned, ...rest } = order.fullQuery;
		return { ...rest, sort: RANDOM, seed: String(seed) };
	}

	/* Open what this tile is a picture of, over this screen. A row that is a STRETCH opens as that
	   stretch on repeat; a row that is its file names none, so it opens as the video it is. */
	function openRow(item: GridItem) {
		const { at, until } = stretchOf(item);
		/* Which Loop, on the wall of them, so the sitting can say it was opened from it. */
		const loop = source.path === LOOP_SOURCE.path && item.asset_id ? item.id : null;
		openAsset(fileOf(item), neighbours(), at, until, runReader(), loop);
	}

	/* What a right-click acts on: the selection when the tile is in it, the tile alone otherwise. */
	function targetRows(id: string): string[] {
		return selection.has(id) ? selection.ordered(grid.items.map((each) => each.id)) : [id];
	}

	/** What this wall offers that the shared set does not, for ROWS (see `extraVerbs`), appended
	 *  after `offered` so `hideVerbs` cannot reach them. */
	function wallVerbs(rows: string[]): Verb[] {
		return extraVerbs?.(rows, { forget: (id) => grid.forget(id) }) ?? [];
	}

	/* The files a set of rows is about, in order and without repeats: two marks of one video must
	   not ask the server to act on it twice. */
	function picked(rows: string[]): string[] {
		const files: string[] = [];
		for (const row of rows) {
			const known = grid.byId.get(row);
			/* Off the page where it can, off the whole-query read where it cannot. */
			const file = known
				? fileOf(known)
				: ((selection.beyondScreen ? filesBeyond.get(row) : undefined) ?? row);
			if (!files.includes(file)) files.push(file);
		}
		return files;
	}

	/* Whether a row still answers this screen's question: only `fav`, the screen's own yes or no.
	   Anything in the query language is left for the next load, wrong in the safe direction. */
	function stillBelongs(item: { favorite: boolean }): boolean {
		if (query.fav === 'yes') return item.favorite;
		if (query.fav === 'no') return !item.favorite;
		return true;
	}

	/* Say what was found, once the page has really landed. Guarded on `loading` so a screen is never
	   told the previous wall's number while this one is still on its way. */
	$effect(() => {
		if (!grid.loading) oncount?.(grid.total);
	});
</script>

<svelte:window onresize={measure} onkeydown={onEscape} />
<!-- Back in front. Whatever was owed while this window was behind another one, minimised, or a tab
     in the background is asked for now. See `WallCatchUp.payWhatIsOwed`. Visibility rather than focus: a
     window sitting beside the one being used is still visible and has been re-reading all along. -->
<svelte:document onvisibilitychange={() => current.payWhatIsOwed()} />

<!-- Every verb and every sheet behind it comes from the one host shared with the file's own screen. -->
<FileVerbs
	items={grid.items}
	{showingHidden}
	{pinnable}
	around={{
		lookup: (id) => rowFor(id),
		selection,
		setState: (id, state, keep) => {
			for (const row of rowsAbout(id)) grid.setState(row, state, keep);
		},
		forget: (id) => {
			for (const row of rowsAbout(id)) grid.forget(row);
		},
		/* A re-read for a verb that changes where a row belongs: the pin. Quiet, like the live feed's. */
		refresh: () => void current.catchUp(),
		stillBelongs,
		showingHidden: () => showingHidden
	}}
>
	{#snippet children(verbs)}
		<!-- The wall, declared here: `verbs` is `FileVerbs`'s parameter, and `PageFrame` has no `wall`. -->
		{#snippet wall()}
			<!-- Files on their way in, before anything arrived: a shimmer, named only for files handed
			     over through the interface, which came with a name. -->
			{#if capture.imports.length > 0}
				<div class="row importing" style:height="{grid.rowHeight}px" style:gap="{grid.gutter}px">
					{#each capture.imports as item (item.id)}
						<div class="slot" style:width="{grid.rowHeight}px" style:height="{grid.rowHeight}px">
							<ImportSkeleton name={item.name} />
						</div>
					{/each}
				</div>
			{/if}

			<!-- Said when the server stopped before filling the page: a tight filter by meaning can cut
			     the closest files away, and an empty page would read as files gone. -->
			{#if !grid.complete && !grid.loading}
				<p class="stopped" role="status">
					Sift stopped looking after the closest few thousand files — add a filter to reach further.
				</p>
			{/if}
			<!-- How much of the library a search by meaning could come from, while some is left to
			     describe; scoped to this account, so it says nothing about anything hidden. -->
			{#if order.describedMatters && !grid.loading && coverage.library > 0 && coverage.described < coverage.library}
				<p class="described">
					Sift has described {coverage.described.toLocaleString()} of {coverage.library.toLocaleString()}
					files so far.
				</p>
			{/if}
			<!-- A filter whose value could not be read matches nothing, and the page says which and why
			     rather than captioning an empty wall as if the library held no such file. Keyed by
			     position too: the same bad term twice is two equal problems. -->
			{#each grid.problems as problem, at (`${at}:${problem.field}:${problem.value}`)}
				<Problem message="{problem.field}:{problem.value} couldn't be read — {problem.reason}." />
			{/each}

			{#if grid.failed}
				<p class="note">{grid.failed}</p>
			{:else if grid.items.length === 0 && !grid.loading && capture.imports.length === 0}
				<!-- The wall IS the screen, so its empty state is a page's: the glyph, the sentence
				     and the one action that ends it. -->
				<Empty scope="page" {icon} action={emptyAction}>{empty}</Empty>
			{:else}
				<!-- ONE keyed each over every tile, so a tile that changes row keeps its element and its
				     `<img>`: a newest-first wall re-rows behind every new file. See `wallOf`. -->
				<div class="wall" style:height="{grid.wall.height}px">
					{#each grid.wall.tiles as placed (placed.id)}
						{@const item = grid.byId.get(placed.id)}
						{#if item}
							<!-- Draggable: the drag takes the FILE out where the desktop client can, and otherwise
							     drops the tile on a tag, a person or a collection, each also a menu verb. -->
							<div
								{...{ [TILE_ID]: item.id }}
								{@attach (element) => media.observe(element, item.id)}
								class="placed"
								style:width="{placed.width}px"
								style:height="{placed.height}px"
								style:transform="translate3d({placed.x}px, {placed.y}px, 0)"
								draggable={!item.concealed}
								role="listitem"
								ondragstart={(event) => {
									/* A sweep is `TileGesture`'s and never reaches here; dragging a selection
									   is Alt and drag. */
									if (
										dragOut(event, {
											id: fileOf(item),
											filename: item.original_filename ?? fileOf(item)
										})
									)
										return;
									startAssign(event, {
										kind: 'asset',
										// Everything picked, as files: two marks of one video tag it once.
										ids: picked(targetRows(item.id))
									});
								}}
								onpointerdown={(event) => pressStart(item.id, event)}
								onpointerup={pressEnd}
								onpointerleave={pressEnd}
								onpointercancel={pressEnd}
								onclickcapture={(event) =>
									swapping ? swapClicked(item, event) : tileClicked(item.id, event)}
							>
								<ContextMenu>
									<!-- Every address and every action here names the FILE the tile is a
										     picture of, which is the row itself on an ordinary wall and the video
										     a moment was cut from on a wall of moments. See `fileOf`. -->
									<Tile
										{item}
										picked={selection.has(item.id)}
										swapPicked={swapMode.on && swapMode.has('asset', fileOf(item))}
										swapRefused={swapping && refusedInSwaps(item)}
										width={placed.width}
										height={placed.height}
										thumbSrc={stillFor(item, source, fileOf)}
										importing={fileArriving(item)}
										placeholder={stillMissing(item)}
										reason={item.verdict ?? undefined}
										{pinnable}
										previewSrc={media.previews.urlFor(item.id)}
										playing={media.playing(item.id)}
										onmark={session.isAdmin
											? () => {
													const on = [fileOf(item)];
													verbs.named('share', on)?.run?.(on);
												}
											: undefined}
										onhidden={() => askAboutHidden(fileOf(item))}
										onopen={() => openRow(item)}
										onhover={(id, hovering) => media.tileHovered(id, hovering)}
										controls={tileControls}
									/>

									{#snippet items()}
										<!-- The declared verbs as menu rows, taken and picked once, with the wall's own
										     verbs handed the ROWS. One file names a subject ("Remove from favorites"). -->
										{@const rows = targetRows(item.id)}
										{@const on = picked(rows)}
										<VerbMenuItems
											ids={on}
											subjectId={fileOf(item)}
											verbs={[
												...offered(verbs.menu(on, on.length > 1 ? undefined : fileOf(item))),
												...wallVerbs(rows)
											]}
											extra={menuExtra && !item.concealed ? own : undefined}
											extraGrouped={menuExtraGrouped}
										/>
										<!-- The wall's own rows are a group of the menu, before the one that
										     destroys something rather than after it. -->
										{#snippet own()}
											{@render menuExtra?.(item, { forget: (id) => grid.forget(id) })}
										{/snippet}
									{/snippet}
								</ContextMenu>
								{#if grid.caption > 0}
									<!-- The row's name, or why it was left out, on the line a captioned wall keeps
									     under every tile; empty for neither, so every row keeps one rhythm. -->
									{@const caption = item.name ?? item.left_out ?? ''}
									<p class="caption" style:height="{grid.caption}px">
										{#if caption}
											<Tooltip label={caption} placement="top" stretch shrinks>
												<span class="caption-words">{caption}</span>
											</Tooltip>
										{/if}
									</p>
								{/if}
							</div>
						{/if}
					{/each}
				</div>
			{/if}
		{/snippet}

		<!-- The one page shape (`PageFrame`). The wall eases in on the page somebody ASKED for
		     (`grid.showing`), never on a re-layout or a catch-up. No footer under an empty wall. -->
		<PageFrame
			{crumbs}
			footer={grid.total > 0 || grid.loading ? pagerFooter : undefined}
			onbody={watchSize}
			fillBody={background !== undefined}
			arrival={() => grid.showing}
		>
			{#snippet header()}
				<!-- What the embedding screen puts above the grid, in the frame's header. -->
				{#if above}
					<PageAbove>{@render above()}</PageAbove>
				{/if}

				<!-- The SCOPED total, absent until the first page lands. -->
				<PageHeader
					{title}
					{icon}
					{beside}
					{lede}
					{titleHidden}
					level={titleLevel}
					count={grid.loaded > 0 || !grid.loading ? grid.total : undefined}
					bytes={grid.totalBytes}
				>
					{#snippet status()}
						{@render notice?.()}
						<!-- Why this wall may not be keeping up, in one line, explaining the pace. -->
						{#if liveLine}
							<span class="paused" role="status">{liveLine}</span>
						{/if}
						{#if imports.arriving > 0}
							<!-- Not a total: the queue's stream shows a window onto its front. -->
							<span class="working" role="status">
								<span class="pip"></span>
								Importing {imports.arriving === 1 ? '1 file' : `${counted(imports.arriving)} files`}
							</span>
						{/if}
						<!-- What has arrived above this page, held still for somebody reading further down,
						     and the press that takes it. The count is the label. -->
						{#if current.offersTheNew}
							<Button
								size="small"
								tone="secondary"
								icon="arrow_upward"
								onclick={() => current.takeTheNewOnes()}
							>
								{grid.newer.toLocaleString()}
								new
							</Button>
						{/if}
					{/snippet}

					{#snippet controls()}
						{@render tools?.()}
					{/snippet}
				</PageHeader>

				<!-- What belongs to the WALL rather than to the page: drawn under the title, above the
				     files. See `beneath`. -->
				{#if beneath}
					<div class="beneath">{@render beneath()}</div>
				{/if}
			{/snippet}

			<!-- The ground around the tiles and its menu, only where a screen asked for one; a tile's
			     own menu inside it wins on a tile. -->

			{#if background}
				<ContextMenu triggerClass="wall-ground">
					{@render wall()}
					{#snippet items()}{@render background()}{/snippet}
				</ContextMenu>
			{:else}
				{@render wall()}
			{/if}

			<!-- Picked, and what can be done with them: in the frame's floating slot, positioned against
			     the screen and clear of the body's arrival transform. The same declared verbs as the menu. -->
			{#snippet floating()}
				<HiddenDialog bind:open={hiddenOpen} target={hiddenAbout} />

				<!-- Worked out once here: `barShape` splits the named presses from the door. -->
				{@const rows = selection.ordered(grid.items.map((each) => each.id))}
				{@const on = picked(rows)}
				{@const shape = barShape([...offered(verbs.bar(on)), ...wallVerbs(rows)])}

				<ActionBar
					count={selection.count}
					noun="file"
					total={grid.total}
					onselectall={pickTheWholeQuery}
					onclear={() => selection.clear()}
				>
					{#snippet actions()}
						<VerbButtons ids={on} verbs={shape.named} />
					{/snippet}
					{#snippet overflow()}
						<VerbMore ids={on} verbs={shape.rest} noun="file" />
					{/snippet}
				</ActionBar>
			{/snippet}
		</PageFrame>
	{/snippet}
</FileVerbs>

<!-- The pager, in the frame's own row: it says where you are as well as how to move. -->
{#snippet pagerFooter()}
	<Pager
		offset={grid.offset}
		shown={grid.wall.tiles.length}
		total={grid.total}
		loading={grid.loading && grid.loaded === 0}
		onfirst={() => turnTo({ at: 0 })}
		onprevious={() => (start = walk.previous(grid, source.anchored))}
		onnext={() => (start = walk.next(grid))}
		onlast={() => turnTo({ last: true })}
		onjump={(position) => turnTo({ at: Math.max(0, position - 1) })}
		noun={source.noun}
	/>
{/snippet}

{#snippet tileControls(id: string)}
	{@const item = grid.byId.get(id)}
	{#if item}
		<!-- The heart belongs to the file: every row about it moves with the answer. -->
		<TileControls
			id={fileOf(item)}
			favorite={item.favorite}
			rating={item.rating}
			onchange={(state) => {
				for (const row of rowsAbout(fileOf(item))) grid.setState(row, state, stillBelongs);
			}}
		/>
	{/if}
{/snippet}

<style>
	/* The ground the wall's menu hangs on fills the body. `:global`: handed to `ContextMenu`. */
	:global(.wall-ground) {
		display: block;
		min-block-size: 100%;
	}

	/* The wall's own band, under the title. It may shrink below its content for the same reason
	   `.above` may: a wide child must not stretch the header and push the heading's controls off
	   the side of the window. */
	.beneath {
		min-inline-size: 0;
		margin-block-start: var(--space-3);
	}

	/* Room at the top for a tile to rise into: the scrolling box clips on both axes, which would
	   slice the top row's ring and lift. No snap points: a wheel notch would spring back or jump. */
	:global(.frame-body) .row:first-child,
	:global(.frame-body) .wall:first-child {
		margin-block-start: var(--space-2);
	}

	/* The importing placeholders' row. */
	.row {
		display: flex;
		margin-bottom: var(--grid-gutter);
	}

	/* One box with every tile placed in it by `translate3d`, so a tile that moves is composited
	   rather than laid out; its height is set from the layout, since the tiles are out of flow. */
	.wall {
		position: relative;
	}

	.placed {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
	}

	/* Under the tile rather than over it, in the room the row keeps for it; its height is the
	   layout's own number, written onto the line. One line, cut with an ellipsis. */
	.caption {
		position: absolute;
		inset-block-start: 100%;
		inset-inline: 0;
		box-sizing: border-box;
		margin: 0;
		padding-block-start: var(--space-1);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		white-space: nowrap;
		overflow: hidden;
	}

	/* The words, cut here rather than on the line: inside the label's flex box it is this span
	   that runs out of room. */
	.caption-words {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	/* Square, because a file that has not been read yet has no shape to lay out to. Every other row
	   is justified from the real proportions the server sent. */
	.importing .slot {
		flex: none;
	}

	.working {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/* The same in-progress colour the rest of the app uses, so a glance reads it without a legend. */
	.pip {
		width: 8px;
		height: 8px;
		border-radius: var(--radius-full);
		background: var(--sift-warn);
	}

	/* Quiet ink: a paused connection clears by itself. */
	.paused {
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.stopped {
		margin-block-end: var(--space-3);
		color: var(--sift-warn);
		font: var(--text-label);
	}

	/* Quiet: how much has been read so far is ordinary. */
	.described {
		margin-block-end: var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.note {
		color: var(--sift-ink-3);
		font: var(--text-body);
		padding: var(--space-6) 0;
	}
</style>
