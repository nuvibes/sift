<script lang="ts" module>
	import type { Answer } from '$lib/components/organize/Answers.svelte';
	import type { Shoot } from '$lib/entity/shoots.svelte';

	/** What each of a shoot's answers does where it is asked: the card here, or the shoot's page. */
	export interface ShootPresses {
		make: () => void;
		makeNamed: () => void;
		nameRest: () => void;
		discard: () => void;
	}

	/**
	 * A shoot's answers, in the one order and the one wording both places ask them in: this wall's
	 * card and the shoot's own page (`ShootDetail`). Create is the answer the question expects; the
	 * others sit behind its chevron, Name the rest only where there is a rest.
	 */
	export function shootAnswers(one: Shoot, press: ShootPresses): { yes: Answer; rest: Answer[] } {
		return {
			yes: { label: 'Create Photo Set', icon: 'add', run: press.make },
			rest: [
				{ label: 'Create with a name\u2026', icon: 'edit', run: press.makeNamed },
				...(one.unnamed > 0
					? [{ label: 'Name the rest', icon: 'person_add' as const, run: press.nameRest }]
					: []),
				{ label: 'Discard', icon: 'remove', run: press.discard }
			]
		};
	}
</script>

<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Runs of one creator's loose pictures that look like one sitting.
	 *
	 * A wall of cards rather than a list of rows: the question is "are these the same shoot", and
	 * only looking at the pictures answers it.
	 *
	 * The two answers sit apart on purpose. Create Photo Set is the safe affirmative; Not a set is
	 * permanent (remembered against the pictures so no rearrangement brings it back), so it asks
	 * first, here, where somebody is looking carefully. The board's own card offers only the
	 * affirmative for that reason.
	 *
	 * Create Photo Set is a split button, the shape every Organize card wears: the lead half makes
	 * the Photo Set under the proposal's name (the creator's), and the chevron offers "Create with
	 * a name...". One act with one adjustable detail, and the ordinary press stays one press.
	 *
	 * The card asks the board's question in the board's words ("Do these 13 pictures of X belong
	 * together?" over "13 pictures of X that are in no Photo Set"). Both come from the server
	 * (`ShootView.question` / `.detail`, built by the one function the board's card uses), so the
	 * two cards cannot disagree.
	 *
	 * The person's name in the question is a way to that person: the server sends who the sentences
	 * name (`ShootView.links`), and `Said` finds and links the name with History's own
	 * `sentenceParts` and `hrefOf`. Only the title line links; the line under it says the same name
	 * and is plain, here and on the board's card, so a card carries one way to the person rather
	 * than two.
	 *
	 * The count sits at the top, where Identified People says its own, the first thing read on
	 * arrival. There is no "Look again" press: the pass is asked for by every scan as it settles
	 * (`settles_into` in the composition root), by the board's card while nothing has been found,
	 * and by the Activity screen's run-now.
	 *
	 * It pages the way every other Organize wall pages: `CardPaging` sizes a page to whole rows of
	 * the screen, the pager goes to the frame's foot through `onpaging`, and the proposal the page
	 * starts at goes in the address as `from`, so Back from a picture or a person lands on the page
	 * it left.
	 *
	 * Name the rest is a third, separate press that appears only where there is a rest: a shoot can
	 * pull in pictures of the same sitting that carry nobody, and putting the creator on those is a
	 * different judgement from agreeing the pictures belong together. Each has its own receipt, so
	 * either can be taken back alone.
	 */
	import { goto } from '$app/navigation';
	import { thumbUrl } from '$lib/entity/art';
	import { openAsset } from '$lib/player/asset-view';
	import { ApiError } from '$lib/api/client';
	import {
		Button,
		ConfirmDialog,
		Empty,
		Field,
		Pressable,
		Problem,
		Skeleton,
		TextInput
	} from '$lib/components/common';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import Said from '$lib/components/organize/Said.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { onDestroy, untrack } from 'svelte';
	import { page as address } from '$app/state';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import {
		SHOOTS_PER_PAGE,
		makeTheSet,
		nameTheRest,
		notASet,
		shoots
	} from '$lib/entity/shoots.svelte';

	let items = $state<Shoot[]>([]);
	let total = $state(0);
	let automatic = $state(false);
	let loading = $state(true);
	let failed = $state(false);
	let busy = $state<string | null>(null);
	let refusing = $state<Shoot | null>(null);
	let refuseOpen = $state(false);
	/* "Create with a name...": the shoot it is about, whether the box is open, and what is typed.
	   One box on the screen for whichever card opened it, the same arrangement as the refusal. */
	let naming = $state<Shoot | null>(null);
	let nameOpen = $state(false);
	let typed = $state('');
	/* The pictures whose still could not be fetched, by id. Drawn as the empty-frame glyph a tile
	   wears for the same thing (`Tile`'s `hide_image`), rather than the browser's broken-image box,
	   which reads as the screen being broken rather than one picture having no still yet. */
	let unseen = $state<Set<string>>(new Set());

	/* A page of whole rows of cards, the same paging every Organize wall has. See `CardPaging`. */
	const paging = new CardPaging(SHOOTS_PER_PAGE, 'organize.shoots');

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();
	$effect(() => {
		onpaging?.(paging.asPager(items.length, total, 'shoots'));
	});
	onDestroy(() => onpaging?.(null));

	/* Where this wall was left, carried in the address. See `$lib/grid/anchor`. `path` is caught
	   once so a background re-read cannot rewrite the address after somebody has gone elsewhere,
	   and `arriving` is true exactly once: after the first page the anchor there is one WE wrote. */
	const path = address.url.pathname;
	let arriving = true;

	async function load() {
		failed = false;
		try {
			// The rows go in as a function: `fill` reads them untracked, so the effect that runs
			// this load cannot come to depend on its own answer.
			const page = await paging.fill(
				'',
				() => items,
				(query) => {
					// The skeleton only when a request goes out: a landing or a trim asks nothing.
					loading = true;
					return shoots(query);
				},
				(answer) => ({ rows: answer.shoots, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			if (page.answer) automatic = page.answer.auto_file;
			/* A page emptied by answering its last card comes back as the new last page: one rule
			   for every wall, in `CardPaging.fill`. */
			items = page.rows;
			total = page.total;
			// Through `land`, in the same step as the rows: see `CardPaging.land`.
			paging.land(page.offset);
			rememberAnchor(address.url, path, items[0]?.id, page.offset);
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	/* A page turned, or a window that now holds more rows or fewer. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			// UNTRACKED: this effect's own answer writes the address. See `IdentifiedPanel`.
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		// UNTRACKED too: its dependencies are the two named above. See `FolderSuggestions`.
		untrack(() => void load());
	});

	/* And again whenever the library's shape changes underneath: a picture filed into a set while
	   this screen is open is a picture that is no longer part of any proposal here. */
	reloadOnLibraryChange(() => void load());

	/* A 409 is a card whose pictures went into a Photo Set while it stood: the server refuses the
	   press and answers the card by that set, so the page is re-read and the card goes. Its
	   sentence goes to a toast; nothing about the queue is broken. */
	async function answer(one: Shoot, act: () => Promise<unknown>) {
		busy = one.id;
		try {
			await act();
			await load();
		} catch (failure) {
			if (failure instanceof ApiError && failure.status === 409 && failure.detail) {
				toasts.show(failure.detail);
				await load();
			} else {
				failed = true;
			}
		} finally {
			busy = null;
		}
	}

	/* The refusal, once the dialog has been answered. Held as state rather than passed through the
	   dialog, because there is one dialog on the screen and it is about whichever card opened it. */
	function doRefuse() {
		const one = refusing;
		refusing = null;
		if (one) void answer(one, () => notASet(one.id));
	}

	function openNaming(one: Shoot): void {
		naming = one;
		typed = one.name;
		nameOpen = true;
	}

	/* The Photo Set, made under the name typed into the box.

	   A refusal is the SERVER'S sentence, shown as written: "a Photo Set's name cannot contain a
	   double quote" is what somebody needs in order to type a different one. And it goes to a
	   toast rather than to the whole-screen problem `answer` raises: one name was refused, nothing
	   about the queue is broken. */
	async function createNamed(): Promise<void> {
		const one = naming;
		naming = null;
		if (!one) return;
		busy = one.id;
		try {
			await makeTheSet(one.id, typed);
			await load();
		} catch (failure) {
			toasts.show(
				failure instanceof ApiError && failure.detail
					? failure.detail
					: "That Photo Set couldn't be created",
				{ tone: 'error' }
			);
		} finally {
			busy = null;
		}
	}

	function missing(id: string): void {
		unseen = new Set([...unseen, id]);
	}

	/**
	 * How many of a shoot's pictures a card shows before the rest go behind a press.
	 *
	 * A proposal holds from the floor to sixty pictures, so cards would differ wildly in height.
	 * Twelve is two rows of the strip at any card width, because the strip always fits at least six
	 * to a row (see `.strip`), which is enough of a sitting to judge; every picture is on the
	 * shoot's own page, one press away, and the question is unchanged.
	 */
	const SHEET = 12;

	function shown(one: Shoot): Shoot['items'] {
		return one.items.slice(0, SHEET);
	}

	/* Every card opens its shoot on a page of its own (`ShootDetail`), the way a Photo Set opens:
	   every picture of it, with this card's question and answers. The address is the shoot's. */
	function openShoot(id: string): void {
		void goto(`/organize/shoots/${encodeURIComponent(id)}`);
	}

	/*
	 * A picture pressed opens the viewer over this queue, on that picture.
	 *
	 * `openAsset` is the door every Organize card uses (Copies, Duplicates, the faces piles): the
	 * viewer opens over the page as a shallow history entry, so closing it lands back here with the
	 * queue as it was. The list handed in is the whole shoot, not the dozen the card draws, so Next
	 * and Previous walk the sitting being judged. A GIF runs and a still does not; the server says
	 * which (`ShootPictureView.media_type`).
	 */
	function look(one: Shoot, pictureId: string): void {
		openAsset(
			pictureId,
			one.items.map((item) => ({ id: item.id, runs: item.media_type !== 'image' }))
		);
	}
</script>

<!--
	ONE OF THE CARD'S SENTENCES, with each name in it drawn as a way to the thing it names. Written
	on one line on purpose: the runs sit inside the sentence, and a line break between them would be
	a space in the words.
-->

<section>
	{#if loading && items.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem message="Shoots couldn't be loaded. Refresh the page to try again." />
	{:else if items.length === 0}
		<Empty scope="page" icon="photo_library" title="No shoots waiting">
			{#if automatic}
				Sift creates these Photo Sets without asking. Each one is listed in History, under
				Decisions, where you can undo it.
			{:else}
				Photos by one creator that look like one shoot, and that are in no Photo Set, appear here.
				Sift looks for them after every scan.
			{/if}
		</Empty>
	{:else}
		<!-- How many are waiting, over the wall rather than under it: this page has no tab to carry
		     the count, as the faces tabs do. The server's total, not the cards drawn, so it is the
		     size of the queue. -->
		<p class="count">{total === 1 ? '1 shoot waiting' : `${counted(total)} shoots waiting`}</p>
		<ul class="wall" {@attach paging.cards}>
			{#each items as one (one.id)}
				<li>
					<DecisionCard>
						<!-- The question and the line under it at the card's foot, the answers after
						     them: the one place every question on Organize is asked
						     (`DecisionCard`). Only the question links the person. -->
						{#snippet question()}<Said what={one.question} links={one.links} />{/snippet}
						{#snippet detail()}{one.detail}{/snippet}
						<ul class="strip">
							{#each shown(one) as picture, at (picture.id)}
								<li class:unnamed={!picture.named}>
									<Pressable
										class="look"
										feedback="none"
										radius="sm"
										aria-label="Open photo {at + 1} of {one.items.length}"
										onclick={() => look(one, picture.id)}
									>
										{#if unseen.has(picture.id)}
											<span class="none">
												<Icon name="hide_image" size={16} label="No preview" />
											</span>
										{:else}
											<img
												src={thumbUrl({ id: picture.id, art: picture.art })}
												alt=""
												loading="lazy"
												decoding="async"
												onerror={() => missing(picture.id)}
											/>
										{/if}
									</Pressable>
								</li>
							{/each}
						</ul>
						<!-- Never sixty thumbnails on a card. The DECISION is one decision whatever the
						     number is, and every picture is on the shoot's own page, a press away. -->
						<div class="rest">
							<Button
								tone="link"
								size="small"
								aria-label="Open all {counted(one.items.length)} photos of this shoot"
								onclick={() => openShoot(one.id)}
							>
								{one.items.length > SHEET
									? `Open all ${counted(one.items.length)} photos`
									: 'Open this shoot'}
							</Button>
						</div>
						<!-- Create is the answer the question expects; the others sit behind its
						     chevron. Discard asks first, because it lasts. -->
						{#snippet answers()}
							{@const given = shootAnswers(one, {
								make: () => void answer(one, () => makeTheSet(one.id)),
								makeNamed: () => openNaming(one),
								nameRest: () => void answer(one, () => nameTheRest(one.id)),
								discard: () => {
									refusing = one;
									refuseOpen = true;
								}
							})}
							<Answers
								yes={given.yes}
								rest={given.rest}
								about="this shoot"
								disabled={busy === one.id}
							/>
						{/snippet}
					</DecisionCard>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<ConfirmDialog
	bind:open={refuseOpen}
	title="Discard this shoot?"
	consequence="Sift won't suggest these photos as a shoot again, in any grouping. Nothing is
	deleted or moved, and you can still add them to a Photo Set yourself."
	confirmLabel="Discard"
	destructive={false}
	onconfirm={doRefuse}
/>

<!-- The rename box every named thing in Sift is renamed through (`EntityWallFlows`): a confirmation
     with one field in it. It opens holding the proposal's own name, so the ordinary edit is a
     change to it rather than a retype. -->
<ConfirmDialog
	bind:open={nameOpen}
	title="Create a Photo Set with a name"
	consequence="The photos on this card become one Photo Set with this name. You can rename it later on its page."
	confirmLabel="Create"
	destructive={false}
	confirmDisabled={typed.trim().length === 0}
	onconfirm={() => void createNamed()}
>
	{#snippet extra()}
		<Field label="Name">
			{#snippet control({ id, describedBy })}
				<TextInput {id} bind:value={typed} autocomplete="off" {describedBy} />
			{/snippet}
		</Field>
	{/snippet}
</ConfirmDialog>

<style>
	/* The queue's size over its wall, drawn as Identified People draws its own (`.count` there). */
	.count {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	.wall {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(22rem, 100%), 1fr));
		gap: var(--space-4);
	}

	/*
	 * Each card at its own height, with no floor, as every Organize card is: a floor would leave the
	 * short ones a band of empty ground. The grid gives the cards of one row one height.
	 */
	.wall > li {
		display: grid;
	}

	/* On the card's button side (`DecisionCard`), where an action goes on a card. */
	.rest {
		display: flex;
		justify-content: var(--card-actions-justify, flex-end);
	}

	/* The pictures of the shoot, wrapping rather than scrolling: the question is about all of them
	   at once, and a strip that hides half of it behind a scroll asks it of half.

	   AT LEAST SIX TO A ROW, so the twelve a card shows (`SHEET`) are never more than two rows and
	   the cards of one wall stay close in height. A cell is 4rem
	   where six of those fit, and a sixth of the row where they do not: a narrower picture rather
	   than a third row. Wider cards still pack more than six. */
	.strip {
		list-style: none;
		margin: var(--space-3) 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(
			auto-fill,
			minmax(min(4rem, calc((100% - 5 * var(--space-1)) / 6)), 1fr)
		);
		gap: var(--space-1);
	}

	/* No ground under the picture: it covers its own box, and a colour behind it would be a box
	   drawn by hand where the card already provides one. */
	/* The press is the whole picture, and takes no room of its own. `:global` because the class is
	   handed to `Pressable` and lands on its element. */
	.strip :global(.look) {
		display: block;
		inline-size: 100%;
		padding: 0;
	}

	/* A picture with no still: the same square, holding the glyph a tile wears for it, and no
	   ground of its own, for the reason the picture has none (the card is the box). */
	.strip .none {
		display: grid;
		place-items: center;
		aspect-ratio: 1;
		color: var(--sift-ink-3);
	}

	.strip img {
		display: block;
		inline-size: 100%;
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: var(--radius-sm);
	}

	/* A picture carrying nobody is marked, because it is what "Name the rest" is about and a button
	   naming files somebody cannot pick out is a button they have to trust rather than read. */
	.strip li.unnamed img {
		outline: 2px dashed var(--sift-line);
		outline-offset: -2px;
	}
</style>
