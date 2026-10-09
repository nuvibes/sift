<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* The people Sift is proposing, the Faces page's first tab: Yes settles every face standing for
	 * somebody, and the No behind the chevron refuses them all, asked of the person. Then the
	 * groups that may be somebody (`MayBeCard`), one list paged by the server, the page kept in the
	 * address. */
	import { page as address } from '$app/state';
	import { goto } from '$app/navigation';
	import { onDestroy, untrack } from 'svelte';

	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import CardWall from '$lib/components/organize/CardWall.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import MayBeCard from '$lib/components/organize/MayBeCard.svelte';
	import { Empty, Skeleton } from '$lib/components/common';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import type { OnTools } from '$lib/components/organize/OrganizeHeader.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { FACES_WORDS, WallWords, emptyWallSays, wordsIn } from '$lib/components/shell/wall-words';
	import { answered, decided } from '$lib/organize/organize.svelte';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { thing, type ToastWords } from '$lib/components/common/toast-pieces';
	import {
		CROPS_ON_A_CARD,
		TO_CHECK_PER_PAGE as PAGE,
		confirmGroups,
		confirmLookAlikes,
		rejectGroups,
		rejectLookAlikes,
		toCheck,
		type ToCheckItem,
		type ToCheckKind
	} from '$lib/people/faces.svelte';

	/** The tiers this tab draws, in the server's order. */
	const TIERS: readonly ToCheckKind[] = ['person', 'may_be'];

	/** Where the pager and the search box go, both drawn by the route. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();

	/* The words the tab is searched by, read from the address; the box writes them there. */
	const addressWords = $derived(wordsIn(address.url, FACES_WORDS));
	const words = new WallWords(FACES_WORDS);
	let term = $state(untrack(() => wordsIn(address.url, FACES_WORDS)));
	/* Words reaching the address another way are put in the box (see `WallWords`). */
	$effect(() => {
		const arrived = addressWords;
		untrack(() => {
			if (!words.echoed(arrived)) term = arrived;
		});
	});
	/* The words the rows on screen were read for, so a change of words starts at the top. */
	let readFor = untrack(() => wordsIn(address.url, FACES_WORDS));

	let people = $state<ToCheckItem[]>([]);
	let total = $state(0);
	let loading = $state(true);
	const paging = new CardPaging(PAGE, 'faces.to-confirm');
	/* The address's anchor, honoured once at mount: this panel writes it. */
	paging.arrive(anchorIn(address.url));
	/** The card whose answer is in flight, so only it spins. */
	let busy = $state<string | null>(null);

	async function load() {
		/* Taken as the read starts; the address may have moved on by the answer. */
		const path = address.url.pathname;
		const asked = addressWords;
		const page = await paging.fill(
			`person:${asked}`,
			() => people,
			(query) => {
				loading = true;
				return toCheck(query, 'waiting', TIERS, asked);
			},
			(answer) => ({ rows: answer.items, total: answer.total, offset: answer.offset })
		);
		// Overtaken by a newer read, which finishes this one's work.
		if (page === null) return;
		people = page.rows;
		total = page.total;
		/* In the same synchronous turn as the rows (`CardPaging.land`). */
		paging.land(page.offset);
		rememberAnchor(address.url, path, people[0]?.id, page.offset);
		loading = false;
	}

	/*
	 * Read again on a page turn and on any Organize decision; the load untracked, as on every wall.
	 */
	$effect(() => {
		void paging.offset;
		void paging.size;
		void answered.stamp;
		/* New words are a new list, read from its top. */
		const arrived = addressWords;
		untrack(() => {
			if (arrived !== readFor) {
				readFor = arrived;
				if (paging.restart()) return;
			}
			void load();
		});
	});

	$effect(() => {
		ontools?.(searchBox);
	});

	/* And when a share or restrict moves what this scoped list may show. */
	reloadOnLibraryChange(() => void load());

	$effect(() => {
		/* A noun, as `PagerProps.noun` asks: an empty tab reads "No faces to confirm". */
		onpaging?.(paging.asPager(people.length, total, 'faces to confirm'));
	});
	onDestroy(() => {
		onpaging?.(null);
		ontools?.(null);
	});

	/** The card's question whole, so verb and noun agree. */
	function asked(person: ToCheckItem): string {
		return person.size === 1
			? `Does this one face look like ${person.person_name}?`
			: `Do these ${person.size.toLocaleString()} faces look like ${person.person_name}?`;
	}

	/* How close the best came, as a percentage; nothing where there is no measurement. */
	function surest(person: ToCheckItem): string | null {
		return person.best === null || person.best === undefined
			? null
			: `Surest at ${Math.round(person.best * 100)}%`;
	}

	/** Where one person's proposals are looked at; `from` names this tab for the way back. */
	function reviewHref(person: ToCheckItem): string {
		return `/organize/known-people/${encodeURIComponent(person.id)}?show=suggested&via=faces`;
	}

	/* Refuse every proposal for one person in one press; nothing goes up but the person. */
	async function refuse(person: ToCheckItem) {
		if (busy) return;
		busy = key(person);
		try {
			const answer = await rejectLookAlikes(person.id);
			/* Through `decided`, so the toast offers Undo on the receipt. */
			decided(
				answer.changed === 1
					? "That face won't be suggested for them again"
					: `${counted(answer.changed)} faces won't be suggested for them again`,
				answer.decision_id ?? null
			);
		} catch {
			toasts.show("Those suggestions couldn't be discarded", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/** A card's own key: its kind and its person, since both kinds are keyed on the person. */
	function key(item: ToCheckItem): string {
		return `${item.kind}:${item.id}`;
	}

	function namedFaces(answer: { changed: number; offered: number }, card: ToCheckItem): ToastWords {
		const faces = answer.changed === 1 ? 'One face is ' : `${counted(answer.changed)} faces are `;
		const name = card.person_name;
		const who = name ? thing('person', card.person_id ?? card.id, name) : 'named';
		const more = answer.offered > 0 ? `. ${counted(answer.offered)} more were suggested.` : '';
		return [faces, who, more];
	}

	/* Yes on a card of groups: the ticked groups and the faces shown of them. */
	async function agreeGroups(card: ToCheckItem, pileIds: string[], trackIds: string[]) {
		if (busy) return;
		busy = key(card);
		try {
			const answer = await confirmGroups(card.id, pileIds, trackIds);
			decided(namedFaces(answer, card), answer.decision_id ?? null);
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	/* No on it: every group on the card, every face of theirs refused as the person. */
	async function refuseGroups(card: ToCheckItem, pileIds: string[]) {
		if (busy) return;
		busy = key(card);
		try {
			const answer = await rejectGroups(card.id, pileIds);
			decided(
				answer.changed === 1
					? "That face won't be suggested for them again"
					: `${counted(answer.changed)} faces won't be suggested for them again`,
				answer.decision_id ?? null
			);
		} catch {
			toasts.show("Those suggestions couldn't be discarded", { tone: 'error' });
		} finally {
			busy = null;
		}
	}

	async function agree(person: ToCheckItem) {
		if (busy) return;
		busy = key(person);
		try {
			const answer = await confirmLookAlikes(person.id);
			decided(namedFaces(answer, person), answer.decision_id ?? null);
		} catch {
			toasts.show("Those couldn't be confirmed", { tone: 'error' });
		} finally {
			busy = null;
		}
	}
</script>

{#snippet searchBox()}
	<WallControls
		noun="person"
		plural="people"
		bind:term
		onsettled={(typed) => words.write(address.url, typed)}
	/>
{/snippet}

<section class="panel">
	<!-- Only while nothing is on screen yet, so a reload does not blink. -->
	{#if loading && people.length === 0}
		<Skeleton lines={3} />
	{:else if people.length === 0 && addressWords}
		<!-- A search that found nobody says so. -->
		<Empty scope="page" icon="search">{emptyWallSays('people', addressWords, false, '')}</Empty>
	{:else if people.length === 0}
		<Empty scope="page" icon="person" title="Nothing to confirm">
			Faces that look like people you have confirmed appear here, so one answer names all of them.
		</Empty>
	{:else}
		<CardWall cards={paging.cards}>
			{#each people as person (key(person))}
				<li>
					{#if person.kind === 'may_be'}
						<MayBeCard
							card={person}
							busy={busy !== null}
							answering={busy === key(person)}
							onyes={(pileIds, trackIds) => void agreeGroups(person, pileIds, trackIds)}
							onno={(pileIds) => void refuseGroups(person, pileIds)}
						/>
					{:else}
						<!-- The Organize card: crops, question, the line under it, the answers. -->
						<DecisionCard opens={reviewHref(person)} detailLines={2}>
							<!--
							The crops are the link, two rows of six, room held so every card is one
							height.
							-->
							<FaceCovers
								faces={person.faces}
								most={CROPS_ON_A_CARD}
								total={person.size}
								href={reviewHref(person)}
								label={`Review every face suggested for ${person.person_name}`}
							/>
							<!-- How close the best came, on the line a group card ticks on. -->
							<p class="facts">{surest(person) ?? ''}</p>
							{#snippet question()}{asked(person)}{/snippet}
							{#snippet detail()}Compared with the faces already named.{/snippet}
							<!--
							Yes leads; the whole refusal and a door to look first sit behind the
							chevron.
							-->
							{#snippet answers()}
								<Answers
									yes={{ label: 'Yes', icon: 'check', run: () => void agree(person) }}
									rest={[
										{ label: 'No', icon: 'close', run: () => void refuse(person) },
										{
											label: 'No, one at a time',
											icon: 'arrow_forward',
											run: () => void goto(reviewHref(person))
										},
										{
											label: 'Show me',
											icon: 'arrow_forward',
											run: () => void goto(reviewHref(person))
										}
									]}
									about={person.person_name ?? ''}
									busy={busy === key(person)}
									disabled={busy !== null}
								/>
							{/snippet}
						</DecisionCard>
					{/if}
				</li>
			{/each}
		</CardWall>
	{/if}
</section>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	/* A control's height, as on a group card. */
	.facts {
		display: flex;
		align-items: center;
		min-block-size: var(--control-height-sm);
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}
</style>
