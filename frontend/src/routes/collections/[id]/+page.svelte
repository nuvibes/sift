<script lang="ts">
	import { counted, filesSaid, sizeOf, withSize } from '$lib/entity/entity-counts';
	/*
	 * One collection, in the order it was arranged.
	 *
	 * The order is the point: a collection is a sequence somebody put together, so the items are
	 * drawn in the order the server sends and never re-sorted here. Rearranging sends the ids it
	 * can see in their new order and the server writes the positions; anything concealed is absent
	 * from this screen and keeps its place behind what was arranged.
	 *
	 * The wall is DRAWN pinned first and paged like every wall of files, so Move earlier and Move
	 * later act on the stored `position`, never on what is drawn (see `collections.move`).
	 *
	 * Dropping a clip here adds it, as on the list screen, and no file moves.
	 */
	import type { Crumb } from '$lib/components/common';
	import type { Frame } from '$lib/entity/cover-frame';
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/state';
	import {
		ActionBar,
		ConfirmDialog,
		ContextMenu,
		Empty,
		FileVerbs,
		Pager,
		Problem,
		VerbButtons,
		VerbMenuItems,
		VerbMore
	} from '$lib/components/common';
	import { Selection } from '$lib/components/common/selection.svelte';
	import { TILE_ID, TileGesture } from '$lib/components/common/tile-gesture.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import EntityHeader from '$lib/components/entity/EntityHeader.svelte';
	import { barShape, type Verb } from '$lib/components/common/verbs';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import PickedFilter from '$lib/components/entity/PickedFilter.svelte';
	import { narrowingOf, tabsCarryingPicks } from '$lib/components/entity/picks';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import RelatedWall from '$lib/components/entity/RelatedWall.svelte';
	import TabHold, { wallOfTab } from '$lib/components/entity/TabHold.svelte';
	import {
		showing as chosenTab,
		iconOf,
		tabsFor,
		TabCounts,
		TabWords,
		type RelatedKind
	} from '$lib/entity/related.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { clearWallSays, emptyWallSays } from '$lib/components/shell/wall-words';
	import { dropTarget, startAssign } from '$lib/components/common/drag-assign.svelte';
	import { dragOut } from '$lib/capture/copy-out';
	import Icon from '$lib/components/Icon.svelte';
	import {
		collections,
		contentsAsked,
		contentsSource,
		type Collection,
		type CollectionItem
	} from '$lib/library/collections.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import Tile from '$lib/components/Tile.svelte';
	import { Grid, type PageStart } from '$lib/grid/grid.svelte';
	import { WalkBack } from '$lib/grid/walk-back';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	// The hover clip, shared with the search wall. See the module for why this is not the library
	// grid's whole slot-pool machine.
	import { HoverPreviews, canPreview } from '$lib/grid/hover-preview.svelte';
	import { openAsset, type Continues } from '$lib/player/asset-view';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { api, isMissing } from '$lib/api/client';
	import { thumbUrl } from '$lib/entity/art';
	import type { Tag } from '$lib/entity/tags.svelte';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';
	import { makerOf, type Maker } from '$lib/entity/enrich.svelte';
	import { setHidden } from '$lib/library/hiding';
	import { vault } from '$lib/shell/vault.svelte';

	const id = $derived(page.params.id ?? '');

	/* The tab is read off the address, so every tab is a real place. Files is this page's own wall,
	   so the strip goes to the frame's header in the slot `EntityGrid` uses. */
	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('collection', asked));
	const fileWords = new TabWords();

	/* The numbers beside the tab words: all of them in one request, and whatever the wall on
	   screen found beating the map for its own tab. See `TabCounts`. */
	const counts = new TabCounts();
	$effect(() => counts.follow('collection', id));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	let collection = $state<Collection | null>(null);

	/* WHO MADE IT, for the line under the name. Read on its own rather than off the row: the row's
	   shape is the WALL's too, so a maker on it would be filled here and null there with nothing to
	   say which meaning the null had. See `makerOf`.

	   Guarded by the id it was asked for, like every other per-entity read on these screens: a
	   slower answer for a collection navigated away from must not land under this heading. */
	let madeBy = $state<Maker | null>(null);
	let madeByFor = $state('');
	$effect(() => {
		const one = id;
		if (!one || madeByFor === one) return;
		madeByFor = one;
		void makerOf('collection', one).then((held) => {
			if (madeByFor === one) madeBy = held;
		});
	});
	/* Items move under the cursor, the same as everywhere else. Set up here because this screen
	   draws its own tiles rather than using the library grid, and the clip lives inside that
	   grid. */
	const previews = new HoverPreviews();
	onDestroy(() => previews.dispose());
	/* The arrangement a page at a time, as every wall of files pages; its rows are the route's own. */
	const grid = $derived(new Grid(contentsSource(id)));
	const items = $derived(grid.items as unknown as CollectionItem[]);
	/* The picks' own total while words or a bar filter narrow them further. */
	let pickedTotal = $state<number | null>(null);

	/* The address's filters, keyed so only a change of them re-reads; `narrowing` is the picks. */
	const filters = $derived(contentsAsked(page.url));
	const filtersKey = $derived(JSON.stringify(filters));
	const narrowing = $derived(narrowingOf(page.url));
	const narrowed = $derived(Object.keys(narrowing).length > 0);
	const searched = $derived(Object.keys(filters).some((name) => !(name in narrowing)));
	const filtered = $derived(Object.keys(filters).some((one) => one !== 'q' && !(one in narrowing)));
	/* The route pins first; saying so keeps the grid from continuing after a row it cannot find. */
	const query = $derived({ ...filters, pinned_first: '1' });
	/* The tab's number: the whole, or what the picks left, never what the words found. */
	const wholeFiles = $derived(
		searched
			? ((narrowed ? pickedTotal : collection?.item_count) ?? undefined)
			: grid.loaded > 0 || !grid.loading
				? grid.total
				: undefined
	);

	const tabs = $derived([
		...tabsFor('collection', id, `/collections/${id}`, {
			...counts.current,
			files: wholeFiles
		}),
		/* And it wears its number: the strip is a MAP of what this page can show, and one bare
		   word on a row of numbered ones reads as a tab nobody has looked at yet. It is the
		   count of the very thread the pane draws, at the same cap. See
		   `history_count_of_entity`. */
		{
			id: HISTORY,
			label: 'History',
			icon: 'history' as const,
			href: `/collections/${id}?show=${HISTORY}`,
			count: counts.current.history
		}
	]);
	let rowFailed = $state(false);
	const failed = $derived(rowFailed || grid.failed !== null);
	/* The server said there is no such collection for this account (a 404 means "no such id" and
	   "not yours" alike). Only that answer draws the page saying so: any other failure is Sift's. */
	let missing = $state(false);
	let confirming = $state<CollectionItem[]>([]);

	/* Picking several, the way every other wall does it: a press held on a tile starts a
	   selection and a sweep across its neighbours extends it (`TileGesture`), the bar rises with
	   a count, and the drag out of the window carries everything picked. The gesture swallows a
	   drag that starts during a sweep, in the capture phase, so the two cannot fight. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () => order);

	/* Escape clears the picks and Ctrl+Z takes back the last one, from wherever focus is: the same
	   window-level handling the grid has, for the same reason: the last click left focus on a tile
	   or nowhere, and a handler on the wall would only hear it while the wall had focus. */
	function onEscape(event: KeyboardEvent) {
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}
	let confirmOpen = $state(false);
	let confirmVault = $state(false);

	/* What this page can do to this collection, behind the one door every entity page wears. */
	const options = $derived<Verb[]>(
		!session.isAdmin || !collection
			? []
			: [
					collection.vault
						? {
								id: 'unhide',
								label: 'Stop hiding it',
								icon: 'visibility' as const,
								run: () => void reveal()
							}
						: {
								id: 'hide',
								label: 'Hide it',
								icon: 'visibility_off' as const,
								run: () => (confirmVault = true)
							}
				]
	);

	let scroller = $state<HTMLElement | null>(null);
	let contentBox = $state<HTMLElement | null>(null);

	/* The bar while Files shows (`RelatedWall` publishes for the rest): no orders, since the
	   arrangement made by hand is the point of the screen. */
	const mine = Symbol('collection-files');

	$effect(() => {
		if (shown !== 'files') return;
		/* The query language filters this wall as it does a person's Files tab. */
		screenBar.publish(mine, {
			query: collection ? { collections: collection.name } : undefined,
			filterable: true,
			resizable: true,
			playable: true,
			sorts: []
		});
	});

	$effect(() => () => screenBar.release(mine));

	const byId = $derived(new Map(items.map((item) => [item.id, item])));
	/* What the page draws: the grid holds the next page's first row beyond it. */
	const order = $derived(grid.rows.flatMap((row) => row.tiles.map((tile) => tile.id)));

	/* Measured as the walls of files measure, so a page holds the same whole screens. */
	function measure() {
		if (!scroller || !contentBox) return;
		if (scroller.clientHeight === 0 || contentBox.clientWidth === 0) return;
		const style = getComputedStyle(contentBox);
		const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
		const vertical = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
		grid.containerWidth = Math.max(0, contentBox.clientWidth - padding);
		grid.screenHeight = Math.max(0, scroller.clientHeight - vertical);
	}

	function watchSize(parts: { scroller: HTMLElement; content: HTMLElement }): () => void {
		scroller = parts.scroller;
		contentBox = parts.content;
		measure();
		if (typeof ResizeObserver === 'undefined') return () => {};
		const observer = new ResizeObserver(() => measure());
		observer.observe(parts.scroller);
		observer.observe(parts.content);
		return () => observer.disconnect();
	}

	// A new collection is a new grid, measured before it asks.
	$effect(() => {
		void grid;
		untrack(measure);
	});

	/* Where the page begins, for the question it was turned on; a new question starts at the top.
	   Previous returns to the page Next left, as on every wall of files. */
	const question = $derived(`${id}\n${filtersKey}`);
	let start = $state<{ for: string; at: PageStart }>({ for: '', at: { at: 0 } });
	const from = $derived<PageStart>(start.for === question ? start.at : { at: 0 });
	const walk = new WalkBack();

	function turnTo(next: PageStart) {
		walk.forget();
		start = { for: question, at: next };
	}

	function walkTo(next: () => PageStart) {
		if (start.for !== question) walk.forget();
		start = { for: question, at: next() };
	}

	$effect(() => {
		const asked = query;
		const at = from;
		void grid.pageRows;
		untrack(() => {
			void grid.loadAt(asked, at);
			if (scroller) scroller.scrollTop = 0;
		});
	});

	/* Next and Previous walk the collection in ITS order, past the page through the same question. */
	function asNeighbour(item: { id: string; media_type: string }) {
		return { id: item.id, runs: item.media_type === 'video' || item.media_type === 'gif' };
	}

	function openHere(opened: string) {
		const more: Continues = {
			from: grid.offset,
			total: grid.total,
			shuffles: false,
			fetch: async (offset, limit) => (await grid.blockAt(query, offset, limit)).map(asNeighbour)
		};
		openAsset(opened, items.map(asNeighbour), undefined, undefined, more);
	}

	/* Reordering is `nudge`, from the tile's own menu: a tile's drag means "take this file
	   out" on every screen in Sift, including this one, and the menu is the way to do it without
	   a mouse. */

	$effect(() => {
		// Read here so the picks' total follows the filters as well as the collection.
		void filtersKey;
		void refresh(id);
	});

	/* Its own loader rather than `EntitySubject`: the row and the arrangement are two reads, and a
	   re-read never blanks the header. It follows what this account may see. */
	reloadOnLibraryChange(() => void reread());

	/* Which re-read is the latest. A pick moves the filtering while an earlier read is in flight, and
	   the slower answer for picks already taken off must not land over the current one. */
	let asking = 0;

	/* The row and the picks' total; the files are the grid's. */
	async function refresh(collectionId: string) {
		if (!collectionId) return;
		const ask = (asking += 1);
		const picksOnly = searched && narrowed ? narrowing : null;
		rowFailed = false;
		try {
			const row = await collections.one(collectionId);
			const picked = picksOnly && (await collections.contents(collectionId, 1, 0, picksOnly));
			if (ask !== asking) return;
			collection = row;
			pickedTotal = picked ? picked.total : null;
			missing = false;
		} catch (error) {
			if (ask !== asking) return;
			missing = isMissing(error);
			rowFailed = !missing;
		}
	}

	/* Both, the page held where it is. */
	async function reread() {
		await Promise.all([refresh(id), grid.loadAt(query, { at: grid.offset }, { quiet: true })]);
	}

	/*
	 * Move one file one place in the ARRANGEMENT, never in the drawn order: the pin is one account's
	 * opinion and the arrangement is everybody's. Never over a filtered wall either, where the files
	 * left out would be moved behind the ones kept; the verbs are dimmed for the same reason.
	 */
	async function nudge(assetId: string, by: -1 | 1) {
		if (partWhy !== null) return;
		try {
			await collections.move(id, assetId, by);
		} catch {
			toasts.show("That couldn't be rearranged", { tone: 'error' });
		}
		await reread();
	}

	/* The pin is the shared verb's (`setFilesPinned`): it moves the tile, never the arrangement. */

	/** Whether the picture chooser is open. Opened by the pencil on the cover. */
	let pickingPicture = $state(false);

	async function makeCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		try {
			collection = await collections.setCover(id, assetId, atMs, { frame });
			toasts.show('Cover set', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be used as the cover", { tone: 'error' });
		}
	}

	async function uploadCover(file: File) {
		collection = await collections.uploadCover(id, file);
		toasts.show('Cover set', { tone: 'success' });
	}

	/** Take these out of the collection, once the question below has been answered. */
	function askToRemoveIds(ids: string[]) {
		confirming = ids.flatMap((one) => byId.get(one) ?? []);
		if (confirming.length > 0) confirmOpen = true;
	}

	/** Everything picked when this tile is part of it, otherwise this one, as on every wall. */
	function targetIds(one: string): string[] {
		return selection.has(one) ? selection.ordered(order) : [one];
	}

	/*
	 * The verbs only a collection has (a place in the arrangement, the cover, membership), appended
	 * to `FileVerbs`' list so the menu and the bar render one declaration. The three that move or
	 * choose are `singleOnly`, so the bar keeps only Remove. The moves are offered where the server
	 * sent a position, which is only to whoever may rearrange.
	 */
	function ownVerbs(item: CollectionItem): Verb[] {
		const at = item.position;
		const moves: Verb[] =
			at === null
				? []
				: [
						{
							id: 'move-earlier',
							label: 'Move earlier',
							icon: 'arrow_upward',
							singleOnly: true,
							disabled: partWhy !== null || at <= 0,
							why: partWhy ?? "It's already first",
							run: () => void nudge(item.id, -1)
						},
						{
							id: 'move-later',
							label: 'Move later',
							icon: 'arrow_downward',
							singleOnly: true,
							disabled: partWhy !== null || at >= grid.total - 1,
							why: partWhy ?? "It's already last",
							run: () => void nudge(item.id, 1)
						}
					];
		/* The cover and membership are an admin's, as the routes behind them are. */
		if (!session.isAdmin) return moves;
		return [
			...moves,
			{
				id: 'cover',
				label: collection?.cover_asset_id === item.id ? 'This is the cover' : 'Use as the cover',
				icon: 'star',
				singleOnly: true,
				disabled: collection?.cover_asset_id === item.id,
				why: "It's already the cover",
				run: () => void makeCover(item.id)
			},
			removeVerb
		];
	}

	/** Why the arrangement cannot be changed while picks filter the wall. See `nudge`. */
	const NARROWED_WHY = 'Only part of the collection is showing. Clear the picks to rearrange it';
	const SEARCHED_WHY = 'Only part of the collection is showing. Clear the search to rearrange it';
	const FILTERED_WHY = 'Only part of the collection is showing. Clear the filters to rearrange it';
	const partWhy = $derived(
		fileWords.asked ? SEARCHED_WHY : narrowed ? NARROWED_WHY : searched ? FILTERED_WHY : null
	);

	/** Out of this collection, and out of nothing else. Offered on both surfaces, worded once. */
	const removeVerb: Verb = {
		id: 'remove-from-collection',
		label: 'Remove from this collection',
		icon: 'close',
		run: (ids) => askToRemoveIds(ids)
	};

	async function removeItem() {
		const taken = confirming;
		if (taken.length === 0) return;
		try {
			const done = await collections.removeItems(
				id,
				taken.map((one) => one.id)
			);
			if (done.skipped === 0) selection.clear();
			await reread();
			/* A refused write comes back counted as skipped, not thrown. */
			if (done.skipped > 0) toasts.show("Couldn't remove that", { tone: 'error' });
		} catch {
			toasts.show("Couldn't remove that", { tone: 'error' });
		} finally {
			confirming = [];
		}
	}

	async function addDropped(assetIds: string[]) {
		try {
			const done = await collections.add(id, assetIds);
			toasts.show(
				done.changed === 0
					? 'Already in this collection'
					: `Added ${filesSaid(done.changed)}. Nothing moved.`,
				{ tone: 'success' }
			);
			announceSkipped(done);
			await reread();
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
		}
	}

	/* Deleting the collection.
	 *
	 * The files in it are untouched: a collection is a grouping, so what goes is the grouping and
	 * its membership rows. The question says so. */
	async function removeCollection() {
		await collections.remove(id);
		toasts.show('Deleted. The files in it are still here.', { tone: 'success' });
		await leaveFor('/collections');
	}

	/* What the two below are about, in one place: the noun the sentence uses, its plural being the
	   obvious one, and the write itself. The same lines the Collections wall passes. */
	const hiddenAs = $derived({
		noun: 'collection',
		stays: vault.unlocked,
		set: (one: string, flag: boolean) => collections.setVault(one, flag)
	});

	/*
	 * Hiding a collection, and bringing it back, through the ONE mechanism that does it.
	 *
	 * `collections.setVault` is the per-account vault route, not the record write an admin makes,
	 * and everything around it (the Privacy wording, a 401 for no PIN yet read like the 409, and an
	 * Undo on the sentence) comes from `hiding.ts`, which every wall uses. `hiding.test.ts`
	 * refuses a second copy anywhere in the client.
	 *
	 * `stays` is the vault's state and not a constant: with Hidden open it is still listed, so
	 * "unlock Hidden to see it" would be advice about something the person is looking at.
	 */
	async function conceal() {
		const moved = await setHidden([id], true, hiddenAs);
		// Away from a page that is about to stop answering: hidden, it is a 404 for you. Only on a
		// write that actually landed. A refusal has already been said, and leaving the page would
		// take the sentence away with it.
		if (moved.length > 0) await leaveFor('/collections');
	}

	/* The tags on this collection, as every other kind of thing carries them. */
	let chips = $state<Tag[]>([]);

	$effect(() => {
		const wanted = id;
		void (async () => {
			try {
				chips = await api.get<Tag[]>(`/collections/${wanted}/tags`);
			} catch {
				chips = [];
			}
		})();
	});

	/* This account's own opinion of the collection.
	 *
	 * The wall of collections has a heart and stars on every card, and the page about ONE
	 * collection has them too, so rating one is not only possible from the list. The store settles
	 * both from the server's own answer, so all these have to do is say when a write failed; silence would be a heart that springs back with nothing on screen to explain it.
	 */
	async function heart(favorite: boolean) {
		try {
			await collections.setFavorite(id, favorite);
			collection = collection ? { ...collection, favorite } : collection;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(rating: number | null) {
		try {
			await collections.setRating(id, rating);
			collection = collection ? { ...collection, rating } : collection;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* The chips as the shared header wants them.
	 *
	 * The tag store's row carries more than the header draws: a count, sharing marks. Filtered
	 * here, at the boundary, rather than by loosening the header's type: the header should go on
	 * describing exactly what it needs, or every caller after this one gets to hand it anything.
	 */
	const chipsForHeader = $derived(chips.map((tag) => ({ id: tag.id, name: tag.name })));

	async function setTag(tagId: string, add: boolean) {
		try {
			chips = await api.post<Tag[]>(`/collections/${id}/tags`, {
				body: { tag_id: tagId, add }
			});
		} catch {
			toasts.show(add ? "That tag couldn't be added" : "That tag couldn't be removed", {
				tone: 'error'
			});
		}
	}

	/* Re-read at once: the header's own Hide or Stop hiding is drawn off the row it holds. */
	async function reveal() {
		const moved = await setHidden([id], false, hiddenAs);
		if (moved.length > 0) await reread();
	}

	/* How big the WHOLE collection is, for the line under its name: that line is about the
	   collection, not about what the picks left on the wall. The record's own count while filtered
	   (scoped to this viewer the same way), and what is on the wall otherwise, which is the same
	   number and follows an add or a removal the moment it lands. */
	const wholeCount = $derived(
		narrowed || searched ? (collection?.item_count ?? grid.total) : grid.total
	);
	/* How big those files are, off the record, and only while the line's number IS the record's:
	   an add or a removal moves the wall's count before the record is read again, and a size of the
	   files before it beside a count of the files after it would describe neither. */
	const wholeBytes = $derived(
		collection && wholeCount === collection.item_count ? sizeOf(collection) : null
	);

	const target = $derived(
		dropTarget({ kind: 'asset', targetId: id, onassign: session.isAdmin ? addDropped : undefined })
	);
	/* The trail, drawn by the frame's band above the identity band. */
	const crumbs = $derived<Crumb[]>([
		{ label: 'Collections', href: '/collections' },
		{ label: collection?.name ?? 'Collection' }
	]);
</script>

<svelte:window onkeydown={onEscape} />

<svelte:head><title>{collection?.name ?? 'Collection'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and filed under it, exactly as one
     dropped on its card on the wall is. Only while there IS one: a page still settling
     has no id to aim at. See `EntityDropZone`. -->
{#if collection}
	<EntityDropZone kind="collection" id={collection.id} name={collection.name} />
{/if}

{#snippet tabStrip()}
	<Tabs
		tabs={tabsCarryingPicks(tabs, page.url)}
		current={showingHistory ? HISTORY : shown}
		label="What to show for {collection?.name ?? 'this collection'}"
	/>
	<!-- The cards picked on the tabs, counted, and the press that filters the Files tab to
	     them. Draws nothing while nothing is picked. See `picks.ts`. -->
	<PickedFilter />
{/snippet}

<!--
	Who this page is about, drawn on EVERY tab: whose collection it is does not change with what is
	being shown OF it. The tags are drawn here with it: `EntityHeader` draws a tag row for a person
	and a site, so a collection has no copy of that control of its own.
-->
{#snippet identity()}
	<EntityHeader
		{options}
		optionIds={[id]}
		icon={iconOf('collection')}
		kind="collection"
		mayEdit={session.isAdmin}
		deleteWord="collection"
		ondelete={session.isAdmin ? removeCollection : undefined}
		name={collection?.name ?? 'Collection'}
		onpicture={() => (pickingPicture = true)}
		oncover={async (assetId, atMs, more) => {
			collection = await collections.setCover(id, assetId, atMs, more);
		}}
		coverHref={`/collections/${id}`}
		coverAssetId={collection?.cover_asset_id ?? null}
		coverUploadId={collection?.cover_upload_id ?? null}
		coverAtMs={collection?.cover_at_ms ?? null}
		coverFrame={collection?.cover_frame ?? null}
		art={collection?.art ?? null}
		counts={withSize(filesSaid(wholeCount), wholeCount, wholeBytes)}
		oCount={collection?.o_count}
		favorite={collection?.favorite ?? false}
		rating={collection?.rating ?? null}
		onfavorite={(next) => void heart(next)}
		onrate={(next) => void rate(next)}
		tags={chipsForHeader}
		{madeBy}
		ontag={session.isAdmin ? (tag) => setTag(tag.id, true) : undefined}
		onuntag={session.isAdmin ? (tagId) => setTag(tagId, false) : undefined}
	></EntityHeader>
{/snippet}

<!--
	The drop handlers sit on the frame rather than on a chip, because the target here is the whole
	collection: there is one of it, and a strip to aim at would be a second, smaller place to miss.
	It carries a name so the region is announced rather than being a silent element that happens to
	accept a gesture.
-->
<!--
	The Files tab, which is this screen's own wall, and every other tab, which is the shared one.

	Only Files carries the drop target and the drag-to-rearrange grid: dropping a clip on a wall of
	PEOPLE would have no meaning, and the order that makes this screen worth having is an order over
	files. The identity band and the tab strip are rendered by both, from the same two snippets, so
	the page does not change shape when somebody moves between tabs.
-->
{#if missing}
	<!-- No header, no tabs and no verbs: a collection that is not there has nothing to act on. -->
	<Empty scope="page" icon="box" title="That collection isn't here">It may have been deleted.</Empty
	>
{:else if showingHistory}
	<!--
		The thread, in the frame every other screen uses. Not a wall: nothing to select, nothing to
		page and nothing to count, so this screen's own drop target and its drag-to-rearrange grid
		would be furniture with no work behind it.

		WITHOUT `measure`: that bounds the line AND centres it, and the thread belongs at the page's
		own left edge where the tabs and the title are.
	-->
	<!-- History draws the identity and the tab strip in the shape every other tab does
	     (`PageAbove`, then the strip as the heading row), so the strip stands at one height
	     on every tab. -->
	<PageFrame {crumbs}>
		{#snippet header()}
			<PageAbove>{@render identity()}</PageAbove>
			<PageHeader title="History" icon="history" level={2} titleHidden beside={tabStrip} />
		{/snippet}
		{#snippet children()}
			<EntityHistory subject="collection" {id} name={collection?.name} />
		{/snippet}
	</PageFrame>
{:else}
	<TabHold tab={shown} wallOf={wallOfTab}>
		{#snippet surface(tab, arrived)}
			{#if tab === 'files'}
				<div
					{@attach () => {
						// The files are the page's own read, so they have arrived once it is not reading.
						if (!grid.loading) arrived();
					}}
					class="drop"
					class:dropping={target.over}
					role="region"
					aria-label="Collection contents"
					ondragenter={target.handlers.ondragenter}
					ondragover={target.handlers.ondragover}
					ondragleave={target.handlers.ondragleave}
					ondrop={target.handlers.ondrop}
				>
					<!--
					Every verb, and every sheet behind it, from the one host the library grid draws from.
					The contents route carries `favorite` and `rating` on every item, so the library verbs
					have the whole opinion they read.

					What this screen hands over is what only this screen knows: how to look a row up, how to
					drop one, and how to re-read the arrangement. `pinnable`, because a collection is a wall
					somebody curates and is drawn pinned-first (see the note on the pin above).
				-->
					<FileVerbs
						{items}
						pinnable
						around={{
							lookup: (one) => byId.get(one),
							selection,
							setState: (one, state) => {
								/* The pin is carried with the heart and the stars, which the grid's own version does
							   not do (it re-reads instead). A collection's row HAS the field, so moving it here
							   is what makes the mark appear on the press rather than a round trip later. */
								grid.items = grid.items.map((each) =>
									each.id === one
										? {
												...each,
												favorite: state.favorite,
												rating: state.rating,
												pinned: state.pinned ?? each.pinned
											}
										: each
								);
							},
							forget: (one) => grid.forget(one),
							refresh: () => void reread()
						}}
					>
						{#snippet children(verbs)}
							<PageFrame
								{crumbs}
								footer={grid.total > 0 || grid.loading ? pagerFooter : undefined}
								onbody={watchSize}
							>
								{#snippet floating()}
									<!-- The bar every wall raises over a selection, drawing the declared verbs and nothing
								     hand-written: there is no markup here for a button to be added to, which is what
								     stops this bar and the menu below coming to mean different things.

								     Split once, up here rather than inside the bar's snippets: the few it names and
								     the door holding the rest read the same list and act on the same files, and a
								     second copy of either would be a second answer. `barShape` decides which verb
								     goes where; nothing on this page does. -->
									{@const on = selection.ordered(order)}
									{@const shape = barShape([
										...verbs.bar(on),
										...(session.isAdmin ? [removeVerb] : [])
									])}

									<ActionBar
										count={selection.count}
										noun="file"
										total={order.length}
										onselectall={async () => selection.toggleAll(order)}
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
								{#snippet header()}
									<!-- The identity band above the heading, in the same slot and the same order `EntityGrid`
						     puts it in on the other four entity pages. A person, a site, a shoot and a collection
						     are the same kind of place and say so the same way. -->
									<PageAbove>{@render identity()}</PageAbove>
									<PageHeader
										title="Files"
										icon="browse"
										level={2}
										titleHidden
										count={grid.total}
										beside={tabStrip}
									>
										{#snippet controls()}
											<WallControls
												noun="file"
												plural="files"
												bind:term={fileWords.term}
												onsettled={(typed) => fileWords.write(typed)}
											/>
										{/snippet}
									</PageHeader>
								{/snippet}

								{#if failed}
									<Problem message="This collection couldn't be loaded." />
								{:else if !grid.loading && items.length === 0 && searched}
									<Empty
										scope="page"
										icon="box"
										title={emptyWallSays('files', fileWords.asked, filtered, '')}
										>{clearWallSays(fileWords.asked, filtered)} to see the whole collection.</Empty
									>
								{:else if !grid.loading && items.length === 0 && narrowed}
									<Empty scope="page" icon="box" title="Nothing here has every pick"
										>Take a pick off to see more of this collection.</Empty
									>
								{:else if !grid.loading && items.length === 0}
									<Empty scope="page" icon="box" title="Nothing in here yet"
										>Drag files from the library onto this page to add them.</Empty
									>
								{:else}
									<!-- Browse's paging, rows and tile, drawn here because the moves and the drop are this
									     screen's own; the order is the server's and never re-sorted. -->
									<div class="wall">
										{#each grid.rows as row, rowIndex (rowIndex)}
											<div class="row" style:height="{row.height}px" style:gap="{grid.gutter}px">
												{#each row.tiles as placed (placed.id)}
													{@const item = byId.get(placed.id)}
													{#if item}
														<!-- Draggable out of Sift as everywhere; reordering is Move earlier and Move later
														     in the menu, which the keyboard reaches too. No buttons on a tile as narrow as 110. -->
														<div
															{...{ [TILE_ID]: item.id }}
															class="slot"
															draggable="true"
															role="listitem"
															ondragstart={(event) => {
																if (
																	dragOut(event, {
																		id: item.id,
																		filename: item.original_filename ?? item.id
																	})
																)
																	return;
																// A drag of something picked carries everything picked, as on every wall.
																startAssign(event, {
																	kind: 'asset',
																	ids: selection.has(placed.id)
																		? selection.ordered(order)
																		: [placed.id]
																});
															}}
															onpointerdown={(event) => gesture.pressStart(item.id, event)}
															onpointerup={() => gesture.pressEnd()}
															onpointerleave={() => gesture.pressEnd()}
															onpointercancel={() => gesture.pressEnd()}
															onclickcapture={(event) => gesture.clicked(item.id, event)}
														>
															<!--
												Named `tileMenu` and passed by name, NOT declared as `{#snippet items()}`.
												The prop is called `items`, and a snippet of that name shadows this page's own
												`items`: the array of everything in the collection, which the body below reads
												to know when a tile is last. It would throw while the menu was rendering, so the
												menu would open and draw nothing: right-clicking a tile would seem to do nothing.
											-->
															{#snippet tileMenu()}
																<!-- This wall's rows first (the arrows are the keyboard's way to the order), then
																     every file verb, both through `VerbMenuItems`. -->
																<VerbMenuItems
																	ids={targetIds(item.id)}
																	subjectId={item.id}
																	verbs={[
																		...ownVerbs(item),
																		...verbs.menu(targetIds(item.id), item.id)
																	]}
																/>
															{/snippet}
															<ContextMenu items={tileMenu}>
																<Tile
																	{item}
																	picked={selection.has(item.id)}
																	width={placed.width}
																	height={placed.height}
																	thumbSrc={item.thumb ? thumbUrl(item) : undefined}
																	previewSrc={previews.srcFor(item.id)}
																	playing={previews.playing(item.id)}
																	importing={!item.thumb}
																	pinnable
																	onopen={openHere}
																	onhover={(id, hovering) =>
																		previews.enter(item, hovering, canPreview(item))}
																/>
															</ContextMenu>
														</div>
													{/if}
												{/each}
											</div>
										{/each}
									</div>
								{/if}
							</PageFrame>
						{/snippet}
					</FileVerbs>
				</div>
			{:else}
				<RelatedWall
					on="collection"
					{id}
					named={collection?.name}
					showing={tab}
					title={tabs.find((one) => one.id === tab)?.label ?? ''}
					icon={tabs.find((one) => one.id === tab)?.icon ?? 'browse'}
					beside={tabStrip}
					titleHidden
					above={identity}
					oncount={(total, searched) => {
						if (!searched) counts.saw(tab, total);
						arrived();
					}}
				/>
			{/if}
		{/snippet}
	</TabHold>
{/if}

{#snippet pagerFooter()}
	<Pager
		offset={grid.offset}
		shown={grid.wall.tiles.length}
		total={grid.total}
		loading={grid.loading && grid.loaded === 0}
		onfirst={() => turnTo({ at: 0 })}
		onprevious={() => walkTo(() => walk.previous(grid, grid.source.anchored))}
		onnext={() => walkTo(() => walk.next(grid))}
		onlast={() => turnTo({ last: true })}
		onjump={(position) => turnTo({ at: Math.max(0, position - 1) })}
	/>
{/snippet}

<ConfirmDialog
	bind:open={confirmVault}
	title={collection ? `Hide "${collection.name}"?` : 'Hide it?'}
	consequence={'It disappears from every list, count and search box, on your screens ' +
		'\u2014 and so does everything in it. Unlock Hidden with your PIN to bring it back. Nothing ' +
		'is deleted or moved.'}
	confirmLabel="Hide it"
	onconfirm={conceal}
/>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirming.length > 1
		? `Remove ${counted(confirming.length)} files from the collection?`
		: 'Remove this from the collection?'}
	consequence={confirming.length > 1
		? "They leave this collection only. The files themselves aren't touched and stay exactly where they are."
		: "It leaves this collection only. The file itself isn't touched and stays exactly where it is."}
	confirmLabel="Remove"
	onconfirm={removeItem}
/>

<!-- The pencil's sheet. The same files this collection holds, and the same route the right-click
     menu on a tile already writes through: one way to set a cover, reached from two places. -->
<PickPicture
	bind:open={pickingPicture}
	name={collection?.name ?? 'this collection'}
	query={{ collection: id }}
	current={collection?.cover_asset_id ?? null}
	onpick={makeCover}
	onupload={uploadCover}
/>

<style>
	/*
	 * The whole screen is the drop target, so this wraps the frame rather than sitting inside it.
	 * Inside, it would be the scrolling body, and a border drawn on a box that scrolls moves when
	 * the wall does, which is the one thing a "you may drop here" edge must not do.
	 */
	.drop {
		display: flex;
		block-size: 100%;
		min-block-size: 0;
		/* An OUTLINE inside the edge, never a border: a border takes a pixel of room on every side,
		   and would put the identity band and the tab strip on this tab 1px lower than on every
		   other tab of the page. An outline is drawn over the box and moves nothing. */
		outline: 1px solid transparent;
		outline-offset: -1px;
		border-radius: var(--radius-md);
	}

	.drop.dropping {
		outline-color: var(--sift-accent);
	}

	/* Room for the top row's ring and hover lift, as the grid's scroller keeps. */
	.wall {
		padding-block-start: var(--space-2);
	}

	.row {
		display: flex;
		margin-bottom: var(--grid-gutter);
	}

	.slot {
		position: relative;
		flex: none;
	}
</style>
