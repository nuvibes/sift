<script lang="ts">
	/*
	 * Music: the songs the library's files carry. One card per song, on as many files as carry it.
	 * The page, its sidebar entry and every tab that shows songs are called Music, the word a
	 * file's own Music field and Settings > Music use; one row of it is still a song.
	 *
	 * A destination of its own and not a column of Browse, because a song is a thing a library is
	 * looked through by, the way a Photo Set or a tag is: "everything set to this song" is a question
	 * somebody asks, and the answer is a page with the song's own files, people and Sites on it.
	 *
	 * The same wall as every other wall of things: the same card, the same declared verbs, the same
	 * selection gesture and the same sheets (`WallVerbs`, `EntityWallFlows`), so a person who has
	 * learned to right-click one of these walls has learned this one: Share, Visibility and Hide
	 * among them. What a song has fewer of is decided there, not here: no tags of its own.
	 *
	 * Under each card's name, who the song credits (`SongArtists`): each artist a press that filters
	 * this wall to that artist, the same filter as the panel's Artists column, which the order by
	 * artist sits beside.
	 *
	 * A card whose song has no chosen cover is the music glyph on the letter's tint, never a file's
	 * picture: a wall of songs is a wall of music.
	 */
	import { untrack } from 'svelte';
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import { goto } from '$app/navigation';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { counted, filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { anchorIn, asked, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { SONG_ORDERS, songsSort } from './sort.svelte';
	import SongArtists from '$lib/components/entity/SongArtists.svelte';
	import ArtistRenameDialog from '$lib/components/entity/ArtistRenameDialog.svelte';
	import RowMenu from '$lib/components/common/RowMenu.svelte';
	import { ARTIST_FIELD, artistVerbs } from '$lib/entity/artists.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { api } from '$lib/api/client';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { songs } from '$lib/entity/songs.svelte';
	import { iconOf } from '$lib/entity/related.svelte';
	import { drawnBy, glyphOf } from '$lib/entity/entity-picture';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { page as address } from '$app/state';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { facetParams, rememberFacetNames } from '$lib/components/shell/facet-labels';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import type { components } from '$lib/api/schema';

	type Song = Pick<
		components['schemas']['SongSummary'],
		'id' | 'name' | 'locked' | 'cover_asset_id' | 'cover_upload_id' | 'item_count'
	> &
		// The opinions, the card's count row, its size, and the cover's moment, window and token.
		// Optional, because a row this page makes itself carries none of them.
		Partial<
			Pick<
				components['schemas']['SongSummary'],
				| 'favorite'
				| 'pinned'
				| 'rating'
				| 'counts'
				| 'size_bytes'
				| 'cover_at_ms'
				| 'cover_frame'
				| 'art'
				| 'artists'
				| 'vault'
			>
		>;

	/** What a song with no cover is drawn as: the Music page's own glyph. */
	const MUSIC = iconOf('song');

	let items = $state<Song[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state<string | null>(null);

	/* A page is the rows that fill a screenful, measured off a real card: the same machinery every
	   other card wall uses. */
	const paging = new CardPaging(24, 'wall.songs');

	/* The order the wall is in, SENT rather than applied here. Held in `./sort.svelte` because
	   opening a song unmounts this screen. */
	const order = $derived(songsSort.value);

	/* What the bar has filtered this shelf to, out of the address, as a derived string beside it so
	   the effects below wake only when it really changes. See the Photo Sets wall. */
	const narrowedBy = $derived(facetParams('song', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));
	/* What is typed in the box, and what the list is asked with once the typing settles. */
	let term = $state('');
	const prefix = $derived(wordsIn(address.url));
	const words = new WallWords();

	$effect(() => {
		const arrived = prefix;
		untrack(() => {
			if (words.echoed(arrived)) return;
			term = arrived;
		});
	});
	const listKey = $derived(`${narrowedKey}\n${prefix}`);

	/* Picking several at once, the same gesture and the same bar every other wall of cards has. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		items.filter((one) => !one.locked).map((one) => one.id)
	);

	function letGo(event: KeyboardEvent) {
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key === 'Escape' && gesture.escaped(event)) event.stopPropagation();
	}

	$effect(() => {
		const here = items.map((one) => one.id);
		untrack(() => selection.retain(here));
	});

	/* What this wall offers the bar above it: the shared orders, the card size and the filters
	   from the server. A cover is a still, so the wall plays nothing. */
	const mine = Symbol('songs-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'song',
			/* A chip for one artist renames that artist on every song, by its right-click: the
			   same verbs the artist's own press under a song's name opens. */
			chipVerbs: (field, value, label) =>
				field === ARTIST_FIELD && !value.startsWith('-')
					? artistVerbs({ id: value, name: label }, session.isAdmin)
					: [],
			resizable: true,
			playable: "A cover is a still, and a still doesn't play",
			sorts: [...SONG_ORDERS],
			sort: order,
			onSort: (next) => {
				songsSort.set(next);
				paging.forget();
				paging.offset = 0;
				forgetAnchor(address.url, path);
			}
		});
	});

	$effect(() => () => screenBar.release(mine));

	const byId = (id: string) => items.find((one) => one.id === id);

	/* The one artist this wall is filtered to, by the name the rows on it credit, or null where it is
	   filtered to none, to several, or to "not this artist". */
	const oneArtist = $derived.by(() => {
		const asked = address.url.searchParams.getAll(ARTIST_FIELD);
		if (asked.length !== 1 || asked[0].startsWith('-')) return null;
		for (const song of items) {
			const found = (song.artists ?? []).find((one) => one.id === asked[0]);
			if (found) return found;
		}
		return null;
	});

	/* The route this wall belongs to, captured once. See `$lib/grid/anchor`. */
	const path = address.url.pathname;
	let arriving = true;
	let askedFor = -1;
	let askedSize = -1;
	let askedOrder = '';
	let askedKey = '';

	async function load() {
		loading = true;
		failed = null;
		try {
			const page = await paging.fill(
				JSON.stringify([order, narrowedBy, prefix]),
				() => items,
				(ask) =>
					api.get<components['schemas']['SongList']>('/songs', {
						// The box's words match anywhere in a name, as every wall of things does.
						query: { ...narrowedBy, prefix, anywhere: 'true', ...asked(ask), sort: order }
					}),
				(answer) => ({
					rows: answer.items ?? [],
					total: answer.total ?? 0,
					offset: answer.offset ?? 0
				})
			);
			if (page === null) return;
			items = page.rows;
			total = page.total;
			/* What each artist id on the bar is called, from the rows that credit them: a chip
			   for `artists=` arrived at by a link or a reload draws the name, not the id. */
			rememberFacetNames(
				'artists',
				items.flatMap((one) =>
					(one.artists ?? []).map((artist) => ({
						value: artist.id,
						label: artist.name,
						count: 0
					}))
				)
			);
			paging.land(page.offset);
			rememberAnchor(address.url, path, items[0]?.id, page.offset);
			loading = false;
		} catch {
			failed = "Those couldn't be loaded.";
			loading = false;
		}
	}

	/* Re-run when the page moves, its size changes, or the order or filtering does. `loading` is
	   deliberately not read. See the Photo Sets wall, which says why at length. */
	$effect(() => {
		const wanted = paging.offset;
		const size = paging.size;
		const wantedOrder = order;
		const narrowing = listKey;
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
			if (anchored) paging.arrive(anchored);
			askedFor = paging.offset;
			askedSize = size;
			askedOrder = wantedOrder;
			askedKey = narrowing;
			void load();
		});
	});

	/* A different filter is a different list: back to its first page, and the anchor forgotten. */
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

	/* A file put on a song or taken off it, a share or a hide on a file changes which songs this
	   account may see and how many files each is on, and produces no event of its own. */
	reloadOnLibraryChange(load);

	/* Every verb this wall offers, for the bar AND for each card's menu, from the one registry. */
	const verbs = new WallVerbs({
		kind: () => 'song',
		rows: () =>
			items.map((one) => ({
				id: one.id,
				name: one.name,
				count: one.item_count,
				favorite: one.favorite,
				rating: one.rating,
				pinned: one.pinned,
				hidden: one.vault,
				/* What the merge sheet reads a row by: the rule every picker draws a thing by. */
				picture: drawnBy('song', one)
			})),
		changed: () => void load(),
		clear: () => selection.clear(),
		/* A pin moves the row to the top of this very list, so this wall offers it. */
		pins: true
	});

	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(items.map((one) => one.id)) : [id];
	}

	const pickedIds = $derived(selection.ordered(items.map((one) => one.id)));

	/* The row moves at once and the server's answer is kept: the same optimistic write the heart on
	   a tile makes. */
	function put(id: string, state: components['schemas']['SongStateView']) {
		items = items.map((one) => (one.id === id ? { ...one, ...state } : one));
	}

	async function heart(song: Song, wanted: boolean) {
		put(song.id, { favorite: wanted, rating: song.rating ?? null });
		try {
			const held = await api.put<components['schemas']['SongStateView']>(
				`/songs/${song.id}/favorite`,
				{ body: { favorite: wanted } }
			);
			put(song.id, held);
		} catch {
			put(song.id, { favorite: song.favorite ?? false, rating: song.rating ?? null });
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(song: Song, wanted: number | null) {
		put(song.id, { favorite: song.favorite ?? false, rating: wanted });
		try {
			const held = await api.put<components['schemas']['SongStateView']>(
				`/songs/${song.id}/rating`,
				{ body: { rating: wanted } }
			);
			put(song.id, held);
		} catch {
			put(song.id, { favorite: song.favorite ?? false, rating: song.rating ?? null });
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Files dragged from the grid onto a song. The one membership row is all that is written; no
	   file moves. A file carries one song, so a file on another moves to this one. */
	async function addDropped(assetIds: string[], songId: string) {
		const song = byId(songId);
		const named = song ? thing('song', songId, song.name) : 'that song';
		try {
			const done = await songs.add(songId, assetIds);
			toasts.show(
				done.changed === 0
					? ['Already on ', named]
					: [
							`Added ${counted(done.changed)} ${done.changed === 1 ? 'file' : 'files'} to `,
							named,
							'. Nothing moved.'
						],
				{ tone: 'success' }
			);
			announceSkipped(done);
			await load();
		} catch {
			toasts.show("Those couldn't be added", { tone: 'error' });
		}
	}
</script>

<svelte:head><title>Music</title></svelte:head>

<EntityGrid
	title="Music"
	icon={MUSIC}
	drawn={items.length}
	{total}
	{loading}
	failed={failed ?? undefined}
	measure={paging.cards}
	page={paging.showing}
	empty={emptyWallSays(
		'songs',
		prefix,
		Object.keys(narrowedBy).length > 0,
		session.isAdmin
			? 'No songs yet. A song is named when AcoustID recognizes a file, when a download page names one, or when you add one.'
			: 'None of the files you can see carries a song yet.'
	)}
>
	{#snippet pager()}
		<Pager
			offset={paging.offset}
			shown={items.length}
			{total}
			noun="songs"
			onfirst={() => paging.goTo(0, total)}
			onprevious={() => paging.step(-1, total)}
			onnext={() => paging.step(1, total)}
			onlast={() => paging.last(total)}
			onjump={(position) => paging.goTo(position - 1, total)}
		/>
	{/snippet}

	{#snippet controls()}
		<!-- Filtered to one artist: a door onto that artist's menu, named by the artist, the same
		     rows their right-click opens (Rename on every song). Nothing while the wall is filtered
		     to none, to several or to "not this artist". -->
		{#if oneArtist && artistVerbs(oneArtist, session.isAdmin).length > 0}
			<RowMenu
				verbs={artistVerbs(oneArtist, session.isAdmin)}
				ids={[oneArtist.id]}
				label="Options for {oneArtist.name}"
				words={oneArtist.name}
			/>
		{/if}
		<!-- The Add and the search, in the one shape every entity wall wears. Add opens the blank
		     record form at `/songs/new`; the box filters through the list route's `prefix`. -->
		<WallControls
			noun="song"
			plural="songs"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			onadd={session.isAdmin ? () => void goto('/songs/new') : undefined}
		/>
	{/snippet}

	{#each items as song (song.id)}
		{@const target = dropTarget({
			kind: 'asset',
			targetId: song.id,
			onassign: addDropped,
			/* A link dropped on a song: what it downloads is put on that song, as a link dropped on
			   a tag is filed under the tag. */
			onlink: (url, id) => void fetchOnto(url, 'song', id, song.name)
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
				href="/songs/{song.id}"
				swapAs={{ kind: 'song', id: song.id }}
				name={song.name}
				locked={song.locked}
				counts={cardCells('song', song.id, `/songs/${song.id}`, song.counts ?? {})}
				coverAssetId={song.cover_asset_id}
				coverUploadId={song.cover_upload_id}
				coverAtMs={song.cover_at_ms}
				coverFrame={song.cover_frame}
				art={song.art}
				glyph={glyphOf('song')}
				detail={filesSized(song.item_count, sizeOf(song))}
				hidden={song.vault}
				onhidden={() => verbs.askAboutHidden(song.id)}
				favorite={song.favorite ?? false}
				pinned={song.pinned ?? false}
				rating={song.rating ?? null}
				onfavorite={(next) => void heart(song, next)}
				onrate={(next) => void rate(song, next)}
				selected={selection.has(song.id)}
				id={song.id}
				onpressstart={(event) => gesture.pressStart(song.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(song.id, event)}
				dropping={target.over}
			>
				{#snippet byline()}
					<SongArtists artists={song.artists ?? []} />
				{/snippet}
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. -->
					<VerbMenuItems
						ids={targetIds(song.id)}
						subjectId={song.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(song.id)),
								pinned: verbs.allPinned(targetIds(song.id)),
								favorite: verbs.allFavorite(targetIds(song.id)),
								rating: verbs.sharedRating(targetIds(song.id)),
								handlers: verbs.handlers
							})
						)}
					/>
				{/snippet}
			</EntityCard>
		</div>
	{/each}
	{#snippet floating()}
		<EntitySelectionBar
			{selection}
			order={() => items.map((one) => one.id)}
			noun="song"
			showingHidden={verbs.allHidden(pickedIds)}
			pinned={verbs.allPinned(pickedIds)}
			favorite={verbs.allFavorite(pickedIds)}
			rating={verbs.sharedRating(pickedIds)}
			handlers={verbs.handlers}
		/>
	{/snippet}
</EntityGrid>

<svelte:window onkeydown={letGo} />

<!-- Every sheet a verb opens, drawn once for the wall by the registry. -->
<EntityWallFlows {verbs} />
<ArtistRenameDialog />
