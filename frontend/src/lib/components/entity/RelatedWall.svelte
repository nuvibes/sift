<script lang="ts">
	/*
	 * One tab of an entity page: the things of another kind this thing's files reach, as the same
	 * EntityCards the walls draw. A card's count is the count in this context, from the server.
	 * NOT ON THE GALLERY: it fetches on mount, so an entry for it would draw whatever the live
	 * library held; EntityGrid, EntityCard and Tabs are there.
	 */
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { wallHeld } from '$lib/components/entity/TabLayer.svelte';
	import { untrack, type Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import {
		kindOf,
		loadRelated,
		narrowingFor,
		nounFor,
		ordersFor,
		pinnableOf,
		relatedHref,
		RELATED_PER_PAGE,
		NAME_WORDS,
		TabWords,
		tabSort,
		type EntityKind,
		type RelatedKind,
		type RelatedRow
	} from '$lib/entity/related.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import { enrichBoxes, loadEnrichBoxes } from '$lib/entity/enrichment.svelte';
	import { personRow } from '$lib/people/person-row';
	import { siteRow } from '$lib/entity/site-row';
	import { session } from '$lib/shell/session.svelte';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import { LOOP_SOURCE } from '$lib/grid/grid.svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { isPicked, pickFieldOf, picksOf, togglePick } from '$lib/components/entity/picks';
	import { filesSaid, picturesSaid, sizeOf, withSize } from '$lib/entity/entity-counts';
	import { fieldOf } from '$lib/components/shell/facet-labels';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { glyphOf } from '$lib/entity/entity-picture';
	import SongArtists from '$lib/components/entity/SongArtists.svelte';
	import ArtistRenameDialog from '$lib/components/entity/ArtistRenameDialog.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { emptyWallSays } from '$lib/components/shell/wall-words';

	interface Props {
		on: EntityKind;
		id: string;
		/** Its NAME, which is what a card carries away as a filter. See `hrefFor`. */
		named?: string;
		/** Which wall to draw. Never `files`: the media grid draws that one. */
		showing: Exclude<RelatedKind, 'files'>;
		title: string;
		icon: IconName;
		/** The page's tab strip, drawn inline with this wall's heading exactly as it is on Files. */
		beside?: Snippet;
		/** Let the tab row name the section instead of the title. See `PageHeader.titleHidden`. */
		titleHidden?: boolean;
		/** The page's identity band, on every tab. */
		above?: Snippet;
		/** The trail for the frame's band, the same one on every tab of this page. */
		crumbs?: Crumb[];
		/** What the wall found, and whether words narrowed it: then it is not the tab's number. */
		oncount?: (total: number, searched: boolean) => void;
		/**
		 * What the page knows about one card, drawn under its facts; handed the kind it was read
		 * as.
		 */
		under?: Snippet<[RelatedRow, Exclude<RelatedKind, 'files'>]>;
		/**
		 * Cards the page draws after this wall's, on its last page (a Site's unclaimed usernames).
		 */
		after?: Snippet;
		trailing?: number;
	}

	let {
		on,
		id,
		named,
		showing,
		title,
		icon,
		beside,
		titleHidden = false,
		above,
		crumbs,
		oncount,
		under,
		after,
		trailing = 0
	}: Props = $props();

	let rows = $state<RelatedRow[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state<string | null>(null);

	/* Which tab the rows on screen were read for; the last tab's stay until the answer lands. */
	let rowsOf = $state<Exclude<RelatedKind, 'files'>>(untrack(() => showing));
	let rowsAt = $state(0);

	/* Bumped when an answer lands on an empty wall, the only arrival that plays the entrance. */
	let entrance = $state(0);

	/** The last answer is on screen while the next is coming. See `EntityGrid.held`. */
	const held = $derived(loading && rows.length > 0);

	/* An empty answer is held too: its sentence stays until the next tab's answer lands. */
	let answeredEmpty = $state<string | null>(null);
	const heldEmpty = $derived(loading && rows.length === 0 && answeredEmpty !== null);
	/** Whether the rows on screen were read for the tab that is chosen. */
	const settled = $derived(rowsOf === showing);

	/* One paging per kind of card, so each kind keeps its own measured size (`CardPaging`). */
	const paging = $derived(new CardPaging(RELATED_PER_PAGE, `related.${showing}`));

	/* The box's words: a name, a Loop's or a card's, so never the query language's `q`. */
	const tabWords = new TabWords(NAME_WORDS);
	const words = $derived(tabWords.asked);

	/* The order, remembered per kind of tab as the walls remember theirs. */
	const order = $derived(tabSort(showing));
	const sort = $derived(order.value);

	/* What the wall is asked: a different one starts at the top and is read loudly. */
	function here() {
		return { on, id, showing, words, sort };
	}
	type Where = ReturnType<typeof here>;

	function keyOf(where: Where): string {
		return `${where.on}/${where.id}/${where.showing}\n${where.sort}\n${where.words}`;
	}

	/* What one row is called, for the bar's sentence and the pager; not the tab's heading. */
	const rowNoun = $derived(nounFor(showing));
	const heldNoun = $derived(nounFor(rowsOf));

	/* The page last asked for, so a settled screen asks once: the fetch writes state read here. */
	let askedFor = -1;
	let askedSize = -1;
	let askedWhere = '';

	/*
	 * What the reset was done for, kept apart from what was fetched (RelatedWall.svelte.test.ts).
	 */
	let resetFor = '';

	/* So a slower answer for a tab already left cannot land under this one. */
	let generation = 0;

	/* One read, two ways: loud keeps the old cards dimmed until the answer; quiet changes nothing
	   until the rows land, for a pin, which moves a card in the server's order. */
	async function read(where: Where, { quiet = false } = {}) {
		const wanted = ++generation;
		if (!quiet) loading = true;
		failed = null;
		try {
			/* Through the paging, so a first measurement trims or extends rather than re-asking. */
			const page = await paging.fill(
				keyOf(where),
				() => rows,
				async (ask) => {
					const offset = 'offset' in ask ? ask.offset : 0;
					const got = await loadRelated(where.on, where.id, where.showing, {
						limit: ask.limit,
						offset,
						words: where.words,
						sort: where.sort
					});
					return { ...got, offset };
				},
				(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset })
			);
			if (page === null || wanted !== generation) return;
			if (rows.length === 0) entrance += 1;
			rows = page.rows;
			total = page.total;
			rowsOf = where.showing;
			rowsAt = page.offset;
			answeredEmpty = page.rows.length === 0 ? emptyWords : null;
			oncount?.(page.total, Boolean(where.words));
		} catch {
			if (wanted !== generation) return;
			// A quiet re-read that fails leaves the wall alone; a loud one says so.
			if (!quiet) {
				failed = 'That could not be loaded.';
				rows = [];
				total = 0;
				answeredEmpty = null;
			}
		} finally {
			if (wanted === generation) loading = false;
		}
	}

	/* A different tab or subject starts at the top: a pre-effect before the loader, so the reset
	   lands in the same flush and draw; old rows stay drawn as the kind they were read for. */
	$effect.pre(() => {
		const where = keyOf(here());
		untrack(() => {
			if (where === resetFor) return;
			resetFor = where;
			paging.offset = 0;
			loading = true;
			/* The Loops tab is the media grid; its rows are never drawn here. */
			if (showing === 'loops' || rowsOf === 'loops') {
				rows = [];
				total = 0;
			}
		});
	});

	$effect(() => {
		/* The page and its size, so a resize re-asks. */
		const wanted = paging.offset;
		const size = paging.size;
		const where = here();
		const key = keyOf(where);
		untrack(() => {
			if (askedWhere === key && askedFor === wanted && askedSize === size) return;
			/* Only the size moved: read quietly, so `fill` can trim rows it still holds. */
			const resized = askedWhere === key && askedFor === wanted;
			askedWhere = key;
			askedFor = wanted;
			askedSize = size;
			void read(where, { quiet: resized });
		});
	});

	/* And again, quietly, when the library moves. */
	reloadOnLibraryChange(() => void read(here(), { quiet: true }));

	/* The page's own cards go after the last of this wall's, so they are drawn once, at the end. */
	const onLastPage = $derived(
		!(loading && rows.length === 0) && settled && rowsAt + rows.length >= total
	);
	const trailingHere = $derived(after && onLastPage ? trailing : 0);

	/* What the bar can do over the cards. The Loops tab is the media grid, which publishes its own. */
	const mine = Symbol('related-wall');
	const leaving = wallHeld();

	$effect(() => {
		if (showing === 'loops') {
			screenBar.release(mine);
			return;
		}
		if (leaving()) return;
		const chosen = order;
		screenBar.publish(mine, {
			filterable: `This wall is ${title.toLowerCase()}, not files`,
			resizable: true,
			playable: 'A card is a picture and a name, and neither plays',
			sorts: ordersFor(showing),
			sort,
			onSort: (next) => chosen.set(next)
		});
	});

	$effect(() => () => screenBar.release(mine));

	/* Where a card leads, from related.svelte, which also decides what the card counts. */
	function hrefFor(row: RelatedRow): string {
		return nothingHere(row)
			? relatedHref(on, rowsOf, row.id)
			: relatedHref(on, rowsOf, row.id, named);
	}

	/*
	 * A card counting nought here does not pick, which would filter to none; its picture is a link.
	 */
	function nothingHere(row: RelatedRow): boolean {
		return (row.item_count ?? row.asset_count) === 0;
	}

	/** The line under the name. Whatever this kind of thing is counted in. */
	function detailFor(row: RelatedRow): string {
		const count = row.item_count ?? row.asset_count ?? 0;
		// The one sentence every wall draws; a Photo Set holds pictures, a collection files.
		const counted = rowsOf === 'photo_sets' ? picturesSaid(count) : filesSaid(count);
		// A Site's People tab: this person's files from this Site.
		const said = on === 'site' && rowsOf === 'people' ? `${counted} from this Site` : counted;
		// And how big those same files are, off the same row as the count.
		return withSize(said, count, sizeOf(row));
	}

	/* An empty wall names what was looked for and where. */
	const emptyWords = $derived(
		emptyWallSays(
			rowNoun.many,
			words,
			false,
			showing === 'people'
				? 'Nobody else turns up on these files.'
				: showing === 'sites_within'
					? 'No other Site is part of this one.'
					: showing === 'tags_within'
						? 'No tag is filed under this one yet.'
						: `Nothing here carries ${title.toLowerCase()} yet.`
		)
	);

	/* The shared gesture and selection, and the verbs that need only the row; the flows that live
	   on the thing's own wall arrive by being handed in. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () => rows.map((row) => row.id));
	const pinnable = $derived(pinnableOf(showing));

	/** The selection when this row is part of one, this row alone otherwise: the grid's own rule. */
	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(rows.map((row) => row.id)) : [id];
	}

	/*
	 * Every verb the card's thing has, from wall-verbs.svelte.ts, the one registry for all kinds.
	 */
	const wallKind = $derived(kindOf(showing));
	const verbs = new WallVerbs({
		kind: () => wallKind ?? 'person',
		/* No count: here it is in context, and a delete's confirmation would understate the act. */
		rows: () =>
			rows.map((row) => ({
				id: row.id,
				name: row.name,
				favorite: row.favorite ?? false,
				hidden: hiddenOf(row),
				rating: row.rating ?? null,
				pinned: row.pinned ?? false,
				/* And the picture, for the merge sheet, by the builders every picker uses. */
				picture: pictureOf(row)
			})),
		/* Quietly: the rows stay until the new ones land. */
		changed: () => void read(here(), { quiet: true }),
		clear: () => selection.clear(),
		get pins() {
			return pinnable !== null && wallKind !== null;
		}
	});

	/* One spelling for the two the kinds use on the wire. See `WallRow.hidden`. */
	function hiddenOf(row: RelatedRow): boolean {
		return row.vault ?? row.hidden ?? false;
	}

	/* A row's picture, only for the kinds a merge exists for. */
	function pictureOf(row: RelatedRow) {
		if (wallKind === 'person') return personRow(row).picture;
		if (wallKind === 'site') return siteRow(row).picture;
		return undefined;
	}

	/* Whether the Enrich rows are refused, from the holder the handlers refuse from. */
	function enrichState(ids: string[]): {
		keptLocal?: boolean;
		enrichRefused?: boolean;
		enrichWhy?: string;
		enrichBoxes?: readonly { word: string; name: string }[];
	} {
		const asking = verbs.facts.enrichAs;
		if (!asking) return {};
		const stands = verbs.enrichment(asking);
		const why = stands.why(ids);
		return {
			keptLocal: stands.allKeptLocal(ids),
			enrichRefused: stands.anyRefused(ids),
			...(why ? { enrichWhy: why } : {}),
			// Auto-enrich's row per box, as the walls draw it. See `autoEnrichRows`.
			enrichBoxes: enrichBoxes()
		};
	}

	/* The box list, loaded once for an admin: the same list every wall's flyout reads. */
	$effect(() => {
		if (session.isAdmin && verbs.facts.enrichAs) loadEnrichBoxes();
	});

	/* Picking a card filters this page's files: a press on its picture toggles the pick (wash and
	 * mark, a parameter in the address through `replaceState`), the name stays a link, and a verb
	 * selection in progress wins. See `picks.ts`. */
	const pickField = $derived(pickFieldOf(showing));
	/* The picks the cards on screen wear, which are their own tab's while a new tab is coming. */
	const drawnPickField = $derived(pickFieldOf(rowsOf));
	const drawnKind = $derived(kindOf(rowsOf));
	const picks = $derived(picksOf(page.url));

	function pickRow(row: RelatedRow) {
		if (!pickField) return;
		void goto(togglePick(page.url, { field: pickField, name: row.name }), {
			replaceState: true,
			keepFocus: true,
			noScroll: true
		});
	}

	/* The entity walls' own filtering, as the media grid's query. */
	const narrowing = $derived(Object.fromEntries(narrowingFor(on, id, showing)));
	const loopsAsked = $derived(words ? { ...narrowing, [NAME_WORDS]: words } : narrowing);

	/* The page by name, as the filter bar reads it, so the Loops panel counts this page's marks;
	   a tag reaching a mark directly cannot be counted in that language. */
	const filesQuery = $derived(named ? { [fieldOf(on)]: named } : undefined);
</script>

<!-- The search box at the end of the tab row, searching what this tab lists. -->
{#snippet searchBox()}
	<WallControls
		noun={rowNoun.one}
		plural={rowNoun.many}
		bind:term={tabWords.term}
		onsettled={(typed) => tabWords.write(typed)}
	/>
{/snippet}

<!-- A Loop is media, not a named thing, so the Loops tab is the media grid Browse draws. -->
{#if showing === 'loops'}
	<AssetGrid
		source={LOOP_SOURCE}
		query={loopsAsked}
		{filesQuery}
		words={NAME_WORDS}
		{title}
		{icon}
		{beside}
		{titleHidden}
		{above}
		{crumbs}
		titleLevel={2}
		oncount={(found) => oncount?.(found, Boolean(words))}
		empty={emptyWords}
	>
		{#snippet tools()}
			{@render searchBox()}
		{/snippet}
	</AssetGrid>
{:else}
	<EntityGrid
		{title}
		{icon}
		{beside}
		{titleHidden}
		{above}
		{crumbs}
		drawn={rows.length + trailingHere}
		{total}
		loading={loading && !heldEmpty}
		failed={failed ?? undefined}
		{held}
		empty={heldEmpty && answeredEmpty !== null ? answeredEmpty : emptyWords}
		measure={settled ? paging.cards : undefined}
		page={loading && rows.length === 0 ? null : String(entrance)}
	>
		{#snippet controls()}
			{@render searchBox()}
		{/snippet}
		{#snippet pager()}
			<!-- In the frame's footer, paging what the heading counts; while a tab's answer is coming it
			reads the cards on screen and its presses wait. -->
			<Pager
				offset={rowsAt}
				shown={rows.length}
				{total}
				{loading}
				noun={heldNoun.many}
				onfirst={() => settled && paging.goTo(0, total)}
				onprevious={() => settled && paging.step(-1, total)}
				onnext={() => settled && paging.step(1, total)}
				onlast={() => settled && paging.last(total)}
				onjump={(position) => settled && paging.goTo(position - 1, total)}
			/>
		{/snippet}
		{#each rows as row (row.id)}
			<EntityCard
				href={hrefFor(row)}
				name={row.name}
				locked={row.locked ?? false}
				coverAssetId={row.cover_asset_id}
				coverUploadId={row.cover_upload_id}
				coverAtMs={row.cover_at_ms}
				coverFrame={row.cover_frame}
				coverTrackId={row.cover_track_id}
				creatorName={rowsOf === 'people' ? row.name : null}
				siteName={rowsOf === 'sites' || rowsOf === 'sites_within' ? row.name : null}
				siteIcon={row.icon ?? false}
				art={row.art}
				glyph={drawnKind ? glyphOf(drawnKind) : undefined}
				detail={detailFor(row)}
				pinned={row.pinned ?? false}
				shared={row.shared}
				restricted={row.restricted}
				shared_here={row.shared_here}
				restricted_here={row.restricted_here}
				hidden={hiddenOf(row)}
				onsharing={session.isAdmin && wallKind ? () => verbs.askToShare([row.id]) : undefined}
				onhidden={wallKind ? () => verbs.askAboutHidden(row.id) : undefined}
				id={row.id}
				selected={selection.has(row.id)}
				onpressstart={(event) => gesture.pressStart(row.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(row.id, event)}
				picked={drawnPickField !== null && isPicked(picks, drawnPickField, row.name)}
				onpick={pickField && !nothingHere(row) ? () => pickRow(row) : undefined}
			>
				<!-- Declared for every card; it renders nothing where the page handed nothing. -->
				{#snippet beneath()}
					{@render under?.(row, rowsOf)}
				{/snippet}
				<!-- A song card names who the song credits. -->
				{#snippet byline()}
					{#if rowsOf === 'songs'}
						<SongArtists artists={row.artists ?? []} />
					{/if}
				{/snippet}
				{#snippet menu()}
					<!-- The same declared verbs the bar draws. -->
					<VerbMenuItems
						ids={targetIds(row.id)}
						subjectId={row.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(row.id)),
								pinned: verbs.allPinned(targetIds(row.id)),
								favorite: verbs.allFavorite(targetIds(row.id)),
								rating: verbs.sharedRating(targetIds(row.id)),
								...enrichState(targetIds(row.id)),
								handlers: verbs.handlers
							})
						)}
					/>
				{/snippet}
			</EntityCard>
		{/each}
		{#if trailingHere > 0}
			{@render after?.()}
		{/if}
	</EntityGrid>
{/if}

<!-- The bar, only where there is a verb to offer. -->
{#if pinnable}
	<EntitySelectionBar
		{selection}
		order={() => rows.map((row) => row.id)}
		noun={rowNoun.one}
		plural={rowNoun.many}
		showingHidden={verbs.allHidden(selection.ordered(rows.map((row) => row.id)))}
		pinned={verbs.allPinned(selection.ordered(rows.map((row) => row.id)))}
		favorite={verbs.allFavorite(selection.ordered(rows.map((row) => row.id)))}
		rating={verbs.sharedRating(selection.ordered(rows.map((row) => row.id)))}
		handlers={verbs.handlers}
		enrichBoxes={verbs.facts.enrichAs ? enrichBoxes() : []}
	/>
{/if}

<!-- The sheets those verbs open. Drawn once for the wall, whichever card asked. -->
<!-- A Music tab's cards name their artists, and an artist's right-click renames them. -->
{#if showing === 'songs'}
	<ArtistRenameDialog />
{/if}

{#if wallKind}
	<EntityWallFlows {verbs} />
{/if}
