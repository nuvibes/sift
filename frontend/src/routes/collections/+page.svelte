<script lang="ts">
	import { HeldNewcomers, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { Button } from '$lib/components/common';
	/* The collections screen: every collection, what it holds, and the making of one. */
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { filesSaid, filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import { untrack } from 'svelte';
	import { session } from '$lib/shell/session.svelte';
	import { goto } from '$app/navigation';
	import { page as address } from '$app/state';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { facetParams } from '$lib/components/shell/facet-labels';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import {
		COLLECTIONS_PER_PAGE,
		COLLECTION_ORDERS,
		collections
	} from '$lib/library/collections.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { anchorIn, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { collectionsSort } from './sort.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	/* Picking several together, the same gesture and the same bar the People and Sites walls
	   have, so sharing, hiding or deleting eight rows does not mean pressing the same menu eight
	   times. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		shown.filter((one) => !one.locked).map((one) => one.id)
	);

	function letGo(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key === 'Escape' && gesture.escaped(event)) event.stopPropagation();
	}

	/* Anything that leaves the wall leaves the selection with it, or the bar counts rows that
	   are not there and the next action runs over ids the server has forgotten. */
	$effect(() => {
		const here = new Set(shown.map((one) => one.id));
		untrack(() => selection.retain([...here]));
	});

	/* What order the shelf is in. SENT rather than applied here: the list is paged, so a
	 * comparison applied here would order one page and call it the order of the shelf. */
	/* Held in `./sort.svelte` and not here, because opening a row unmounts this screen. */
	const order = $derived(collectionsSort.value);

	/* Which page, at what size, in what order was last asked for, so a settled screen does not
	   ask for the same thing twice. */
	let askedFor = -1;
	let askedSize = -1;
	let askedOrder = '';
	/** ...and under which filter, so a facet going on or off re-asks from the same place. */
	/* What has been typed into the search box, and what the wall was last asked for. */
	let term = $state('');
	/* The words the wall is searched by live in its address, so the chip on the bar, Back and a
	   link all say the same thing as the box. */
	const prefix = $derived(wordsIn(address.url));
	const words = new WallWords();

	/* Words that reach the address any other way (the chip's cross, Back, the search band's See
	   all) are put in the box; the box's own write is not handed back to somebody still typing. */
	$effect(() => {
		const arrived = prefix;
		untrack(() => {
			if (words.echoed(arrived)) return;
			term = arrived;
		});
	});

	let askedNarrowing = '';
	/** ...and under which search, so a letter typed re-asks from the first page. */
	let askedPrefix = '';

	/* What the bar has filtered this shelf to, out of the address: only this noun's facets,
	   never the order or the page position. */
	const narrowedBy = $derived(facetParams('collection', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));

	/* Whole rows of the window, the same way every other wall pages (see `CardPaging`). */
	const paging = new CardPaging(COLLECTIONS_PER_PAGE, 'wall.collections');

	/* The route this wall belongs to, captured once, so a page landing under a row somebody has
	   just opened can be told from one landing on the wall itself. */
	const path = address.url.pathname;

	/* True exactly once: the first settle, which is somebody arriving at a link. */
	let arriving = true;

	/* What a bell brings while somebody is reading further down is held, so nothing they are
	   reading moves; at the top it comes straight in and the rows below slide. */
	const held = new HeldNewcomers<(typeof collections.items)[number]>();
	let fromBell = false;
	$effect(() => {
		const items = collections.items;
		untrack(() => held.take(items, fromBell));
	});
	const shown = $derived(held.shown);
	/* One function for the life of the screen: a new one each render would re-attach the wall. */
	function measureWall(wall: HTMLElement): () => void {
		const stop = held.watch(wall);
		const measured = paging.cards(wall);
		return () => {
			stop();
			measured();
		};
	}

	/* What this wall offers the bar above it. */
	const mine = Symbol('collections-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'collection',
			count: collections.loaded ? collections.total : undefined,
			resizable: true,
			playable: "A cover is a still, and a still doesn't play",
			sorts: [...COLLECTION_ORDERS],
			sort: order,
			onSort: (next) => {
				collectionsSort.set(next);
				/* A new order is a new list, so the page it was on means nothing in it, and
				   neither does the anchor in the address, which names a row of the list that has
				   just been replaced. */
				paging.offset = 0;
				forgetAnchor(address.url, path);
			}
		});
	});

	$effect(() => () => screenBar.release(mine));

	$effect(() => {
		/* `loaded` is READ here and `loading` is not, and the split is the whole of it. */
		/* Named so the effect re-runs when the page moves or the order changes. */
		const wanted = paging.offset;
		const size = paging.size;
		const wantedOrder = order;
		const narrowing = narrowedKey;
		// Read here rather than inside the untracked block, exactly as the page and the order are:
		// a letter typed is a different list and has to wake this.
		const wantedPrefix = prefix;
		const stale = !collections.loaded;
		// UNTRACKED, and that is load-bearing. Reading the address inside an effect makes the effect
		// depend on it, and this effect's own answer WRITES the address, so it would re-run itself.
		untrack(() => {
			if (arriving) {
				arriving = false;
				// The anchor lives on the paging until the page lands (see `CardPaging.land`).
				paging.arrive(anchorIn(address.url));
			}
			const narrowed = askedNarrowing !== narrowing || askedPrefix !== wantedPrefix;
			if (
				!stale &&
				!narrowed &&
				askedFor === wanted &&
				askedSize === size &&
				askedOrder === wantedOrder
			) {
				return;
			}
			/* A different filter is a different list, so the page it was on means nothing in it. */
			const moved = narrowed && askedNarrowing !== '';
			const at = moved ? 0 : wanted;
			if (paging.offset !== at) paging.offset = at;
			/* ...and the stale anchor comes OUT of the address with it, or a link copied from
			   the bar would carry a position belonging to a question nobody is asking any more. */
			if (moved) {
				paging.forget();
				forgetAnchor(address.url, path);
			}
			askedFor = at;
			askedSize = size;
			askedOrder = wantedOrder;
			askedNarrowing = narrowing;
			askedPrefix = wantedPrefix;
			void collections
				.fill(paging, wantedOrder, narrowedBy, wantedPrefix)
				// Written after the page lands, because turning to an anchor knows the offset it is
				// going to only once the answer arrives.
				.then(() => rememberAnchor(address.url, path, collections.items[0]?.id, paging.offset));
		});
	});

	async function addDropped(assetIds: string[], collectionId: string) {
		const collection = collections.byId(collectionId);
		const named = collection
			? thing('collection', collectionId, collection.name)
			: 'that collection';
		try {
			const done = await collections.add(collectionId, assetIds);
			toasts.show(
				done.changed === 0
					? ['Already in ', named]
					: [`Added ${filesSaid(done.changed)} to `, named, '. Nothing moved.'],
				{ tone: 'success' }
			);
			announceSkipped(done);
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
		}
	}

	/* The heart and the stars, which are THIS account's and reach nobody else's screen. */
	async function heart(one: { id: string; name: string }, favorite: boolean) {
		try {
			await collections.setFavorite(one.id, favorite);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(one: { id: string; name: string }, rating: number | null) {
		try {
			await collections.setRating(one.id, rating);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Every verb this wall offers, for the bar AND for each card's menu, from the one registry. */
	/* Read the wall again, at the page, the order and the search it is showing. */
	function reread() {
		fromBell = true;
		void collections.fill(paging, order, undefined, prefix).finally(() => (fromBell = false));
	}

	const verbs = new WallVerbs({
		kind: () => 'collection',
		rows: () =>
			shown.map((one) => ({
				id: one.id,
				name: one.name,
				count: one.item_count,
				favorite: one.favorite ?? false,
				hidden: one.vault,
				rating: one.rating ?? null,
				pinned: one.pinned ?? false
			})),
		changed: () => reread(),
		clear: () => selection.clear(),
		/* A pin moves the row to the top of this very list, so this wall offers it. */
		pins: true
	});

	/* What a right-click acts on: the selection when this row is part of one, this row alone
	   otherwise: the same rule the file grid follows. */
	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(shown.map((one) => one.id)) : [id];
	}

	const pickedIds = $derived(selection.ordered(shown.map((one) => one.id)));

	/* And again whenever a share or a restrict moves, because this list is scoped: what belongs
	   in it changes without anything being imported. */
	reloadOnLibraryChange(reread);
</script>

<svelte:head><title>Collections</title></svelte:head>

<!-- The same wall component every other entity screen uses, and the same card. -->
<EntityGrid
	title="Collections"
	icon="box"
	drawn={shown.length}
	total={collections.total}
	loading={collections.loading}
	measure={measureWall}
	page={paging.showing}
	failed={collections.failed ? "The collections couldn't be loaded." : null}
	empty={emptyWallSays(
		'collections',
		prefix,
		Object.keys(narrowedBy).length > 0,
		session.isAdmin
			? 'No collections yet. A collection is a sequence you arrange by hand. Make one above, then drag clips onto it.'
			: 'Nobody has shared a collection with you yet.'
	)}
>
	{#snippet pager()}
		<Pager
			offset={paging.offset}
			shown={shown.length}
			total={collections.total}
			noun="collections"
			onfirst={() => paging.goTo(0, collections.total)}
			onprevious={() => paging.step(-1, collections.total)}
			onnext={() => paging.step(1, collections.total)}
			onlast={() => paging.last(collections.total)}
			onjump={(position) => paging.goTo(position - 1, collections.total)}
		/>
	{/snippet}

	<!-- After the count, so the line coming and going moves nothing. -->
	{#snippet status()}
		{#if held.waiting > 0}
			<Button size="small" tone="secondary" icon="arrow_upward" onclick={() => held.letIn()}>
				{held.waiting.toLocaleString()}
				new
			</Button>
		{/if}
	{/snippet}

	{#snippet controls()}
		<!-- The box and the Add, in the one shape every entity wall wears (see `WallControls`). -->
		<WallControls
			noun="collection"
			plural="collections"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			onadd={session.isAdmin ? () => void goto('/collections/new') : undefined}
		/>
		<!-- No rename form here: Rename opens the registry's box (`EntityWallFlows`), the one every
		     wall and every tab uses. Add opens the blank record at `/collections/new`. -->
	{/snippet}

	{#each shown as collection (collection.id)}
		{@const target = dropTarget({
			kind: 'asset',
			targetId: collection.id,
			onassign: addDropped,
			onlink: session.isAdmin
				? (url, id) => void fetchOnto(url, 'collection', id, collection.name)
				: undefined
		})}
		<div
			role="listitem"
			data-drop-zone={target.zone}
			ondragenter={target.handlers.ondragenter}
			ondragover={target.handlers.ondragover}
			ondragleave={target.handlers.ondragleave}
			ondrop={target.handlers.ondrop}
		>
			<EntityCard
				href="/collections/{collection.id}"
				swapAs={{ kind: 'collection', id: collection.id }}
				name={collection.name}
				locked={collection.locked}
				counts={cardCells(
					'collection',
					collection.id,
					`/collections/${collection.id}`,
					collection.counts
				)}
				coverAssetId={collection.cover_asset_id}
				coverUploadId={collection.cover_upload_id}
				coverAtMs={collection.cover_at_ms}
				coverFrame={collection.cover_frame}
				art={collection.art}
				detail={filesSized(collection.item_count, sizeOf(collection))}
				shared={collection.shared}
				restricted={collection.restricted}
				hidden={collection.vault}
				onsharing={session.isAdmin ? () => verbs.askToShare([collection.id]) : undefined}
				onhidden={() => verbs.askAboutHidden(collection.id)}
				favorite={collection.favorite ?? false}
				pinned={collection.pinned ?? false}
				rating={collection.rating ?? null}
				onfavorite={(next) => void heart(collection, next)}
				onrate={(next) => void rate(collection, next)}
				dropping={target.over}
				selected={selection.has(collection.id)}
				id={collection.id}
				onpressstart={(event) => gesture.pressStart(collection.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(collection.id, event)}
			>
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. -->
					<VerbMenuItems
						ids={targetIds(collection.id)}
						subjectId={collection.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(collection.id)),
								pinned: verbs.allPinned(targetIds(collection.id)),
								favorite: verbs.allFavorite(targetIds(collection.id)),
								rating: verbs.sharedRating(targetIds(collection.id)),
								handlers: verbs.handlers
							})
						)}
					/>
				{/snippet}
			</EntityCard>
		</div>
	{/each}
	<!-- The selection bar, in the frame's floating slot: positioned against the screen, so it
	     stands above the pager (the frame says how tall its footer is) and above a docked
	     corner player, rather than over both. Mounted after the wall, it would be outside the
	     frame, read no footer, and cover the pager whenever anything was picked. -->
	{#snippet floating()}
		<EntitySelectionBar
			{selection}
			order={() => shown.map((one) => one.id)}
			noun="collection"
			showingHidden={verbs.allHidden(pickedIds)}
			pinned={verbs.allPinned(pickedIds)}
			favorite={verbs.allFavorite(pickedIds)}
			rating={verbs.sharedRating(pickedIds)}
			handlers={verbs.handlers}
		/>
	{/snippet}
</EntityGrid>

<svelte:window onkeydown={letGo} />

<!-- Every sheet a verb opens, drawn once for the wall by the registry. See the People wall. -->
<EntityWallFlows {verbs} />
