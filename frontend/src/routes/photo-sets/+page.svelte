<script lang="ts">
	/* Photo Sets: the shoots. Every set of pictures that arrived together, as a wall of covers. */
	import { untrack } from 'svelte';
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import { goto } from '$app/navigation';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { counted, picturesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { anchorIn, asked, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { photoSetsSort } from './sort.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { COLLECTION_ORDERS } from '$lib/library/collections.svelte';
	import { api } from '$lib/api/client';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { photoSets } from '$lib/library/photo-sets.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { page as address } from '$app/state';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { facetParams } from '$lib/components/shell/facet-labels';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import type { components } from '$lib/api/schema';

	type PhotoSet = Pick<
		components['schemas']['PhotoSetSummary'],
		| 'id'
		| 'name'
		| 'locked'
		| 'cover_asset_id'
		| 'cover_upload_id'
		| 'item_count'
		| 'origin'
		| 'origin_url'
		| 'vault'
		| 'favorite'
		| 'pinned'
		| 'rating'
		| 'shared'
		| 'restricted'
	> &
		// The card's count row, and the cover's moment, window and token for its address.
		Partial<
			Pick<
				components['schemas']['PhotoSetSummary'],
				'counts' | 'cover_at_ms' | 'cover_frame' | 'art'
			>
		>;

	let items = $state<PhotoSet[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state<string | null>(null);

	/* A page is the rows that fill a screenful, measured off a real card: the same machinery every
	   other card wall uses, so a page here is the same distance of scrolling as a page there. */
	const paging = new CardPaging(24, 'wall.photo-sets');

	/* The order the wall is in, SENT rather than applied here: a comparison run over the rows in
	   hand would order one page and call it the order of the wall. */
	const order = $derived(photoSetsSort.value);

	/* What the bar has filtered this shelf to, out of the address: only this noun's facets,
	   never the order or the page position. */
	const narrowedBy = $derived(facetParams('photo_set', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));
	/* What is typed in the box, and what the list is asked with once the typing settles. */
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
	/* The search is part of what list this is: a new term is a new list, so it resets the page
	   and the anchor exactly as a facet does, through the one key both effects read. */
	const listKey = $derived(`${narrowedKey}\n${prefix}`);

	/* Picking several together, the same gesture and the same bar every other wall of cards has. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		items.filter((one) => !one.locked).map((one) => one.id)
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
		const here = items.map((one) => one.id);
		untrack(() => selection.retain(here));
	});

	/* What this wall offers the bar above it. */
	const mine = Symbol('photo-sets-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'photo_set',
			count: loading ? undefined : total,
			resizable: true,
			playable: "A cover is a still, and a still doesn't play",
			sorts: [...COLLECTION_ORDERS],
			sort: order,
			onSort: (next) => {
				photoSetsSort.set(next);
				/* A new order is a new list, so the page it was on means nothing in it, and
				   neither does the anchor in the address, which names a row of the list that has
				   just been replaced. */
				paging.forget();
				paging.offset = 0;
				forgetAnchor(address.url, path);
			}
		});
	});

	$effect(() => () => screenBar.release(mine));

	const byId = (id: string) => items.find((one) => one.id === id);

	/* The route this wall belongs to, captured once, so a page landing under a set somebody has
	   just opened can be told from one landing on the wall itself. */
	const path = address.url.pathname;

	/* True exactly once: the first settle, which is somebody arriving at a link. */
	let arriving = true;

	/** Which offset was last asked for, so a settled screen does not ask for it a second time. */
	let askedFor = -1;
	/** ...and at what size, so a resize that changes the page size re-asks even from the same place. */
	let askedSize = -1;
	/** ...and in which order, and under which filter: both are a different list. */
	let askedOrder = '';
	let askedKey = '';

	async function load() {
		loading = true;
		failed = null;
		try {
			/* Through the paging, so a first visit trims the rows it holds or asks for the
			   remainder instead of asking twice (see `CardPaging.fill`). */
			const page = await paging.fill(
				JSON.stringify([order, narrowedBy, prefix]),
				() => items,
				(ask) =>
					api.get<components['schemas']['PhotoSetList']>('/photo-sets', {
						// The box's words match anywhere in a name, as the People wall's do.
						query: { ...narrowedBy, prefix, anywhere: 'true', ...asked(ask), sort: order }
					}),
				(answer) => ({
					rows: answer.items ?? [],
					total: answer.total ?? 0,
					offset: answer.offset ?? 0
				})
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			items = page.rows;
			total = page.total;
			paging.land(page.offset);
			rememberAnchor(address.url, path, items[0]?.id, page.offset);
			loading = false;
		} catch {
			failed = "Those couldn't be loaded.";
			loading = false;
		}
	}

	/* Named so the effect re-runs when the page moves, when its SIZE changes (a taller window
	   holds more rows), and when the order or the filtering does. */
	$effect(() => {
		const wanted = paging.offset;
		const size = paging.size;
		const wantedOrder = order;
		const narrowing = listKey;
		// UNTRACKED, and that is load-bearing. Reading the address inside an effect makes the effect
		// depend on it, and this effect's own answer WRITES the address, so it would re-run itself.
		const anchored = untrack(() => (arriving ? anchorIn(address.url) : null));
		arriving = false;
		untrack(() => {
			if (
				!anchored &&
				askedFor === wanted &&
				askedSize === size &&
				askedOrder === wantedOrder &&
				askedKey === narrowing
			)
				return;
			// An anchor is asked for INSTEAD of an offset, so the offset it carried means nothing yet.
			if (anchored) paging.arrive(anchored);
			askedFor = paging.offset;
			askedSize = size;
			askedOrder = wantedOrder;
			askedKey = narrowing;
			void load();
		});
	});

	/* A different filter is a different list, so the page it was on means nothing in it, and the
	   anchor in the address names a row of the list that has been replaced. */
	let askedNarrowing = '';
	$effect(() => {
		const narrowing = listKey;
		untrack(() => {
			const first = askedNarrowing === '';
			askedNarrowing = narrowing;
			if (first) return;
			paging.forget();
			if (paging.offset !== 0) paging.offset = 0;
			forgetAnchor(address.url, path);
		});
	});

	/* A share, a hide or an import changes which of these this account may see, and produces no
	   event of its own to say so. */
	reloadOnLibraryChange(load);

	/* Every verb this wall offers, for the bar AND for each card's menu, from the one registry. */
	const verbs = new WallVerbs({
		kind: () => 'photo_set',
		rows: () =>
			items.map((one) => ({
				id: one.id,
				name: one.name,
				count: one.item_count,
				favorite: one.favorite,
				hidden: one.vault,
				rating: one.rating,
				pinned: one.pinned
			})),
		changed: () => void load(),
		clear: () => selection.clear(),
		/* A pin moves the row to the top of this very list, so this wall offers it. */
		pins: true
	});

	/* What a right-click acts on: the selection when this row is part of one, this row alone
	   otherwise: the same rule the file grid follows. */
	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(items.map((one) => one.id)) : [id];
	}

	const pickedIds = $derived(selection.ordered(items.map((one) => one.id)));

	/* The row moves immediately and the server's answer is kept, the same optimistic write the heart on a
	   tile makes: a control that waits for a round trip before filling in reads as broken. */
	function put(id: string, state: components['schemas']['PhotoSetStateView']) {
		items = items.map((one) => (one.id === id ? { ...one, ...state } : one));
	}

	async function heart(set: PhotoSet, wanted: boolean) {
		put(set.id, { favorite: wanted, rating: set.rating });
		try {
			const held = await api.put<components['schemas']['PhotoSetStateView']>(
				`/photo-sets/${set.id}/favorite`,
				{ body: { favorite: wanted } }
			);
			put(set.id, held);
		} catch {
			put(set.id, { favorite: set.favorite, rating: set.rating });
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(set: PhotoSet, wanted: number | null) {
		put(set.id, { favorite: set.favorite, rating: wanted });
		try {
			const held = await api.put<components['schemas']['PhotoSetStateView']>(
				`/photo-sets/${set.id}/rating`,
				{ body: { rating: wanted } }
			);
			put(set.id, held);
		} catch {
			put(set.id, { favorite: set.favorite, rating: set.rating });
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Pictures dragged from the grid onto a set. The same gesture the Collections wall takes,
	   and the same promise: the membership row is the only thing written. */
	async function addDropped(assetIds: string[], setId: string) {
		const set = byId(setId);
		const named = set ? thing('photo_set', setId, set.name) : 'that Photo Set';
		try {
			// Through the store rather than at the endpoint.
			const done = await photoSets.add(setId, assetIds);
			toasts.show(
				done.changed === 0
					? ['Already in ', named]
					: [
							`Added ${counted(done.changed)} ${done.changed === 1 ? 'picture' : 'pictures'} to `,
							named,
							'. Nothing moved.'
						],
				{ tone: 'success' }
			);
			announceSkipped(done, 'picture');
			await load();
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
		}
	}
</script>

<svelte:head><title>Photo Sets</title></svelte:head>

<EntityGrid
	title="Photo Sets"
	icon="photo_library"
	drawn={items.length}
	{total}
	{loading}
	failed={failed ?? undefined}
	measure={paging.cards}
	page={paging.showing}
	empty={emptyWallSays(
		'Photo Sets',
		prefix,
		Object.keys(narrowedBy).length > 0,
		session.isAdmin
			? 'No Photo Sets yet. One is made from a folder of pictures, or from a gallery you download.'
			: 'Nobody has shared a Photo Set with you yet.'
	)}
>
	{#snippet pager()}
		<Pager
			offset={paging.offset}
			shown={items.length}
			{total}
			noun="Photo Sets"
			onfirst={() => paging.goTo(0, total)}
			onprevious={() => paging.step(-1, total)}
			onnext={() => paging.step(1, total)}
			onlast={() => paging.last(total)}
			onjump={(position) => paging.goTo(position - 1, total)}
		/>
	{/snippet}

	{#snippet controls()}
		<!--
			The Add and the search, in the one shape every entity wall wears (see `WallControls`).
		-->
		<WallControls
			noun="Photo Set"
			plural="Photo Sets"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			onadd={session.isAdmin ? () => void goto('/photo-sets/new') : undefined}
		/>
		<!-- No rename form here: Rename opens the registry's box (`EntityWallFlows`), the one every
		     wall and every tab uses. -->
	{/snippet}

	{#each items as set (set.id)}
		{@const target = dropTarget({
			kind: 'asset',
			targetId: set.id,
			onassign: addDropped,
			onlink: session.isAdmin
				? (url, id) => void fetchOnto(url, 'photo_set', id, set.name)
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
				href="/photo-sets/{set.id}"
				swapAs={{ kind: 'photo_set', id: set.id }}
				name={set.name}
				locked={set.locked}
				counts={cardCells('photo_set', set.id, `/photo-sets/${set.id}`, set.counts ?? {})}
				coverAssetId={set.cover_asset_id}
				coverUploadId={set.cover_upload_id}
				coverAtMs={set.cover_at_ms}
				coverFrame={set.cover_frame}
				art={set.art}
				detail={picturesSized(set.item_count, sizeOf(set))}
				shared={set.shared}
				restricted={set.restricted}
				hidden={set.vault}
				onsharing={session.isAdmin ? () => verbs.askToShare([set.id]) : undefined}
				onhidden={() => verbs.askAboutHidden(set.id)}
				favorite={set.favorite}
				pinned={set.pinned}
				rating={set.rating}
				onfavorite={(next) => void heart(set, next)}
				onrate={(next) => void rate(set, next)}
				selected={selection.has(set.id)}
				id={set.id}
				onpressstart={(event) => gesture.pressStart(set.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(set.id, event)}
				dropping={target.over}
			>
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. -->
					<VerbMenuItems
						ids={targetIds(set.id)}
						subjectId={set.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(set.id)),
								pinned: verbs.allPinned(targetIds(set.id)),
								favorite: verbs.allFavorite(targetIds(set.id)),
								rating: verbs.sharedRating(targetIds(set.id)),
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
			order={() => items.map((one) => one.id)}
			noun="Photo Set"
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
