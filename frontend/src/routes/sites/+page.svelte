<script lang="ts">
	import { untrack } from 'svelte';
	import { refusedMark } from '$lib/swap/refused';
	import { page as address } from '$app/state';
	import { WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { facetParams } from '$lib/components/shell/facet-labels';
	import { goto } from '$app/navigation';
	/*
	 * Sites: where media came from, drawn the same way People is.
	 *
	 * The same card and the same wall, deliberately. A site and a person are different kinds of
	 * thing (the whole slice under this exists so they never collapse into one field), but on a
	 * list screen they are the same four facts: a picture, a name, some counts, and what you think
	 * of it. Two screens that look alike and are built twice are two screens that stop looking
	 * alike the first time either is touched.
	 *
	 * Most rows here were never typed in. A download names the site it fetched from, and the site
	 * and the handle appear without anybody doing anything, which is the whole reason attribution
	 * is worth modelling as three things rather than one.
	 *
	 * Deleting a site takes its usernames with it and moves no file and no person, which is what
	 * its confirmation says (`delete_site` in `people/router.py`).
	 */
	import { Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { enrichBoxes, loadEnrichBoxes } from '$lib/entity/enrichment.svelte';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import { siteRow } from '$lib/entity/site-row';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { filesSaid, filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import {
		ENTITY_SORTS,
		people,
		SITES_PER_PAGE,
		sites,
		type Site
	} from '$lib/people/people.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { anchorIn, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';

	/* Which page, at what size, was last asked for, so a settled screen does not ask for the same
	   thing twice. See the People wall for why the guard is untracked. */
	let askedFor = -1;
	let askedSize = -1;
	/** ...and under which filter, so a facet going on or off re-asks from the same place. */
	let askedNarrowing = '';
	/** ...and under which search, so a letter typed re-asks from the first page. */
	let askedPrefix = '';

	/* What has been typed into the search box, and what the wall was last asked for.
	 *
	 * SENT to the list route as its `prefix`, never applied to the rows in hand: this wall is a page
	 * of a longer list, so filtering what has already arrived would quietly hide every site past the
	 * page. The People wall's own note says the same about the same mistake.
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

	/* What the bar has filtered this wall to, out of the address: only this noun's facets, never
	   the order or the page position. A derived STRING beside it, because a derived only propagates
	   when its value changes and the effect below must not wake on every address change. */
	const narrowedBy = $derived(facetParams('site', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));

	/* Whole rows of the window, the same way every other wall pages (see `CardPaging`). */
	const paging = new CardPaging(SITES_PER_PAGE, 'wall.sites');

	/* The route this wall belongs to, captured once, so a page landing under a row somebody has just
	   opened can be told from one landing on the wall itself. See `$lib/grid/anchor`. */
	const path = address.url.pathname;

	/* True exactly once: the first settle, which is somebody arriving at a link. After that the
	   anchor in the address is one WE wrote, for the question being asked at the time. So reading
	   it again on a new question would start the new one at the old one's position. */
	let arriving = true;

	/* Picking several, the same Selection and the same long press as the wall of files. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		shown.filter((site) => !site.locked).map((site) => site.id)
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
		const here = shown.map((site) => site.id);
		untrack(() => selection.retain(here));
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
		/* Named so the effect re-runs when the page moves. The page SIZE is read too: a taller window
		   holds more rows, so a resize is a reason to re-fetch. */
		const wanted = paging.offset;
		const size = paging.size;
		const narrowing = narrowedKey;
		// Read here rather than inside the untracked block, exactly as the page is: a letter typed
		// is a different list and has to wake this.
		const wantedPrefix = prefix;
		const stale = !sites.loaded;
		// UNTRACKED, and that is load-bearing. Reading the address inside an effect makes the effect
		// depend on it, and this effect's own answer WRITES the address, so it would re-run itself.
		untrack(() => {
			if (arriving) {
				arriving = false;
				// The anchor lives on the paging until the page lands (see `CardPaging.land`).
				paging.arrive(anchorIn(address.url));
			}
			const narrowed = askedNarrowing !== narrowing || askedPrefix !== wantedPrefix;
			if (!stale && !narrowed && askedFor === wanted && askedSize === size) return;
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
			askedNarrowing = narrowing;
			askedPrefix = wantedPrefix;
			void sites
				.fill(paging, sites.sort, narrowedBy, wantedPrefix)
				// Written after the page lands, because turning to an anchor knows the offset it is
				// going to only once the answer arrives. The offset itself is landed inside `fill`,
				// with the rows, and its re-run of this effect asks nothing (see `CardPaging.land`).
				.then(() => rememberAnchor(address.url, path, sites.items[0]?.id, paging.offset));
		});
	});

	async function heart(site: Site, favorite: boolean) {
		try {
			await sites.setFavorite(site.id, favorite);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(site: Site, rating: number | null) {
		try {
			await sites.setRating(site.id, rating);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Two counts, and they answer different questions: how much of the library came from here,
	 * and how many PEOPLE it is of. Each is said once. The files are the words under the name, as
	 * on every wall; the people are the People figure in the row under them, which opens the tab
	 * they are on, so a second count of people in the words would only repeat it. People rather than
	 * usernames: a username is a name on a site and a person is who it belongs to, so somebody
	 * with three here is one.
	 */
	/* No mark of the Site's own in the corner of its card: the card carries the Site's NAME
	   under the picture, so a logo over a cover somebody chose would say the same thing a second
	   time.

	   `EntityCard.sites` is for a card about something that is ON several sites (a person, a
	   Photo Set), where the marks are the only place that fact appears. A Site's own card would
	   be the one caller for which the prop meant "this thing is itself", which is not the
	   question the prop asks. */

	const shown = $derived(sites.items);

	/* What this wall offers the bar. A site is an entity rather than a file, so no funnel and no
	   tiles to size: an order, and this wall's own sharing filter. See `screen-bar`. */
	const mine = Symbol('sites-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'site',
			resizable: true,
			playable: "A card is a Site mark, and a mark doesn't play",
			// For everybody, not only an admin. See the People wall: the server refuses a guest
			// nothing here, and the menu disappearing on one screen and not another is the fault
			// this bar is for.
			sorts: [...UNIVERSAL_SORTS, ...ENTITY_SORTS],
			sort: sites.sort,
			onSort: (next) => {
				/* A new order is a new list, so the page it was on means nothing in it, and neither
				   does the anchor in the address, which names a row of the list that has just been
				   replaced. Reset first, then fetch from the top. */
				paging.offset = 0;
				askedFor = 0;
				forgetAnchor(address.url, path);
				paging.forget();
				void sites.fill(paging, next, undefined, prefix);
			}
		});
	});

	$effect(() => () => screenBar.release(mine));

	/*
	 * Every verb this wall offers, for the bar AND for each card's menu, from the one registry.
	 *
	 * No flow is written out here (a rename form, a delete, a share, a reach report, a hide, a
	 * merge, a tag sheet): they are `wall-verbs.svelte.ts`'s, the same ones the walls on an
	 * entity's tabs use. See the People wall for the rest.
	 */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});

	/* Read the wall again, with the order and the search it is showing. */
	function reread() {
		void sites.fill(paging, sites.sort, undefined, prefix);
	}

	const verbs = new WallVerbs({
		kind: () => 'site',
		rows: () =>
			shown.map((site) => ({
				id: site.id,
				name: site.name,
				count: site.asset_count,
				favorite: site.favorite,
				hidden: site.hidden,
				rating: site.rating,
				pinned: site.pinned,
				picture: siteRow(site).picture
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
	const enrichment = verbs.enrichment('site');

	/* A selection of files dragged onto a site: they came from there. The same write the
	   `Add to > Site` verb makes: one call, so a drag and a menu row cannot come to mean two
	   different things. No file is moved; a username row on that site is what carries it. */
	async function fileDropped(assetIds: string[], siteId: string) {
		const site = sites.byId(siteId);
		const named = site ? thing('site', siteId, site.name) : 'that Site';
		try {
			// The PEOPLE store holds this one. Filing a file under a site writes an account row on
			// it, which is that store's territory. So the call lives there and this reads it
			// rather than growing a second copy pointed at the same endpoint.
			const done = await people.filedUnder(assetIds, [siteId]);
			toasts.show(
				done.changed === 0
					? ['Already filed under ', named]
					: [`Filed ${filesSaid(done.changed)} under `, named, '. Nothing moved.'],
				{ tone: 'success' }
			);
			announceSkipped(done);
		} catch {
			toasts.show("Those couldn't be filed", { tone: 'error' });
		}
	}

	/* And again whenever a share or a restrict moves. See the helper: this list is scoped, so what
	   belongs in it changes with no import to announce it. */
	reloadOnLibraryChange(reread);
</script>

<svelte:head><title>Sites</title></svelte:head>

<EntityGrid
	icon="public"
	title="Sites"
	drawn={shown.length}
	total={sites.total}
	loading={sites.loading}
	measure={paging.cards}
	page={paging.showing}
	failed={sites.failed ? "The Sites couldn't be loaded." : null}
	empty={emptyWallSays(
		'Sites',
		prefix,
		Object.keys(narrowedBy).length > 0,
		session.isAdmin
			? 'No Sites yet. Most appear on their own the first time something is downloaded.'
			: 'No Sites yet.'
	)}
>
	<!--
		The sentence sits IN the header, beside the title it describes.

		Not a `<p>` in the page's own flow after the grid, which would be outside `PageFrame`
		entirely: the frame is `block-size: 100%`, so a sibling under it makes the route taller than
		the window by exactly that paragraph: the wall clipped mid-card, the pager above the fold,
		and the sentence under the pager looking like something that has come loose. See the note on
		the People wall for the rest of the reasoning.
	-->

	{#snippet pager()}
		<Pager
			offset={paging.offset}
			shown={shown.length}
			total={sites.total}
			noun="Sites"
			onfirst={() => paging.goTo(0, sites.total)}
			onprevious={() => paging.step(-1, sites.total)}
			onnext={() => paging.step(1, sites.total)}
			onlast={() => paging.last(sites.total)}
			onjump={(position) => paging.goTo(position - 1, sites.total)}
		/>
	{/snippet}

	{#snippet controls()}
		<!-- The order and the sharing filter are on the shared bar above, with every other
		     screen's. What this page does is say what it offers; see the publish in the script.
		     The box and the Add are `WallControls`'s, which is the one shape all five walls wear. -->
		<WallControls
			noun="Site"
			plural="Sites"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			onadd={session.isAdmin ? () => void goto('/sites/new') : undefined}
		/>
		<!-- No rename form here: Rename opens the registry's box (`EntityWallFlows`), the one every
		     wall and every tab uses. Add opens the blank record at `/sites/new`. -->
	{/snippet}

	{#each shown as site (site.id)}
		<!-- The same target the other four walls carry: a file dragged from the grid says "this came
		     from here", and a link dragged in from another tab says "fetch it and say the same".
		     Both write an attribution and nothing else. No file on the disk is touched either way. -->
		{@const target = dropTarget({
			kind: 'asset',
			targetId: site.id,
			onassign: fileDropped,
			onlink: (url, id) => void fetchOnto(url, 'site', id, site.name)
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
				href="/sites/{site.id}"
				swapAs={{ kind: 'site', id: site.id, refused: refusedMark(site) }}
				dropping={target.over}
				droppingKind="site"
				name={site.name}
				locked={site.locked}
				counts={cardCells('site', site.id, `/sites/${site.id}`, site.counts)}
				siteName={site.name}
				siteIcon={site.icon}
				selected={selection.has(site.id)}
				id={site.id}
				onpressstart={(event) => gesture.pressStart(site.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(site.id, event)}
				coverAssetId={site.cover_asset_id}
				coverUploadId={site.cover_upload_id}
				coverAtMs={site.cover_at_ms}
				coverFrame={site.cover_frame}
				art={site.art}
				shared={site.shared}
				restricted={site.restricted}
				shared_here={site.shared_here}
				restricted_here={site.restricted_here}
				hidden={site.hidden}
				onsharing={session.isAdmin ? () => verbs.askToShare([site.id]) : undefined}
				onhidden={() => verbs.askAboutHidden(site.id)}
				detail={filesSized(site.asset_count, sizeOf(site))}
				favorite={site.favorite}
				pinned={site.pinned}
				rating={site.rating}
				onfavorite={(next) => heart(site, next)}
				onrate={(next) => rate(site, next)}
			>
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. Not written out
				     again here, which is what stops the two from coming apart. -->
					<VerbMenuItems
						ids={targetIds(site.id)}
						subjectId={site.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(site.id)),
								pinned: verbs.allPinned(targetIds(site.id)),
								favorite: verbs.allFavorite(targetIds(site.id)),
								rating: verbs.sharedRating(targetIds(site.id)),
								handlers: verbs.handlers,
								keptLocal: enrichment.allKeptLocal(targetIds(site.id)),
								enrichRefused: enrichment.anyRefused(targetIds(site.id)),
								...(enrichment.why(targetIds(site.id))
									? { enrichWhy: enrichment.why(targetIds(site.id)) as string }
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
			order={() => shown.map((site) => site.id)}
			noun="Site"
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
