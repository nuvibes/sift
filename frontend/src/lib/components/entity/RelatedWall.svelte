<script lang="ts">
	/*
	 * One tab's worth of an entity page: the things of some other kind that this thing's files reach.
	 *
	 * The same `EntityCard` the People, Tags, Sites and Collections walls draw, not a second
	 * arrangement of it, so a person's card looks the same whether it is on the People wall or on
	 * a tag's "People" tab. A wall that redrew its own copies would be one more implementation to
	 * keep in step, which is the fault the whole design ratchet exists to prevent.
	 *
	 * ## The count under each name is the count in THIS context
	 *
	 * A tag on Jane's page reading "12" means twelve of Jane's files, not twelve in the library. The
	 * server does the filtering inside the one statement that decides what may be seen, so the
	 * number and the cards come from one question, and this component never computes a count of
	 * its own from the rows it happens to be holding.
	 *
	 * NOT ON THE GALLERY: it fetches on mount, so an entry for it would draw whatever the live
	 * library happened to hold rather than a fixed example: a gallery that changes with the data
	 * is not showing the component. What it draws IS on the gallery: `EntityGrid` and `EntityCard`,
	 * which is everything this composes, and `Tabs` beside them.
	 */
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { untrack, type Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import {
		kindOf,
		loadRelated,
		narrowingFor,
		nounFor,
		pinnableOf,
		relatedHref,
		RELATED_PER_PAGE,
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

	interface Props {
		/** What kind of page this wall is on. */
		on: EntityKind;
		/** The id of the thing the page is about. */
		id: string;
		/** Its NAME, which is what a card carries away as a filter. See `hrefFor`. */
		named?: string;
		/** Which wall to draw. Never `files`: the media grid draws that one. */
		showing: Exclude<RelatedKind, 'files'>;
		/** The heading, which is the chosen tab's own word. */
		title: string;
		icon: IconName;
		/** The page's tab strip, drawn inline with this wall's heading exactly as it is on Files. */
		beside?: Snippet;
		/** Let the tab row name the section instead of the title. See `PageHeader.titleHidden`. */
		titleHidden?: boolean;
		/** The page's identity band. Drawn on every tab, because whose page this is does not
		 *  change with what is being shown OF them. */
		above?: Snippet;
		/** The trail for the frame's band, the same one on every tab of this page. */
		crumbs?: Crumb[];
		/** Told what the wall found, so the tab strip above can put the number beside its word. */
		oncount?: (total: number) => void;
		/**
		 * What the PAGE knows about one card, drawn under that card's facts. See
		 * `EntityCard.beneath`. A person's page hands its handles on each Site to the Sites tab and a
		 * Site's page hands each person's handles on it to the People tab; nothing else does. Handed
		 * the row and the kind of card it is drawn as, which is the tab it was READ for: while the
		 * next tab's answer is coming, the last tab's cards are still on screen (see `held`).
		 */
		under?: Snippet<[RelatedRow, Exclude<RelatedKind, 'files'>]>;
		/**
		 * Cards the PAGE draws after this wall's own, on its last page: a Site's People tab ends
		 * with the usernames on it that belong to nobody yet. `trailing` is how many, so a wall
		 * holding only those is not drawn as empty.
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

	/*
	 * Which tab the rows on screen were read for, and where in it.
	 *
	 * Not always the tab chosen. A tab pressed keeps the last tab's cards on screen until the new
	 * answer lands (`held`), so a wall never goes empty between two answers. Those cards are drawn
	 * as what they ARE: a person's card on its way out of a People tab keeps its face and its link
	 * while the heading above already says Sites. Everything a card is drawn from reads this, never
	 * `showing`; the heading, the empty words and the bar read `showing`.
	 */
	let rowsOf = $state<Exclude<RelatedKind, 'files'>>(untrack(() => showing));
	let rowsAt = $state(0);

	/*
	 * Bumped when an answer lands on a wall that had nothing on it, which is when the wall
	 * ARRIVES and plays its entrance. A tab changed or a page turned keeps the cards on screen
	 * until the answer, so the wall never went: the dimming lifting is the arrival, and a fade from
	 * nothing played over it would be a flash.
	 */
	let entrance = $state(0);

	/** The last answer is on screen while the next is coming. See `EntityGrid.held`. */
	const held = $derived(loading && rows.length > 0);

	/* An empty answer is held too: its sentence stays until the next tab's answer lands. */
	let answeredEmpty = $state<string | null>(null);
	const heldEmpty = $derived(loading && rows.length === 0 && answeredEmpty !== null);
	/** Whether the rows on screen were read for the tab that is chosen. */
	const settled = $derived(rowsOf === showing);

	/*
	 * The wall pages through the shared pager. The heading above takes the scoped total, so drawing
	 * only a first page with no pager would show "People 200" over sixty cards with no way to reach
	 * the rest, while both numbers stayed correct. `CardPaging` measures a real card and the box it
	 * scrolls in and asks for whole rows, which is also what makes the size slider below mean
	 * something here.
	 *
	 * One paging per kind of card, named for it, so the session remembers what each kind measured
	 * (see `CardPaging`). A person's Tags tab and a site's People tab draw different cards, so one
	 * name for every tab would hand a tag card's size to a wall of faces. Derived, because the same
	 * wall is handed a new `showing` when the tab strip changes, and a new kind is a new wall: its
	 * offset starts at the top and its size is that kind's own.
	 */
	const paging = $derived(new CardPaging(RELATED_PER_PAGE, `related.${showing}`));

	/*
	 * What one row of this wall is called, which is not what the tab is called.
	 *
	 * The tab's heading is always a plural, and on a person's own page the phrase "Seen with", so
	 * it cannot be the noun: the selection bar is a sentence about the rows picked ("1 person
	 * selected"), so its words come off the kind being drawn (see `nounFor`). The pager's empty
	 * readout takes the noun too, so the two agree.
	 */
	const rowNoun = $derived(nounFor(showing));
	/* The pager counts the rows on screen, so while a tab's answer is coming it says them in their
	   own noun. */
	const heldNoun = $derived(nounFor(rowsOf));

	/* Which page was last asked for, and at what size, so a settled screen does not ask twice. The
	   guard the People wall carries, and it is load-bearing for the same reason: the fetch writes
	   state this effect reads. */
	let askedFor = -1;
	let askedSize = -1;
	let askedWhere = '';

	/*
	 * Which subject and tab the reset has been done for, a different question from `askedWhere`
	 * above, which is which one was fetched.
	 *
	 * Kept as two fields: with one, the reset would write the new tab where the loader compares,
	 * the loader would decide it had already fetched, no request would go out, the generation
	 * counter would not move, and the previous tab's answer would draw on the empty screen.
	 * `RelatedWall.svelte.test.ts` holds this.
	 */
	let resetFor = '';

	/*
	 * Rising counter, so a slower answer for the tab somebody has already left cannot land under the
	 * tab they are now on. The same guard the person page uses for aliases, and for the same reason:
	 * without it the previous wall's cards appear under this wall's heading.
	 */
	let generation = 0;

	/*
	 * One read, asked two ways.
	 *
	 * Loud is a tab or a page arriving: the cards on screen are kept, quieter and out of reach,
	 * until the answer lands, so the wall is never empty between two answers. QUIET is a re-read of
	 * the tab already on screen, and it changes nothing until the new rows land: nobody asked to be
	 * shown a loading state for a pin.
	 *
	 * The quiet one is why this is a function rather than only an effect. A pin is the one opinion
	 * that changes WHERE a card belongs rather than what it looks like, and this wall is ordered by
	 * it on the server, so settling the row in place, which is all every other opinion needs,
	 * would move the mark and leave the card exactly where it was until somebody navigated away and
	 * back.
	 */
	async function read(
		where: { on: EntityKind; id: string; showing: Exclude<RelatedKind, 'files'> },
		{ quiet = false } = {}
	) {
		const wanted = ++generation;
		if (!quiet) loading = true;
		failed = null;
		try {
			/* Through the paging, so the first measurement of a visit trims the rows held or asks for
			   the remainder only, instead of asking for the whole page again. See `CardPaging.fill`.
			   The question is the subject and the tab; this wall has no anchor, so every ask is an
			   offset. */
			const page = await paging.fill(
				`${where.on}/${where.id}/${where.showing}`,
				() => rows,
				async (ask) => {
					const offset = 'offset' in ask ? ask.offset : 0;
					const got = await loadRelated(where.on, where.id, where.showing, {
						limit: ask.limit,
						offset
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
			oncount?.(page.total);
		} catch {
			if (wanted !== generation) return;
			// A quiet re-read that fails leaves what is on screen alone: it is still the truth as of
			// a moment ago, and blanking a wall somebody is looking at is worse than being stale.
			// A loud one says so, and takes the last tab's cards away with it.
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

	/*
	 * A different tab, or a different subject, starts at the beginning.
	 *
	 * Its own effect and BEFORE the one that fetches, because Svelte runs effects in the order they
	 * were created: the reset lands in the same flush as the change that caused it, so the load
	 * below never asks for page four of a wall it has just arrived at. Left in the loader as a
	 * branch, the reset would be a write to state that same effect reads, which is the shape that
	 * runs itself forever.
	 *
	 * And BEFORE the markup (`$effect.pre`), so the wall is marked as waiting in the same draw
	 * that changes the heading. The rows stay: they are drawn as the kind they were read for
	 * (`rowsOf`), never as the new tab's cards, which would ask a person's card for
	 * `/api/sites/<person>/cover` with a link to a Site that is not there.
	 */
	$effect.pre(() => {
		const where = `${on}/${id}/${showing}`;
		untrack(() => {
			if (where === resetFor) return;
			resetFor = where;
			paging.offset = 0;
			loading = true;
			/* The Loops tab is the media grid, not cards, so there is nothing of it to keep on
			   either side: its rows are never drawn here. */
			if (showing === 'loops' || rowsOf === 'loops') {
				rows = [];
				total = 0;
			}
		});
	});

	$effect(() => {
		/* Named so the effect re-runs when the page moves, and the SIZE with it, because a taller
		   window or a wider tile holds a different number of whole rows and that is a reason to
		   re-ask. */
		const wanted = paging.offset;
		const size = paging.size;
		const where = { on, id, showing };
		const key = `${on}/${id}/${showing}`;
		untrack(() => {
			if (askedWhere === key && askedFor === wanted && askedSize === size) return;
			/* Only the SIZE moved: the first measurement of a visit, or a resize. Read quietly: a
			   loud read empties the wall first, and `fill` can only trim or extend rows it still
			   holds, so emptying them would turn a trim into a second request for the page. */
			const resized = askedWhere === key && askedFor === wanted;
			askedWhere = key;
			askedFor = wanted;
			askedSize = size;
			void read(where, { quiet: resized });
		});
	});

	/* And again, quietly, when the library moves: a username given to somebody, a share taken back.
	   Every other wall follows the library; a tab showing the same cards must too. */
	reloadOnLibraryChange(() => void read({ on, id, showing }, { quiet: true }));

	/* The page's own cards go after the last of this wall's, so they are drawn once, at the end. */
	const onLastPage = $derived(
		!(loading && rows.length === 0) && settled && rowsAt + rows.length >= total
	);
	const trailingHere = $derived(after && onLastPage ? trailing : 0);

	/*
	 * What the bar above this screen can do, while this wall is the screen.
	 *
	 * The wall honours `gridSize.step` (see `cardWidthForStep`), and the bar has to be told so, or
	 * the size slider would do nothing on any tab but Files. `AssetGrid` publishes for the Files
	 * tab, this publishes for
	 * the other five, and only one of the two is ever mounted, so there is no second publisher
	 * fighting the first.
	 *
	 * The reasons on the two dimmed controls are this wall's own, in the words the four entity walls
	 * already use for the same facts: these are named things rather than files, so the query language
	 * has nothing to filter here, and a card is not a moving picture.
	 */
	const mine = Symbol('related-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: `This wall is ${title.toLowerCase()}, not files`,
			resizable: true,
			playable: 'A card is a picture and a name, and neither plays',
			sorts: []
		});
	});

	$effect(() => () => screenBar.release(mine));

	/*
	 * Where a card leads, from the table that also decides what the card counts.
	 *
	 * The same fact is needed by the request that fills the wall: a press carrying a filter
	 * means the number on the card is the size of the filtered wall. So both readers ask
	 * `related.svelte`, where each wall's reasons are written beside its row, and a card never
	 * prints one wall's count over a press that opens another.
	 *
	 * Loops are absent because they are not cards: a wall of marks is the media grid, which owns
	 * opening a file at a moment. See below.
	 */
	function hrefFor(row: RelatedRow): string {
		return nothingHere(row)
			? relatedHref(on, rowsOf, row.id)
			: relatedHref(on, rowsOf, row.id, named);
	}

	/*
	 * A card with nothing here: its count in this context is nought, so a pick would filter the
	 * files to none.
	 *
	 * A Site's People wall counts people with a username on it as well as people with files under
	 * it, and a stash-box's answer can write somebody's username with nothing filed, so a Site can
	 * hold many people and no files. A card whose number here is nought does not pick: its picture
	 * is the link it is on every wall that does not pick, to the thing's own page without this page
	 * carried as a filter (carried, it would open filtered to the same nothing). A disabled press
	 * would make the biggest target on the card dead.
	 *
	 * Read off the number the card already prints, the count of exactly what a pick would filter to
	 * (`SPECS.carries`), so the card's words and what its picture does cannot disagree. A row with
	 * no count at all is not nought and keeps the pick.
	 */
	function nothingHere(row: RelatedRow): boolean {
		return (row.item_count ?? row.asset_count) === 0;
	}

	/** The line under the name. Whatever this kind of thing is counted in. */
	function detailFor(row: RelatedRow): string {
		const count = row.item_count ?? row.asset_count ?? 0;
		// Through the one sentence every wall draws, so the figure is grouped the way it is on the
		// username line under this card, rather than "3007 files from this Site" directly above
		// "posted 3,003 files": two ways of writing one kind of number on one card.
		// A Photo Set holds pictures; a collection holds files, and its card on its own wall says
		// so, so it says so here too rather than calling the same files two things.
		const counted = rowsOf === 'photo_sets' ? picturesSaid(count) : filesSaid(count);
		// A Site's People tab: this person's files FROM THIS SITE, however they arrived; the username
		// lines under the card count the files posted under one username.
		const said = on === 'site' && rowsOf === 'people' ? `${counted} from this Site` : counted;
		// And how big those same files are, off the same row as the count.
		return withSize(said, count, sizeOf(row));
	}

	/*
	 * What to say when there is nothing, in words that name what is missing.
	 *
	 * A sentence that names what was looked for and where. An empty wall is the screen a person
	 * reads most carefully, because they are trying
	 * to work out whether it is broken.
	 */
	const emptyWords = $derived(
		showing === 'people'
			? 'Nobody else turns up on these files.'
			: showing === 'sites_within'
				? // The two walls here that are not about this thing's FILES, so the sentence cannot be
					// about them either: nothing is filed under this, no site says it is part of it.
					'No other Site is part of this one.'
				: showing === 'tags_within'
					? 'No tag is filed under this one yet.'
					: `Nothing here carries ${title.toLowerCase()} yet.`
	);

	/*
	 * Picking rows here, and the verbs that act on what is picked.
	 *
	 * Cards drawn with no menu, no selection and nothing to press would make the same tag a thing
	 * you can act on on the Tags wall and a thing you can only look at on a person's Tags tab.
	 *
	 * The gesture and the selection are the SHARED ones, so a person who has learned long-press and
	 * ctrl-click on a wall of files has learned them here. Nothing about them is re-implemented.
	 *
	 * ## Why the menu here is shorter than the wall's, and why that is right rather than a stub
	 *
	 * `entityVerbs` offers a verb only where it is handed a handler, which is the mechanism the four
	 * walls already use to differ from each other: a tag cannot carry a tag, a collection has no
	 * heart. This wall passes the verbs that need nothing but the row: a pin is an address and a
	 * boolean. Rename, Share, Merge and Delete each open a flow that lives on the thing's own wall,
	 * and half-building one here would be a second copy of it. They arrive by being handed in.
	 */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () => rows.map((row) => row.id));
	const pinnable = $derived(pinnableOf(showing));

	/** The selection when this row is part of one, this row alone otherwise: the grid's own rule. */
	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(rows.map((row) => row.id)) : [id];
	}

	/*
	 * Every verb the thing on this card has, from the one registry that answers for all five kinds.
	 *
	 * The flows (Rename, Share, Merge, Delete) belong to the kind, not to any one wall, so they
	 * live in `wall-verbs.svelte.ts` and `EntityWallFlows` draws the sheets; a person's card offers
	 * the same verbs on a tag's People tab as on the People wall.
	 *
	 * The pin and Hidden are the walls' own too: a pin moves the row, so the tab is read again.
	 */
	const wallKind = $derived(kindOf(showing));
	const verbs = new WallVerbs({
		kind: () => wallKind ?? 'person',
		/* NO COUNT, deliberately, and it is the one thing this wall must not hand over: the number
		   under a card here is the count IN THIS CONTEXT: a person on a tag's People tab reads the
		   files of hers that carry that tag, while deleting her takes her off every file she is
		   on. Passed along, the confirmation would understate the act by whatever the filtering
		   was, and both numbers look perfectly reasonable on screen. See `WallRow.count`. */
		rows: () =>
			rows.map((row) => ({
				id: row.id,
				name: row.name,
				favorite: row.favorite ?? false,
				hidden: hiddenOf(row),
				rating: row.rating ?? null,
				pinned: row.pinned ?? false,
				/* AND THE PICTURE, for the merge sheet, which reads a card by its face before its
				   name. Only for the two kinds a merge exists for, and by the same two builders
				   every other picker uses: a third spelling of "which picture does a person
				   have" is a third answer to it. A row here already carries the cover columns:
				   `EntityCard` on this very wall is drawn from them. */
				picture: pictureOf(row)
			})),
		/* Quietly: the rows on screen stay until the new ones land. A verb that wrote something is
		   not a reason to blank a wall somebody is looking at. */
		changed: () => void read({ on, id, showing }, { quiet: true }),
		clear: () => selection.clear(),
		get pins() {
			return pinnable !== null && wallKind !== null;
		}
	});

	/* One spelling for the two the kinds use on the wire. See `WallRow.hidden`. */
	function hiddenOf(row: RelatedRow): boolean {
		return row.vault ?? row.hidden ?? false;
	}

	/* What a row is drawn by, in the two words the pickers already use.
	 *
	 * Nothing for the kinds that cannot be merged: the sheet is never opened for them, and a
	 * picture built for a tag or a collection would be a picture nothing reads. */
	function pictureOf(row: RelatedRow) {
		if (wallKind === 'person') return personRow(row).picture;
		if (wallKind === 'site') return siteRow(row).picture;
		return undefined;
	}

	/* WHETHER THE ENRICH ROWS ARE DRAWN REFUSED on this tab's rows.
	 *
	 * Read off the same holder the handlers refuse from, so the row somebody presses and the row
	 * that greys out cannot disagree. Nothing at all on a tab whose rows no stash-box is ever told
	 * about: a wall of collections has no `enrichAs` and therefore no verb to grey. */
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

	/*
	 * Picking a card to filter this page's files by.
	 *
	 * A press on a card's picture picks it, and the pick is the filter from that moment: the card
	 * takes the light accent wash and a filled filter glyph, the address gains the filter parameter
	 * (`people=`, `tags=` ...), and the Files tab is filtered to files carrying every pick when it
	 * is opened, with no second press. A second press on the picture takes the pick off; the
	 * control beside the tab words clears them all. A press on the name opens the card's own page,
	 * still carrying this page as a filter (see `hrefFor`); the keyboard reaches the picture as a
	 * toggle (Space or Enter) and the name as a link.
	 *
	 * The picture because it is the big target and picking is what this wall is for; the name was
	 * already a link. The verb selection still wins while running: long press or ctrl-click starts
	 * it, and while anything is verb-selected a plain click adds to that (the gesture's capture
	 * handler stops the press before the picture sees it), as on every wall of tiles.
	 *
	 * The picks are the address (see `picks.ts`), written with `replaceState`: a pick is not a
	 * place to go Back to one click at a time, but the tab they were gathered on is, and moving to
	 * the Files tab is a real navigation, so Back from it returns here with every pick.
	 */
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

	/* The very same filtering the entity walls ask with, as the media grid's query. Built by the
	   one function rather than assembled here, so a tag's Loops tab and a tag's Tags tab cannot
	   come to disagree about what "on this tag" means. */
	const narrowing = $derived(Object.fromEntries(narrowingFor(on, id, showing)));

	/*
	 * The same page in the words the FILTER BAR reads, for the Loops tab's panel.
	 *
	 * The route is asked by id, which is right for it (a name is ambiguous and the page holds the
	 * id) and which the query language does not read. The bar counts its columns and keeps its
	 * filters in that language, so it is told the page by NAME, the way a card on this wall carries
	 * the page away as a filter (`relatedHref`). Without it the panel over a person's loops would
	 * count the marks of the whole library.
	 *
	 * The one thing it cannot say is a TAG reaching a mark directly: the language describes files,
	 * so a tag's Loops tab counts the files carrying the tag and not the videos reached only through
	 * a tagged mark. The wall itself still shows those; only the numbers in the columns leave them
	 * out.
	 */
	const filesQuery = $derived(named ? { [fieldOf(on)]: named } : undefined);
</script>

<!--
	A wall of MARKS is drawn by the media grid, not by this one, and that is parity rather than a
	special case.

	Every other kind here is an ENTITY: a person, a tag, a shoot: a named thing with a count, which
	is exactly what an entity card draws. A loop is not one. It is a piece of a video, and on its own
	screen it is drawn by the same grid Browse is: a bare tile, a preview under the cursor, the
	shared verbs, the shared zoom. Drawn as an entity card instead it would come out as "Untitled
	loop / 2s" with none of that: a second, worse way of showing media, on one tab out of six.

	The filtering is the same parameter the endpoint already takes, so nothing about this is a
	related-list special case either. See `LOOP_SOURCE`.
-->
{#if showing === 'loops'}
	<AssetGrid
		source={LOOP_SOURCE}
		query={narrowing}
		{filesQuery}
		{title}
		{icon}
		{beside}
		{titleHidden}
		{above}
		{crumbs}
		titleLevel={2}
		{oncount}
		empty={emptyWords}
	/>
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
		{#snippet pager()}
			<!--
				In the frame's footer, where every other wall's is. What it pages is what the
				heading counts, so the two numbers agree: `total` is the scoped total the same
				request answered with.
			-->
			<!-- While a tab's answer is coming it reads the cards still on screen, in their own
			     noun, and its presses wait: they would page the new tab by the old one's total. -->
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
				<!-- Declared for every card, and it renders nothing where the page handed nothing or
				     has nothing for this row: a snippet cannot be bound to one row and passed on, so
				     the row is closed over here. -->
				{#snippet beneath()}
					{@render under?.(row, rowsOf)}
				{/snippet}
				<!-- A Music tab's card says who the song credits under its name, as on the Music
				     wall. Every card on the tab is a song, so every one draws the line. -->
				{#snippet byline()}
					{#if rowsOf === 'songs'}
						<SongArtists artists={row.artists ?? []} />
					{/if}
				{/snippet}
				{#snippet menu()}
					<!-- The same declared verbs the bar below draws, as menu rows. Not written out
					     again, which is what stops the two coming apart. -->
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

<!-- What is picked, and what can be done with it. Only where there is a verb to offer: a tab whose
     rows carry none would draw a bar with a count and nothing to press. -->
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
