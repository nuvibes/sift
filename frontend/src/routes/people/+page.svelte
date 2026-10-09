<script lang="ts">
	import { untrack } from 'svelte';
	import { refusedMark } from '$lib/swap/refused';
	import { page as address } from '$app/state';
	import { WallWords, wordsIn } from '$lib/components/shell/wall-words';

	import { anchorIn, forgetAnchor, rememberAnchor } from '$lib/grid/anchor';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { goto } from '$app/navigation';
	/* The People screen: who is in this library, as a wall of faces. */
	import { Button, Chip, Selection, TileGesture, VerbMenuItems } from '$lib/components/common';
	import { dropTarget } from '$lib/components/common/drag-assign.svelte';
	import EntityCard from '$lib/components/entity/EntityCard.svelte';
	import EntityWallFlows from '$lib/components/entity/EntityWallFlows.svelte';
	import { WallVerbs } from '$lib/components/entity/wall-verbs.svelte';
	import { menuVerbs } from '$lib/components/common/verbs';
	import { entityVerbs } from '$lib/components/entity/verbs';
	import { enrichBoxes, loadEnrichBoxes } from '$lib/entity/enrichment.svelte';
	import { fetchOnto } from '$lib/library/aimed-drop.svelte';
	import { personRow } from '$lib/people/person-row';
	import { nearNames } from './near-names';
	import EntityGrid from '$lib/components/entity/EntityGrid.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { filesSaid, filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { cardCells } from '$lib/components/entity/entity-counts';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { facetParams } from '$lib/components/shell/facet-labels';
	import { ENTITY_OPINION_SORTS, UNIVERSAL_SORTS } from '$lib/grid/sort-state.svelte';
	import {
		people,
		PEOPLE_PER_PAGE,
		PeopleSearch,
		type EntitySort,
		type Person
	} from '$lib/people/people.svelte';
	import Pager from '$lib/components/common/Pager.svelte';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { announceSkipped } from '$lib/library/bulk';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing } from '$lib/components/common/toast-pieces';
	import { ApiError } from '$lib/api/client';

	/* Picking several together, exactly as the wall of files does: the same Selection, the same
	   gesture object, the same long press. */
	const selection = new Selection();
	const gesture = new TileGesture(selection, () =>
		// A locked tile is a row whose name cannot be seen: never part of a select-all or a range.
		shown.filter((person) => !person.locked).map((person) => person.id)
	);

	/* Escape lets go, from wherever the pointer is. */
	function letGo(event: KeyboardEvent) {
		// Ctrl+Z takes back the last thing PICKED, Ctrl+Shift+Z picks it again.
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key === 'Escape' && gesture.escaped(event)) event.stopPropagation();
	}

	/* Anything that leaves the wall must leave the selection with it, or the bar goes on counting
	   rows that are not there and the next action runs over ids the server has forgotten. */
	$effect(() => {
		const here = new Set(shown.map((person) => person.id));
		untrack(() => selection.retain([...here]));
	});

	let term = $state('');
	/* The typed search, and its answer. Held in `PeopleSearch` so that re-reading the wall
	   re-reads it too. */
	const typedSearch = new PeopleSearch((wanted) => people.matchingAnywhere(wanted, narrowedBy));
	const searched = $derived(typedSearch.searched);
	const matches = $derived(typedSearch.matches);
	let aliasTarget = $state<Person | null>(null);

	async function narrow(typed: string) {
		try {
			// Which answer is current (an older one landing late must not overwrite a newer) is the
			// search's to decide; this only says what happened.
			if (await typedSearch.run(typed)) aliasTarget = null;
		} catch {
			toasts.show("That search couldn't be run", { tone: 'error' });
		}
	}

	/* The words the wall is searched by live in its address, so the chip on the bar, Back and a
	   link all say the same thing as the box. */
	const addressWords = $derived(wordsIn(address.url));
	const words = new WallWords();

	/* The search follows the address. */
	$effect(() => {
		const arrived = addressWords;
		untrack(() => {
			if (!words.echoed(arrived)) term = arrived;
			void narrow(arrived);
		});
	});

	/* Which page of the wall is shown; not while a search is, whose answer is shown whole. */
	const paging = new CardPaging(PEOPLE_PER_PAGE, 'wall.people');

	/* Load the page being asked for, and ONLY when the page being asked for changes. */
	/** What was last asked (the place, the size and the filtering), so a settled screen does not
	 *  ask again when only the store's own `loaded` moved. See `CardPaging.fill` for the rest. */
	let askedKey = '';
	/** ...and under which filter, so a facet going on or off goes back to the first page. */
	let askedNarrowing = '';

	/* WHAT THE BAR HAS FILTERED THIS WALL TO, out of the address. */
	const narrowedBy = $derived(facetParams('person', address.url.searchParams));
	const narrowedKey = $derived(JSON.stringify(narrowedBy));

	/* A typed name answers within the chips, so a chip going on or off asks it again. */
	$effect(() => {
		void narrowedKey;
		untrack(() => void typedSearch.again());
	});

	/* The route this wall belongs to, captured once so a navigation away can be told from a reload. */
	const path = address.url.pathname;

	/* True exactly once: the first settle, which is somebody arriving at a link. */
	let arriving = true;

	$effect(() => {
		// Named so the effect re-runs when the page moves, which is the whole point of it.
		const wanted = paging.offset;
		const size = paging.size;
		const narrowing = narrowedKey;
		/* And when the cache is DROPPED, which is what `forget()` does as the vault opens or
		   shuts: what is concealed never arrives, so a list held from before is wrong the moment
		   the vault moves. */
		const stale = !people.loaded;
		untrack(() => {
			if (arriving) {
				arriving = false;
				// UNTRACKED, and that is load-bearing. Reading the address inside an effect makes
				// the effect depend on it, and this effect's own answer WRITES the address.
				paging.arrive(anchorIn(address.url));
			}
			const narrowed = askedNarrowing !== narrowing;
			/* A different filter is a different list, so the page it was on means nothing in it:
			   the same call the search above makes. */
			if (narrowed && askedNarrowing !== '') {
				paging.forget();
				if (paging.offset !== 0) {
					// The move re-runs this effect, which asks from there.
					askedNarrowing = narrowing;
					paging.offset = 0;
					return;
				}
			}
			const key = `${paging.offset}|${size}|${narrowing}|${paging.anchor}`;
			if (!stale && key === askedKey) return;
			askedKey = key;
			askedNarrowing = narrowing;
			void wanted;
			/* Through the paging, so a first visit trims or asks for the remainder instead of
			   asking twice, and an anchored arrival lands its offset without asking again. */
			void people
				.fill(paging, people.sort, narrowedBy)
				.then(() => rememberAnchor(address.url, path, people.items[0]?.id, paging.offset));
		});
	});

	/* A search filters what is shown, so the page it was on means nothing any more. */
	$effect(() => {
		void matches;
		untrack(() => {
			paging.offset = 0;
			/* ...and the stale anchor comes OUT of the address, or a link copied from the bar
			   would carry a position belonging to a search nobody is running any more. */
			forgetAnchor(address.url, path);
		});
	});

	/* What the wall shows: the search's answer when one has been run, everybody otherwise. */
	const shown = $derived(matches ?? people.items);

	/* What this wall offers the bar above it. */
	const mine = Symbol('people-wall');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: true,
			subject: 'person',
			count: people.loaded ? people.total : undefined,
			resizable: true,
			playable: "A card is a face, and a face doesn't play",
			/* The orders, for everybody. */
			sorts: [...UNIVERSAL_SORTS, ...ENTITY_OPINION_SORTS],
			sort: people.sort,
			onSort: (next) =>
				// No prefix, and back to the first page. This wall filters through the server's
				// resolver, which knows about aliases and handles, rather than through the name
				// prefix.
				turnTo(next)
		});
	});

	$effect(() => () => screenBar.release(mine));

	/* A new order, from the first page. */
	function turnTo(next: EntitySort) {
		people.sort = next;
		paging.forget();
		if (paging.offset !== 0) paging.offset = 0;
		else void people.fill(paging, next);
	}

	/* Who to offer as "maybe this is another name for them": only names close to what was typed. */
	const candidates = $derived(term.trim() ? nearNames(people.items, term) : []);

	/* Enter runs the search that has already run, never a different one. */
	async function search(event: SubmitEvent) {
		event.preventDefault();
		// Written, the address runs the search (above); already there, it is run again here.
		if (!words.write(address.url, term)) await narrow(term);
	}

	async function addAsAlias(person: Person) {
		try {
			await people.addAlias(person.id, searched);
			toasts.show([`"${searched}" now finds `, thing('person', person.id, person.name)], {
				tone: 'success'
			});
			typedSearch.matches = (await people.resolve(searched)).people;
			aliasTarget = null;
		} catch (error) {
			// 409 means they already carry it, which a list showing only one spelling cannot convey.
			const message =
				error instanceof ApiError && error.status === 409
					? `${person.name} already has that name.`
					: "That name couldn't be added.";
			toasts.show(message, { tone: 'error' });
		}
	}

	async function assignDropped(assetIds: string[], personId: string) {
		const person = people.byId(personId);
		const named = person ? thing('person', personId, person.name) : 'them';
		try {
			const done = await people.assign(assetIds, [personId]);
			toasts.show(
				done.changed === 0
					? ['Already on ', named]
					: [`Attributed ${filesSaid(done.changed)} to `, named, '. Nothing moved.'],
				{ tone: 'success' }
			);
			announceSkipped(done);
		} catch {
			toasts.show("Those couldn't be attributed", { tone: 'error' });
		}
	}

	/* A failed write is put back by the store, so all this has to do is say so. */
	async function heart(person: Person, favorite: boolean) {
		try {
			await people.setFavorite(person.id, favorite);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(person: Person, rating: number | null) {
		try {
			await people.setRating(person.id, rating);
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	function detailFor(person: Person): string {
		return filesSized(person.asset_count, sizeOf(person));
	}

	/* Every verb this wall offers, for the bar AND for each card's menu, from the one registry. */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});

	const verbs = new WallVerbs({
		kind: () => 'person',
		/* The whole count, because this wall counts in the library: the number a delete says. */
		rows: () =>
			shown.map((person) => ({
				id: person.id,
				name: person.name,
				count: person.asset_count,
				favorite: person.favorite,
				hidden: person.vault,
				rating: person.rating,
				pinned: person.pinned,
				picture: personRow(person).picture
			})),
		changed: () => reread(),
		clear: () => selection.clear(),
		/* A pin moves the row to the top of this very list, so this wall offers it. */
		pins: true
	});

	/* What a right-click acts on: the selection when this row is part of one, this row alone
	   otherwise. */
	function targetIds(id: string): string[] {
		return selection.has(id) ? selection.ordered(shown.map((person) => person.id)) : [id];
	}

	const pickedIds = $derived(selection.ordered(shown.map((person) => person.id)));

	/* Where the Enrich rows stand for these, read off the holder the registry's handlers refuse
	   from, so the row somebody presses and the row that greys out cannot disagree. */
	const enrichment = verbs.enrichment('person');

	/* WHAT THIS WALL RE-READS: the page, and the search when one is on screen, because the wall
	   draws whichever of the two it holds (`shown`). */
	function reread() {
		void people.fill(paging);
		void typedSearch.again();
	}

	/* And again whenever a share or a restrict moves, because this list is scoped: what belongs
	   in it changes without anything being imported. */
	reloadOnLibraryChange(reread);
</script>

<svelte:head><title>People</title></svelte:head>

<EntityGrid
	icon="person"
	title="People"
	drawn={shown.length}
	total={matches !== null ? shown.length : people.total}
	loading={people.loading}
	failed={people.failed ? "The people couldn't be loaded." : null}
	empty={matches !== null
		? `No name here contains "${searched}".`
		: 'Nobody yet. Add someone, then drag clips onto their card to say who is in each clip.'}
	measure={paging.cards}
	page={paging.showing}
>
	{#snippet pager()}
		<!--
			Only while showing the whole wall. A search asks the server for everybody whose name
			contains the term and shows the answer whole.
		-->
		{#if matches === null}
			<Pager
				offset={paging.offset}
				shown={shown.length}
				total={people.total}
				noun="people"
				onfirst={() => paging.goTo(0, people.total)}
				onprevious={() => paging.step(-1, people.total)}
				onnext={() => paging.step(1, people.total)}
				onlast={() => paging.last(people.total)}
				onjump={(position) => paging.goTo(position - 1, people.total)}
			/>
		{/if}
	{/snippet}
	<!--
		The sentence sits IN the header, beside the title it describes, rather than under the wall, so
		it is the same line in the same place on a full library, an empty one and every tab, and
		pressing a tab changes what is below the header and nothing above it.
	-->

	{#snippet controls()}
		<!--
			No way through to the review queues from here: Organize has its own place on the rail, and
			a second door in this row would be one control too many beside the box and the Add.
		-->
		<WallControls
			noun="person"
			plural="people"
			bind:term
			onsettled={(typed) => words.write(address.url, typed)}
			onadd={session.isAdmin ? () => void goto('/people/new') : undefined}
		/>
		<!-- No rename form here: Rename opens the registry's box (`EntityWallFlows`), the same
		     one every wall and every tab uses, and Add opens the blank record at `/people/new`. -->
	{/snippet}

	{#each shown as person (person.id)}
		{@const target = dropTarget({
			kind: 'asset',
			targetId: person.id,
			onassign: assignDropped,
			onlink: session.isAdmin
				? (url, id) => void fetchOnto(url, 'person', id, person.name)
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
				href="/people/{person.id}"
				swapAs={{ kind: 'person', id: person.id, refused: refusedMark(person) }}
				name={person.name}
				locked={person.locked}
				counts={cardCells('person', person.id, `/people/${person.id}`, person.counts)}
				selected={selection.has(person.id)}
				id={person.id}
				onpressstart={(event) => gesture.pressStart(person.id, event)}
				onpressend={() => gesture.pressEnd()}
				onclickcapture={(event) => gesture.clicked(person.id, event)}
				coverAssetId={person.cover_asset_id}
				coverUploadId={person.cover_upload_id}
				coverAtMs={person.cover_at_ms}
				coverFrame={person.cover_frame}
				coverTrackId={person.cover_track_id}
				creatorName={person.name}
				art={person.art}
				detail={detailFor(person)}
				shared={person.shared}
				restricted={person.restricted}
				hidden={person.vault}
				onsharing={session.isAdmin ? () => verbs.askToShare([person.id]) : undefined}
				onhidden={() => verbs.askAboutHidden(person.id)}
				favorite={person.favorite}
				rating={person.rating}
				pinned={person.pinned}
				pmvCreator={person.pmv_creator}
				dropping={target.over}
				onfavorite={(next) => heart(person, next)}
				onrate={(next) => rate(person, next)}
			>
				{#snippet menu()}
					<!-- The same declared verbs the bar draws, rendered as menu rows. -->
					<VerbMenuItems
						ids={targetIds(person.id)}
						subjectId={person.id}
						verbs={menuVerbs(
							entityVerbs({
								isAdmin: session.isAdmin,
								showingHidden: verbs.allHidden(targetIds(person.id)),
								pinned: verbs.allPinned(targetIds(person.id)),
								favorite: verbs.allFavorite(targetIds(person.id)),
								rating: verbs.sharedRating(targetIds(person.id)),
								handlers: verbs.handlers,
								keptLocal: enrichment.allKeptLocal(targetIds(person.id)),
								enrichRefused: enrichment.anyRefused(targetIds(person.id)),
								...(enrichment.why(targetIds(person.id))
									? { enrichWhy: enrichment.why(targetIds(person.id)) as string }
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
			order={() => shown.map((person) => person.id)}
			noun="person"
			plural="people"
			showingHidden={verbs.allHidden(pickedIds)}
			pinned={verbs.allPinned(pickedIds)}
			favorite={verbs.allFavorite(pickedIds)}
			rating={verbs.sharedRating(pickedIds)}
			handlers={verbs.handlers}
			enrichBoxes={enrichBoxes()}
		/>
	{/snippet}
</EntityGrid>

<!-- The prompt that grows the alias list, and the reason the search box is worth having. -->
{#if matches !== null && matches.length === 0 && session.isAdmin && people.items.length > 0}
	<div class="prompt">
		{#if aliasTarget}
			<p>
				Add "{searched}" as another name for <strong>{aliasTarget.name}</strong>?
			</p>
			<div class="answer">
				<Button tone="primary" onclick={() => addAsAlias(aliasTarget!)}>Yes, add it</Button>
				<Button onclick={() => (aliasTarget = null)}>No</Button>
			</div>
		{:else if candidates.length > 0}
			<p class="hint">Is it another name for someone already here?</p>
			<ul class="candidates">
				{#each candidates as person (person.id)}
					<!--
						A person is a LABEL somebody applied, so these are pills rather than buttons.
					-->
					<li><Chip onselect={() => (aliasTarget = person)}>{person.name}</Chip></li>
				{/each}
			</ul>
		{/if}
	</div>
{/if}

<svelte:window onkeydown={letGo} />

<!-- Every sheet a verb opens (rename, share, the reach report, why it is hidden, delete, merge and
     the tag sheet), drawn once for the wall by the registry, whichever card or bar asked. -->
<EntityWallFlows {verbs} />

<style>
	.prompt {
		margin: 0 var(--space-4);
		padding: var(--space-3);
		/* The card's light, its edge under this transparent border (see `--sift-card`). */
		border: 1px solid transparent;
		border-radius: var(--radius-md);
		background: var(--sift-card);
	}

	.prompt p {
		margin: 0 0 var(--space-2);
	}

	/* The two answers sit under the question rather than inside it: inline in the paragraph they
	   would be markup a browser will not keep once the controls are real components. */
	.answer {
		display: flex;
		gap: var(--space-2);
	}

	.hint {
		color: var(--sift-ink-3);
	}

	.candidates {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}
</style>
