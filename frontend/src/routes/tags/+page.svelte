<script lang="ts">
	import { goto } from '$app/navigation';
	import { refusedMark } from '$lib/swap/refused';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	/*
	 * The tag screen: every tag, what it holds, and the editing of it.
	 *
	 * It is also the drop target for the gesture the storage model is built around. A clip dragged
	 * from the grid onto a chip here is tagged and **no file moves**: the path and the bytes are
	 * untouched, and the association is the only thing written. The screen says so in words,
	 * because the alternative is somebody inferring it from nothing appearing to happen.
	 */
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { filesSaid, filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import { page as address } from '$app/state';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { facetParams } from '$lib/components/shell/facet-labels';
	import { untrack } from 'svelte';
	import { session } from '$lib/shell/session.svelte';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import { TAGS_PER_PAGE, TAG_ORDERS, tags } from '$lib/entity/tags.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { anchorIn, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { tagsSort } from './sort.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { enrichBoxes, loadEnrichBoxes } from '$lib/entity/enrichment.svelte';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	/*
	 * What order the wall is in, and which page of it is showing.
	 *
	 * The order is SENT rather than applied here: the list is paged, so a comparison applied here
	 * would order the rows in hand and call it the order of all of them.
	 *
	 * Most used first is the default because a library with two hundred tags has perhaps twelve
	 * that anybody actually reaches for.
	 */
	/* Held in `./sort.svelte` and not here, because opening a row unmounts this screen. See
	   that file for the fault that is. */
	const order = $derived(tagsSort.value);

	/* Which page, at what size, in what order was last asked for, so a settled screen does not ask
	   for the same thing twice. The same guard the People wall carries, and for the same reason: an
	   effect that re-reads state its own work writes triggers itself. */
	/* What has been typed into the search box, and what the wall was last asked for.
	 *
	 * SENT TO THE SERVER as the list route's `prefix`, never applied to the rows in hand: this wall
	 * is a page of a longer list, so filtering what has already arrived would quietly hide every
	 * tag past the page. The People wall's own note says the same thing about the same mistake.
	 */
	let term = $state('');
	/* The words the wall is searched by live in its address, so the chip on the bar, Back and a
	   link all say the same thing as the box. See `WallWords`. */
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

	let askedFor = -1;
	let askedSize = -1;
	let askedOrder = '';
	/** ...and under which filter, so a facet going on or off re-asks from the same place. */
	let askedNarrowing = '';
	/** ...and under which search, so a letter typed re-asks from the first page. */
	let askedPrefix = '';

	/* What the bar has filtered this wall to, out of the address: only this noun's facets, never
	   the order or the page position. A derived STRING beside it, because a derived only propagates
	   when its value changes and the effect below must not wake on every address change. */
	const narrowedBy = $derived(facetParams('tag', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));

	/* Whole rows of the window, the same way every other wall pages (see `CardPaging`). */
	const paging = new CardPaging(TAGS_PER_PAGE, 'wall.tags');

	/* The route this wall belongs to, captured once, so a page landing under a row somebody has just
	   opened can be told from one landing on the wall itself. See `$lib/grid/anchor`. */
	const path = address.url.pathname;

	/* True exactly once: the first settle, which is somebody arriving at a link. After that the
	   anchor in the address is one WE wrote, for the question being asked at the time. So reading
	   it again on a new question would start the new one at the old one's position. */
	let arriving = true;

	const shown = $derived(tags.items);

	/*
	 * What this wall offers the bar above it.
	 *
	 * A tag is not a file, so the query language does not apply here; the control SAYS so and
	 * stays where it is, rather than leaving a gap for its neighbours to slide into.
	 */
	const mine = Symbol('tags-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'tag',
			resizable: true,
			playable: "A tag is a word, and a word doesn't play",
			sorts: [...TAG_ORDERS],
			sort: order,
			onSort: (next) => {
				tagsSort.set(next);
				/* A new order is a new list, so the page it was on means nothing in it, and neither
				   does the anchor in the address, which names a row of the list that has just been
				   replaced. Honoured on the next load it would open the new order somewhere in the
				   middle of itself. */
				paging.offset = 0;
				forgetAnchor(address.url, path);
			}
		});
	});

	$effect(() => () => screenBar.release(mine));

	/* Picking several at once, the same gesture and the same bar as every other wall. A library
	   that has accumulated forty tags is where sharing or hiding a set of them matters, rather
	   than doing it one context menu at a time. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		shown.filter((one) => !one.locked).map((one) => one.id)
	);

	function letGo(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again. It touches no
		// data and never reaches the server (see `TileGesture.undoKeys`).
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key === 'Escape' && gesture.escaped(event)) event.stopPropagation();
	}

	$effect(() => {
		const here = new Set(shown.map((one) => one.id));
		untrack(() => selection.retain([...here]));
	});

	$effect(() => {
		/*
		 * `loaded` is READ here and `loading` is not, and the split is the whole of it.
		 *
		 * `loaded` going false is the signal to fetch again: that is exactly what `forget()` does
		 * when the vault opens or shuts, because what is concealed never arrives and a list held
		 * from before is wrong the moment the vault moves. Not reading it would leave this screen
		 * deaf to that: the rows cleared, nothing asking for them again, and the wall empty until
		 * navigated away from and back.
		 *
		 * `loading` is what must NOT be read. It goes true then false on every fetch, so an effect
		 * watching it is triggered by its own work: a request loop. Reading only `loaded` cannot
		 * loop: the load ends with it true, this runs once more, and returns.
		 */
		/* Named so the effect re-runs when the page moves or the order changes, which is the whole
		   point of it. The page SIZE is read too: a taller window holds more rows. */
		const wanted = paging.offset;
		const size = paging.size;
		const wantedOrder = order;
		const narrowing = narrowedKey;
		// The search is read here rather than inside the untracked block, for the same reason the
		// page and the order are: a letter typed is a different list and has to wake this.
		const wantedPrefix = prefix;
		const stale = !tags.loaded;
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
			/* A different filter is a different list, so the page it was on means nothing in it.
			   Not on the FIRST ask, where there is no previous filtering to have moved away from. */
			const moved = narrowed && askedNarrowing !== '';
			const at = moved ? 0 : wanted;
			if (paging.offset !== at) paging.offset = at;
			/* ...and the stale anchor comes OUT of the address with it, or a link copied from the bar
			   would carry a position belonging to a question nobody is asking any more. */
			if (moved) {
				paging.forget();
				forgetAnchor(address.url, path);
			}
			askedFor = at;
			askedSize = size;
			askedOrder = wantedOrder;
			askedNarrowing = narrowing;
			askedPrefix = wantedPrefix;
			void tags
				.fill(paging, wantedOrder, narrowedBy, wantedPrefix)
				// Written after the page lands, because turning to an anchor knows the offset it is
				// going to only once the answer arrives. The offset itself is landed inside `fill`,
				// with the rows, and its re-run of this effect asks nothing (see `CardPaging.land`).
				.then(() => rememberAnchor(address.url, path, tags.items[0]?.id, paging.offset));
		});
	});

	async function assignDropped(assetIds: string[], tagId: string) {
		const tag = tags.byId(tagId);
		const named = tag ? thing('tag', tagId, tag.name) : 'with that tag';
		try {
			const done = await tags.assign(assetIds, [tagId]);
			toasts.show(
				done.changed === 0
					? ['Already tagged ', named]
					: [`Tagged ${filesSaid(done.changed)} `, named, '. Nothing moved.'],
				{ tone: 'success' }
			);
			announceSkipped(done);
		} catch {
			toasts.show("Those couldn't be tagged", { tone: 'error' });
		}
	}

	/*
	 * The heart and the stars, which are THIS account's and reach nobody else's screen.
	 *
	 * Offered to a guest as well as to an admin, unlike renaming or deleting: an opinion changes
	 * where a row appears on this wall and never whether it appears, so there is nothing here for a
	 * permission to protect. Keeping something from another account is what restricting is for.
	 *
	 * The store settles the row from the server's own answer, so nothing here re-reads the wall:
	 * throwing away a list somebody is looking at to change one glyph is what that would cost.
	 */
	async function heart(one: { id: string; name: string }, favorite: boolean) {
		try {
			await tags.setFavorite(one.id, favorite);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(one: { id: string; name: string }, rating: number | null) {
		try {
			await tags.setRating(one.id, rating);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/*
	 * Every verb this wall offers, for the bar AND for each card's menu, from the one registry.
	 *
	 * A tag cannot carry a tag of its own and two tags do not turn out to be one, so neither verb
	 * exists for the kind and neither surface offers it (the registry's `KIND_FACTS`, not a
	 * judgement written here). The heart and the stars ARE offered: the card draws them and the
	 * server has the two routes. There is
	 * no Open: the card is a link to the tag's own page. See the People wall for the rest.
	 */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});

	/* Read the wall again, at the page, the order and the search it is showing. Called bare it
	   would ask for the first page at a size nothing chose. See the note below. */
	function reread() {
		void tags.fill(paging, order, undefined, prefix);
	}

	const verbs = new WallVerbs({
		kind: () => 'tag',
		rows: () =>
			shown.map((one) => ({
				id: one.id,
				name: one.name,
				count: one.asset_count,
				favorite: one.favorite ?? false,
				hidden: one.hidden,
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

	/* Where the Enrich rows stand for these, read off the holder the registry's handlers refuse
	   from, so the row pressed and the row greyed out cannot disagree. */
	const enrichment = verbs.enrichment('tag');

	/* And again whenever a share or a restrict moves, because this list is scoped: what belongs in
	   it changes without anything being imported. See the helper.

	   The page, the page size and the order are all passed, exactly as the other three walls pass
	   them. Called bare it asks for the first two hundred rows in whatever order the store was last
	   left in, so a library change while somebody is on page three would replace the rows with page
	   one at a size nothing chose, and leave the pager still saying page three. */
	reloadOnLibraryChange(reread);
</script>

<svelte:head><title>Tags</title></svelte:head>

<!--
	A wall of cards, the same ones People, Sites and Collections are drawn as.

	A card gives the size slider something to make bigger, gives a tag a cover that says what it IS
	at a glance, and makes the click do what every other wall's does: open the library filtered to
	this tag. Renaming is on the right-click menu, which is where every other wall keeps it.
-->
<EntityGrid
	title="Tags"
	icon="shoppingmode"
	drawn={shown.length}
	total={tags.total}
	loading={tags.loading}
	failed={tags.failed ? "The tags couldn't be loaded." : null}
	measure={paging.cards}
	page={paging.showing}
	empty={emptyWallSays(
		'tags',
		prefix,
		Object.keys(narrowedBy).length > 0,
		session.isAdmin
			? 'No tags yet. A tag is a word you can search for. Make one above, then drag clips onto it.'
			: 'Nobody has shared a tag with you yet.'
	)}
>
	{#snippet pager()}
		<Pager
			offset={paging.offset}
			shown={shown.length}
			total={tags.total}
			noun="tags"
			onfirst={() => paging.goTo(0, tags.total)}
			onprevious={() => paging.step(-1, tags.total)}
			onnext={() => paging.step(1, tags.total)}
			onlast={() => paging.last(tags.total)}
			onjump={(position) => paging.goTo(position - 1, tags.total)}
		/>
	{/snippet}

	{#snippet controls()}
		<!-- The box and the Add, in the one shape every entity wall wears (see `WallControls`).
		     Add opens the blank record form rather than making a tag out of a name here, so a tag
		     can arrive with its description and its other names already on it. -->
		<WallControls
			noun="tag"
			plural="tags"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			maxlength={64}
			onadd={session.isAdmin ? () => void goto('/tags/new') : undefined}
		/>
		<!-- No rename form here: Rename opens the registry's box (`EntityWallFlows`), which keeps
		     the tag's colour through `tags.rename` and stops at a tag's 64 characters. -->
	{/snippet}

	{#each shown as tag (tag.id)}
		{@const target = dropTarget({
			kind: 'asset',
			targetId: tag.id,
			onassign: assignDropped,
			onlink: (url, id) => void fetchOnto(url, 'tag', id, tag.name)
		})}
		<div
			role="listitem"
			data-drop-zone={target.zone}
			ondragenter={target.handlers.ondragenter}
			ondragover={target.handlers.ondragover}
			ondragleave={target.handlers.ondragleave}
			ondrop={target.handlers.ondrop}
		>
			<!-- The tag's own page, not a pre-filled Browse. A tag that was only a WORD writing a filter
			     would leave nowhere to say who turns up under it, which sites it comes from, or which
			     shoots carry it. Its files are the first thing that page shows, so nothing is lost. -->
			<EntityCard
				href="/tags/{tag.id}"
				swapAs={{ kind: 'tag', id: tag.id, refused: refusedMark(tag) }}
				name={tag.name}
				locked={tag.locked}
				counts={cardCells('tag', tag.id, `/tags/${tag.id}`, tag.counts)}
				coverAssetId={tag.cover_asset_id}
				coverUploadId={tag.cover_upload_id}
				coverAtMs={tag.cover_at_ms}
				coverFrame={tag.cover_frame}
				art={tag.art}
				detail={filesSized(tag.asset_count, sizeOf(tag))}
				shared={tag.shared}
				restricted={tag.restricted}
				hidden={tag.hidden}
				onsharing={session.isAdmin ? () => verbs.askToShare([tag.id]) : undefined}
				onhidden={() => verbs.askAboutHidden(tag.id)}
				favorite={tag.favorite ?? false}
				pinned={tag.pinned ?? false}
				rating={tag.rating ?? null}
				onfavorite={(next) => void heart(tag, next)}
				onrate={(next) => void rate(tag, next)}
				dropping={target.over}
				selected={selection.has(tag.id)}
				id={tag.id}
				onpressstart={(event) => gesture.pressStart(tag.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(tag.id, event)}
			>
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. Not written out
					     again here, which is what stops the two from coming apart. -->
					<VerbMenuItems
						ids={targetIds(tag.id)}
						subjectId={tag.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(tag.id)),
								pinned: verbs.allPinned(targetIds(tag.id)),
								favorite: verbs.allFavorite(targetIds(tag.id)),
								rating: verbs.sharedRating(targetIds(tag.id)),
								handlers: verbs.handlers,
								keptLocal: enrichment.allKeptLocal(targetIds(tag.id)),
								enrichRefused: enrichment.anyRefused(targetIds(tag.id)),
								...(enrichment.why(targetIds(tag.id))
									? { enrichWhy: enrichment.why(targetIds(tag.id)) as string }
									: {}),
								enrichBoxes: enrichBoxes()
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
			noun="tag"
			showingHidden={verbs.allHidden(pickedIds)}
			pinned={verbs.allPinned(pickedIds)}
			favorite={verbs.allFavorite(pickedIds)}
			rating={verbs.sharedRating(pickedIds)}
			handlers={verbs.handlers}
			enrichBoxes={enrichBoxes()}
		/>
	{/snippet}
</EntityGrid>

<svelte:window onkeydown={letGo} />

<!-- Every sheet a verb opens, drawn once for the wall by the registry. See the People wall. -->
<EntityWallFlows {verbs} />
