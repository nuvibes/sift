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
	 * A shoot's answers in one order and wording for this card and the shoot's page: Create leads.
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
	/* Runs of one creator's loose pictures that look like one sitting, as cards. Create Photo Set is a
	 * split button (the chevron names it); Not a set is permanent, so it asks first; Name the rest
	 * appears only where pictures carry nobody. The question and its links are the server's, as on
	 * the board's card; it pages as every Organize wall, the page kept in the address. */
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
	import CardWall from '$lib/components/organize/CardWall.svelte';
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
	/* "Create with a name...": one box for whichever card opened it. */
	let naming = $state<Shoot | null>(null);
	let nameOpen = $state(false);
	let typed = $state('');
	/* Pictures with no still, drawn as the tile's `hide_image` glyph. */
	let unseen = $state<Set<string>>(new Set());

	/* A page of whole rows of cards, the same paging every Organize wall has. See `CardPaging`. */
	const paging = new CardPaging(SHOOTS_PER_PAGE, 'organize.shoots');

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();
	$effect(() => {
		onpaging?.(paging.asPager(items.length, total, 'shoots'));
	});
	onDestroy(() => onpaging?.(null));

	/* Where the wall was left, in the address; `path` caught once (`$lib/grid/anchor`). */
	const path = address.url.pathname;
	let arriving = true;

	async function load() {
		failed = false;
		try {
			// The rows as a function, read untracked by `fill`.
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

	/* And when the library changes underneath. */
	reloadOnLibraryChange(() => void load());

	/* A 409: the pictures went into a Photo Set; the page is re-read and the card goes. */
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

	/* The refusal, once the one dialog is answered. */
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

	/* Created under the typed name; a refusal is the server's sentence, in a toast. */

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

	/** Pictures a card shows before the rest: twelve, two rows at any width. */
	const SHEET = 12;

	function shown(one: Shoot): Shoot['items'] {
		return one.items.slice(0, SHEET);
	}

	/* A card opens its shoot's own page. */
	function openShoot(id: string): void {
		void goto(`/organize/shoots/${encodeURIComponent(id)}`);
	}

	/* A picture opens the viewer over this queue (`openAsset`), walking the whole shoot. */
	function look(one: Shoot, pictureId: string): void {
		openAsset(
			pictureId,
			one.items.map((item) => ({ id: item.id, runs: item.media_type !== 'image' }))
		);
	}
</script>

<!-- A sentence with each name a link, on one line, as a break would be a space. -->

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
		<!-- The queue's size over the wall, the server's total. -->
		<p class="count">{total === 1 ? '1 shoot waiting' : `${counted(total)} shoots waiting`}</p>
		<CardWall cards={paging.cards}>
			{#each items as one (one.id)}
				<li>
					<DecisionCard opens={() => openShoot(one.id)}>
						<!--
						The question and the line under it, then the answers (`DecisionCard`).
						-->
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
						<!-- Never sixty thumbnails: every picture is on the shoot's page. -->
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
						<!-- Create leads; Discard asks first, because it lasts. -->
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
		</CardWall>
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

<!-- The rename box, opening on the proposal's own name. -->
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

	/* On the card's button side (`DecisionCard`), where an action goes on a card. */
	.rest {
		display: flex;
		justify-content: var(--card-actions-justify, flex-end);
	}

	/* Wrapping, never scrolling, at least six to a row, so twelve are two rows. */

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

	/* The press is the whole picture; global, as it is Pressable's. */
	.strip :global(.look) {
		display: block;
		inline-size: 100%;
		padding: 0;
	}

	/* No still: the tile's glyph in the same square. */
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

	/* A picture carrying nobody is marked, for Name the rest. */
	.strip li.unnamed img {
		outline: 2px dashed var(--sift-line);
		outline-offset: -2px;
	}
</style>
