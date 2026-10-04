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
	 * One qualification, worth reading before touching `nudge`: the wall is DRAWN with what this
	 * account has pinned first (the server's order, as on every wall with a pin), so the order on
	 * screen is not the arrangement, and a rearrange computed from what is drawn would save the
	 * pinned-first order as the collection's own. Every item carries its stored `position` for
	 * that reason, and `arrangement` is what Move earlier and Move later act on.
	 *
	 * Dropping a clip here adds it, as on the list screen, and no file moves.
	 */
	import type { Crumb } from '$lib/components/common';
	import type { Frame } from '$lib/entity/cover-frame';
	import { onDestroy } from 'svelte';
	import { page } from '$app/state';
	import {
		ActionBar,
		ConfirmDialog,
		ContextMenu,
		Empty,
		FileVerbs,
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
		type RelatedKind
	} from '$lib/entity/related.svelte';
	import { dropTarget, startAssign } from '$lib/components/common/drag-assign.svelte';
	import { dragOut } from '$lib/capture/copy-out';
	import Icon from '$lib/components/Icon.svelte';
	import {
		collections,
		type Collection,
		type CollectionItem
	} from '$lib/library/collections.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import Tile from '$lib/components/Tile.svelte';
	import { GRID_GUTTER, justify, targetRowHeight } from '$lib/grid/justify';
	import { gridSize } from '$lib/grid/grid.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	// The hover clip, shared with the search wall. See the module for why this is not the library
	// grid's whole slot-pool machine.
	import { HoverPreviews, canPreview } from '$lib/grid/hover-preview.svelte';
	import { openAsset } from '$lib/player/asset-view';
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

	/*
	 * Which wall this page is showing, read off the address so every tab is a real place: the back
	 * button steps between them and a link somebody sends opens on the one they were looking at.
	 *
	 * A collection's Files wall is a bespoke drag-to-rearrange grid rather than the shared
	 * `AssetGrid`, so the strip cannot simply be handed to a component the way it is on the other
	 * four entity pages. It is handed
	 * to the frame's own header here instead, in the same slot and the same order `EntityGrid` uses,
	 * so the two shapes cannot drift apart on screen.
	 */
	/*
	 * WHAT HAPPENED TO IT is a tab and not a wall, which is why it is kept out of `tabsFor`.
	 *
	 * Every other tab here is one question (an entity wall filtered to this page's files),
	 * answered by one shared table that a gate holds against the server's. A history is none of
	 * that: no wall, no count, no filter and no page. Putting it in that table to save four
	 * lines here would give a tab with no endpoint to every page that reads the same table.
	 */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('collection', asked));

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
	let items = $state<CollectionItem[]>([]);

	/*
	 * WHAT THE CARDS PICKED ON THIS PAGE'S TABS FILTER THE FILES TO, read off the address the way a
	 * person's Files tab reads it (see `picks.ts`). The contents route takes these five fields and
	 * applies them inside the collection's own arranged order, so a pick on Seen with or Tags filters
	 * this wall exactly as it filters a person's, and the number that comes back is the filtered
	 * one, from the same statement as the rows.
	 *
	 * Held as its spelling too, so the re-read below follows a change in the FILTERING and not every
	 * change of the address: moving between tabs rewrites `show=` and must not fetch the wall again.
	 */
	const narrowing = $derived(narrowingOf(page.url));
	const narrowingKey = $derived(JSON.stringify(narrowing));
	const narrowed = $derived(Object.keys(narrowing).length > 0);

	/* Declared after `items` because it reads it. The Files tab's number is what is on this screen:
	   for a hand-arranged sequence all of it, and while picks filter it, what they left. The wall
	   is this page's own fetch, filtered on every tab, so its rows ARE the tab's number and no second
	   request is made for it (see `narrowedFilesTotal` for why the other pages ask one). */
	const tabs = $derived([
		...tabsFor('collection', id, `/collections/${id}`, {
			...counts.current,
			files: items.length
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
	let loading = $state(false);
	let failed = $state(false);
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

	/* The layout, worked out here rather than fetched.
	 *
	 * `justify` is the same function Browse lays its rows out with, so a clip is the same shape on
	 * both screens. What is deliberately NOT shared is the paging and the virtualisation: a
	 * collection is a sequence somebody arranged by hand, which is dozens of items, and the machinery
	 * that makes ten thousand tiles affordable would only be a way for the arranged order to end up
	 * re-derived somewhere.
	 */
	const GUTTER = GRID_GUTTER;

	let wall = $state<HTMLElement | null>(null);
	let wallWidth = $state(0);

	/*
	 * How tall a row is here, which is the same question Browse asks.
	 *
	 * `targetRowHeight` is the one place that rule lives (the notch somebody chose on the bar
	 * above this screen, and the window's own responsive answer) rather than a constant that
	 * ignores both; see it for why the notch is passed in rather than read there.
	 *
	 * Declared AFTER the width it reads, which a derived needs: the getter is lazy, so a reference
	 * to a `let` further down the file only fails when it is actually read, which is a temporal
	 * dead zone error at whatever moment the wall first lays out rather than at build time.
	 */
	const ROW_HEIGHT = $derived(targetRowHeight(gridSize.step, wallWidth));

	/*
	 * What the bar above this screen can do, while the FILES tab is the one showing.
	 *
	 * The other tabs are `RelatedWall`'s and it publishes for itself, so this is guarded on the tab
	 * rather than left to whichever effect ran last: two publishers for one screen is a bar that
	 * says something different depending on the order things mounted. The wall lays its rows out
	 * from the size notch, so the slider is live here as on every other entity page.
	 *
	 * No orders. A collection is a sequence somebody arranged BY HAND, and that arrangement is the
	 * point of the screen; an order control over it would be a second opinion about what the
	 * sequence is. The reason is on the control rather than left to the general wording.
	 */
	const mine = Symbol('collection-files');

	$effect(() => {
		if (shown !== 'files') return;
		/* The Filter panel stays off. The picks on this page's tabs DO filter this wall, and
		   their chips draw on the bar whatever this says (a chip draws wherever the address
		   carries a filter). What stays off is the rest of the query language (a rating, a
		   date), which the contents route does not take, and a panel offering a filter the wall
		   ignores would be a filter in force on the bar and nowhere else. */
		screenBar.publish(mine, {
			filterable:
				'A collection is the arrangement you made. Pick cards on its other tabs to filter it',
			resizable: true,
			playable: true,
			sorts: []
		});
	});

	$effect(() => () => screenBar.release(mine));

	const byId = $derived(new Map(items.map((item) => [item.id, item])));
	const order = $derived(items.map((item) => item.id));
	const rows = $derived(
		justify(items, { containerWidth: wallWidth, targetHeight: ROW_HEIGHT, gutter: GUTTER })
	);

	/* The content box, not the padding box: `clientWidth` includes padding, so laying rows out to
	 * it makes every row wider than the space it sits in. The same measurement the grid makes, and
	 * for the same reason. */
	function measure() {
		if (!wall) return;
		const style = getComputedStyle(wall);
		const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
		wallWidth = Math.max(0, wall.clientWidth - padding);
	}

	function watchWidth(element: HTMLElement): () => void {
		measure();
		if (typeof ResizeObserver === 'undefined') return () => {};
		const observer = new ResizeObserver(() => measure());
		observer.observe(element);
		return () => observer.disconnect();
	}

	/* What the viewer opens into, so Next and Previous walk the collection in ITS order rather than
	 * the grid's. `runs` is what play-through may advance to on its own: a still has no end to reach
	 * and a run that stepped onto one would sit there. */
	function neighbours() {
		return items.map((item) => ({
			id: item.id,
			runs: item.media_type === 'video' || item.media_type === 'gif'
		}));
	}

	/* Reordering is `nudge`, from the tile's own menu: a tile's drag means "take this file
	   out" on every screen in Sift, including this one, and the menu is the way to do it without
	   a mouse. */

	$effect(() => {
		// Read here so the re-read follows the picks as well as the collection. See `narrowingKey`.
		void narrowingKey;
		void refresh(id);
	});

	/* The one entity detail page that keeps its own loader rather than taking `EntitySubject`,
	 * and that is a decision rather than an omission: this page fetches TWO things (the row and
	 * the ordered list of what is in it), and its screen is built around the second. It never
	 * blanks on a re-read either: the header stays up and only the empty state reads `loading`.
	 *
	 * It re-reads when what this account may see moves: a share taken back, a rename, a cover, a
	 * heart.
	 */
	reloadOnLibraryChange(() => void refresh(id));

	/* Which re-read is the latest. A pick moves the filtering while an earlier read is in flight, and
	   the slower answer for picks already taken off must not land over the current one. */
	let asking = 0;

	async function refresh(collectionId: string) {
		if (!collectionId) return;
		const ask = (asking += 1);
		const narrowTo = narrowing;
		loading = true;
		failed = false;
		try {
			const row = await collections.one(collectionId);
			const contents = await collections.contents(collectionId, 200, 0, narrowTo);
			if (ask !== asking) return;
			collection = row;
			items = contents.items;
			missing = false;
		} catch (error) {
			if (ask !== asking) return;
			missing = isMissing(error);
			failed = !missing;
		} finally {
			if (ask === asking) loading = false;
		}
	}

	/*
	 * Move one item one place in the ARRANGEMENT, and write the arrangement back.
	 *
	 * Not the visible order, and that distinction is the whole of this function. The wall is drawn
	 * with pinned items first (the pin does on this wall what it does on every other), so the
	 * order on screen is not the order in the collection. Sending what is drawn would have saved
	 * that pinned-first order as the collection's own the first time anybody moved anything, and
	 * a pin is one account's private opinion while the arrangement is everybody's and was built by
	 * hand. A private opinion must never be able to overwrite shared work.
	 *
	 * So the sequence is rebuilt from each item's stored `position`, which the server sends for
	 * exactly this. `index` is a position in the ARRANGEMENT too: see where it is computed.
	 */
	const arrangement = $derived(
		[...items].sort((one, two) => (one.position ?? 0) - (two.position ?? 0))
	);

	async function nudge(index: number, by: number) {
		// Never over a filtered wall: the rows drawn are part of the arrangement, and writing an order
		// of part of it would move every file the filtering left out behind the ones it kept. The
		// verbs say so and are dimmed (see `ownVerbs`); this is the same rule where the write is made.
		if (narrowed) return;
		const to = index + by;
		if (to < 0 || to >= arrangement.length) return;
		const next = [...arrangement];
		const [moved] = next.splice(index, 1);
		next.splice(to, 0, moved);
		// The new positions, applied to what is on screen so the change is visible at once. The
		// wall re-sorts itself: `arrangement` reads these, and the drawn order reads the pin.
		const at = new Map(next.map((item, place) => [item.id, place]));
		items = items.map((item) => ({ ...item, position: at.get(item.id) ?? item.position }));
		try {
			await collections.reorder(
				id,
				next.map((item) => item.id)
			);
		} catch {
			toasts.show("That couldn't be rearranged", { tone: 'error' });
			await refresh(id);
		}
	}

	/*
	 * The pin is written by the shared verb, through `setFilesPinned`, not by this screen.
	 *
	 * It MOVES the tile, exactly as it does on every other wall that offers one. What must not be
	 * overwritten is the STORED arrangement, and that is protected where it is actually written:
	 * see `nudge`, which rebuilds from each item's own position rather than from what is on screen.
	 * Ordering the display by the pin never threatens it.
	 *
	 * The shared verb re-reads afterwards because the server owns the order, and a second opinion
	 * about it on the client is the thing that comes to disagree. One copy of the rule, and a
	 * selection of forty is one request rather than forty.
	 */

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

	/** What a menu opened on one tile acts on: everything picked when this tile is part of it, and
	 *  otherwise just this one. The same rule every other wall's menu follows. */
	function targetIds(one: string): string[] {
		return selection.has(one) ? selection.ordered(order) : [one];
	}

	/*
	 * The verbs that are THIS WALL's own, declared rather than written out as rows.
	 *
	 * Everything a file can have done to it comes from `FileVerbs`: the same host the library
	 * grid and the recently-viewed wall draw from. What stays here is the handful only a collection
	 * has: a place in an arrangement, the cover, and membership.
	 *
	 * Appended to the shared list rather than drawn beside it, so both surfaces go on rendering ONE
	 * declaration: the menu's separator still falls in front of the first row that destroys
	 * something, and nothing can be added to one surface without the other.
	 *
	 * The three that move or choose are `singleOnly` (moving forty tiles one place is not a thing,
	 * and neither is forty covers), so the bar leaves exactly those out and keeps Remove.
	 */
	function ownVerbs(item: CollectionItem, index: number): Verb[] {
		return [
			{
				id: 'move-earlier',
				label: 'Move earlier',
				icon: 'arrow_upward',
				singleOnly: true,
				disabled: narrowed || index <= 0,
				why: narrowed ? NARROWED_WHY : "It's already first",
				run: () => void nudge(index, -1)
			},
			{
				id: 'move-later',
				label: 'Move later',
				icon: 'arrow_downward',
				singleOnly: true,
				disabled: narrowed || index === arrangement.length - 1,
				why: narrowed ? NARROWED_WHY : "It's already last",
				run: () => void nudge(index, 1)
			},
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
			await collections.removeItems(
				id,
				taken.map((one) => one.id)
			);
			selection.clear();
			await refresh(id);
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
			await refresh(id);
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

	/* Putting a collection in the vault, and taking it back out.
	 *
	 * Hiding one hides everything in it, which is what makes this worth a confirmation while adding
	 * an item is not: one press changes what is on the grid for every item the collection holds.
	 *
	 * Taking it back out has no confirmation and needs none. It is only reachable with the vault
	 * already open (with it shut this page answers 404 for a concealed collection), so the
	 * deliberate step has already been taken, at the PIN box.
	 */

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

	/* The same write pointed the other way, and nothing to navigate.
	 *
	 * The re-read is kept here rather than left to the library bell, which is what the person's page
	 * relies on. The difference is what draws the verb: there it is a wall that the bell re-reads,
	 * and here it is this page's own header, which offers Hide or Stop hiding off the row it is
	 * holding. A reveal that only waited would leave the wrong verb under the pointer for as long as
	 * the connection took to say so, and for ever where there is no connection up. */
	async function reveal() {
		const moved = await setHidden([id], false, hiddenAs);
		if (moved.length > 0) await refresh(id);
	}

	/* How big the WHOLE collection is, for the line under its name: that line is about the
	   collection, not about what the picks left on the wall. The record's own count while filtered
	   (scoped to this viewer the same way), and what is on the wall otherwise, which is the same
	   number and follows an add or a removal the moment it lands. */
	const wholeCount = $derived(narrowed ? (collection?.item_count ?? items.length) : items.length);
	/* How big those files are, off the record, and only while the line's number IS the record's:
	   an add or a removal moves the wall's count before the record is read again, and a size of the
	   files before it beside a count of the files after it would describe neither. */
	const wholeBytes = $derived(
		collection && wholeCount === collection.item_count ? sizeOf(collection) : null
	);

	const target = $derived(dropTarget({ kind: 'asset', targetId: id, onassign: addDropped }));
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
	The drop handlers sit on the page rather than on a chip, because the target here is the whole
	collection: there is one of it, and a strip to aim at would be a second, smaller place to
	miss. It carries an explicit role and a name so the region is announced rather than being a
	silent element that happens to accept a gesture. A named section is already a region, so it
	needs the name and not the role.
-->
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
						if (!loading) arrived();
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
								items = items.map((each) =>
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
							forget: (one) => {
								items = items.filter((each) => each.id !== one);
							},
							refresh: () => void refresh(id)
						}}
					>
						{#snippet children(verbs)}
							<PageFrame bleed {crumbs}>
								{#snippet floating()}
									<!-- The bar every wall raises over a selection, drawing the declared verbs and nothing
								     hand-written: there is no markup here for a button to be added to, which is what
								     stops this bar and the menu below coming to mean different things.

								     Split once, up here rather than inside the bar's snippets: the few it names and
								     the door holding the rest read the same list and act on the same files, and a
								     second copy of either would be a second answer. `barShape` decides which verb
								     goes where; nothing on this page does. -->
									{@const on = selection.ordered(order)}
									{@const shape = barShape([...verbs.bar(on), removeVerb])}

									<ActionBar
										count={selection.count}
										noun="file"
										total={items.length}
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
										count={items.length}
										beside={tabStrip}
									/>
								{/snippet}

								{#if failed}
									<Problem message="This collection couldn't be loaded." />
								{:else if !loading && items.length === 0 && narrowed}
									<Empty scope="page" icon="box" title="Nothing here has every pick"
										>Take a pick off to see more of this collection.</Empty
									>
								{:else if !loading && items.length === 0}
									<Empty scope="page" icon="box" title="Nothing in here yet"
										>Drag files from the library onto this page to add them.</Empty
									>
								{:else}
									<!--
						The same justified rows Browse uses, and the same tile.

						Not the same COMPONENT, though, and the difference is the whole reason this is written out
						here. `AssetGrid` fetches a page at a time from the server and virtualises what it draws,
						because a library is tens of thousands of items in an order the server decides. A
						collection is a sequence somebody arranged by hand (dozens of items, in an order that is
						the point of the screen), so there is nothing to page and nothing to virtualise, and the
						order must never be re-sorted here or the client would hold a second opinion about what
						the sequence is.

						What IS shared is everything a person can see: `justify` lays the rows out, and `Tile`
						draws each item, so a clip looks and behaves the same here as it does in Browse.
					-->
									<div class="wall" bind:this={wall} {@attach watchWidth}>
										{#each rows as row, rowIndex (rowIndex)}
											<div class="row" style:height="{row.height}px" style:gap="{GUTTER}px">
												{#each row.tiles as placed (placed.id)}
													{@const item = byId.get(placed.id)}
													<!-- Where it sits in the ARRANGEMENT, which is what Move earlier and Move later
										     act on. Not where it is drawn: the wall puts pinned items first, and a
										     move computed from the drawn order would move the wrong neighbour. -->
													{@const index = arrangement.findIndex((one) => one.id === placed.id)}
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
																		...ownVerbs(item, index),
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
																	onopen={(opened) => openAsset(opened, neighbours())}
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
					oncount={(total) => {
						counts.saw(tab, total);
						arrived();
					}}
				/>
			{/if}
		{/snippet}
	</TabHold>
{/if}

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

	/* The wall, and the rows in it. The same shape Browse draws, without the paging.
	 *
	 * The frame is `bleed`, so the inset is carried here: the wall reaches the same two margins the
	 * grid's does on Browse, and the heading above it starts at the same place. */
	.wall {
		padding-inline: var(--page-pad);
		/* The same room the grid's scroller keeps: a tile's ring sits 2px outside it and the hover
		   lift scales it a few pixels past that, and the top row needs room to grow into. */
		padding-block-start: var(--space-2);
		padding-block-end: var(--space-6);
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
