<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * The people Sift is proposing, and the tab the Faces page opens on.
	 *
	 * The Faces page is a row of tabs over one list ordered by how much a press settles, so it
	 * answers where to start while still saying which part somebody has reached, and what was put
	 * aside is a tab rather than a dropdown. This is the first tab, because one press here settles
	 * every face standing for somebody, and every one of those teaches Sift.
	 *
	 * Each tab reads its own tier of the same list on the server rather than a list of its own, so
	 * the order and the scoping stay in one place (see `FaceService.to_check`).
	 *
	 * Yes, and the no behind it. The question already carries the number, so the affirmative has
	 * one word to say and the refusal sits behind the chevron beside it.
	 *
	 * The menu's No writes the refusal for every face on the card. Yes and no are two answers to
	 * one question, and the yes already writes against every one of the same faces from the same
	 * press; the card shows the question whole (her name, the count, the crops). "No, one at a
	 * time" keeps a door for somebody unsure who wants to look. The server refuses them the same
	 * way it agrees: asked of the person, never of a list this card is holding (see
	 * `rejectLookAlikes`).
	 *
	 * Where the page was is carried in the address. It pages as Faces to name does (`CardPaging`;
	 * see `FaceGroupsPanel`): the first person on screen is written as `from`, and the server turns
	 * it into a place in the list (`FaceService.position_in_to_check`), because only the server
	 * holds the scoped, ranked list. A person answered since resolves to nothing and the top is
	 * served, which on a list that empties as it is answered is the page with the work on it.
	 *
	 * The page is a fixed 24: the cards are not measured here, as on Faces to name.
	 *
	 * And the groups that may be somebody, after them: two tiers on this one tab, a person's
	 * standing questions and a card per person of the unnamed groups that may be them
	 * (`MayBeCard`). Both ask whether faces are somebody Sift knows, so they share a tab; the
	 * server pages them as one list, her questions first. The two kinds of card share the person as
	 * their id, so a row is keyed by both.
	 *
	 * The tab's search box is the walls' own (`WallControls`), on the tab line through `ontools`.
	 * Its words live in the address as `who` (`FACES_WORDS`, through `WallWords`) and go to the server,
	 * which narrows the list by the person's name and aliases before the page is taken: the pager
	 * and the tab's count then describe what was found, never a filter of the page in hand.
	 */
	import { page as address } from '$app/state';
	import { goto } from '$app/navigation';
	import { onDestroy, untrack } from 'svelte';

	import FaceCovers from '$lib/components/faces/FaceCovers.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
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

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. And where the
	 *  search box goes: the far end of the tab line. See `OnTools`. */
	let { onpaging, ontools }: { onpaging?: OnPaging; ontools?: OnTools } = $props();

	/* The words the tab is searched by, read from the address; the box writes them there. */
	const addressWords = $derived(wordsIn(address.url, FACES_WORDS));
	const words = new WallWords(FACES_WORDS);
	let term = $state(untrack(() => wordsIn(address.url, FACES_WORDS)));
	/* Words that reach the address any other way than the box (Back, a link) are put in the box;
	   the box's own write is not handed back to somebody still typing. See `WallWords`. */
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
	/* No wall name: nothing here is measured, so there is nothing for a session to remember. */
	const paging = new CardPaging(PAGE);
	/* The address's anchor, honoured once, as the tab is entered. Read at mount rather than in an
	   effect: this panel draws one tab, so there is no filtering to re-enter, and an effect reading
	   the address would re-run on the anchor this panel itself writes. */
	paging.arrive(anchorIn(address.url));
	/** The card whose answer is in flight, by `key`. One at a time, and named rather than a flag,
	 *  because a spinner on every card would say the whole list is being agreed to. */
	let busy = $state<string | null>(null);

	async function load() {
		/* The address this read's anchor may be written onto, taken as the read starts, by the
		   time the answer lands the address may be the person somebody just opened. See
		   `rememberAnchor`. */
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
		/* In the SAME synchronous turn as the rows. See `CardPaging.land`. After an await, the
		   effect watching the offset would re-run with the old rows and ask again for this page. */
		paging.land(page.offset);
		rememberAnchor(address.url, path, people[0]?.id, page.offset);
		loading = false;
	}

	/* Read again whenever the page turns and whenever anything is decided anywhere in Organize:
	   answering one card empties it and the cards under it move up. The load UNTRACKED, as on
	   every wall: `fill` reads the anchor before its first await, and tracked, `land` clearing it
	   would re-run this and ask again for the page just landed. */
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

	/* And when a share or a restrict moves. This list is scoped: a face on a file this account may
	   not see is left out of it, so what belongs here changes with nothing else to announce it. */
	reloadOnLibraryChange(() => void load());

	$effect(() => {
		/* A noun, as `PagerProps.noun` asks: an empty tab reads "No faces to confirm". */
		onpaging?.(paging.asPager(people.length, total, 'faces to confirm'));
	});
	onDestroy(() => {
		onpaging?.(null);
		ontools?.(null);
	});

	/** The question a card asks, whole: the verb and the noun agree, which two halves spelled apart
	 *  would not ("Do these one face look like"). */
	function asked(person: ToCheckItem): string {
		return person.size === 1
			? `Does this one face look like ${person.person_name}?`
			: `Do these ${person.size.toLocaleString()} faces look like ${person.person_name}?`;
	}

	/* How close the best of them came, as a percentage: the same way every other surface in this
	   feature says it. Nothing at all where there is no measurement: a proposal offered off the back
	   of a named GROUP carries no confidence, and a figure invented for the sentence would be read
	   as one. */
	function surest(person: ToCheckItem): string | null {
		return person.best === null || person.best === undefined
			? null
			: `Surest at ${Math.round(person.best * 100)}%`;
	}

	/**
	 * Where one person's proposals are looked at, and taken off, one at a time.
	 *
	 * `from` names the tab it was opened from, so the trail on that screen reads "Organize > Faces
	 * > Needs Your Input > <name>" and its way back comes here rather than to People Sift can recognize,
	 * the queue that screen is registered under. See `organizeCrumbs`.
	 */
	function reviewHref(person: ToCheckItem): string {
		return `/organize/known-people/${encodeURIComponent(person.id)}?show=suggested&via=faces`;
	}

	/* Agree with every proposal standing for one person.
	 *
	 * No list of faces goes up with the press. See `confirmLookAlikes`. What comes back says how
	 * many were named and how many further faces were OFFERED as a result: agreeing extends the
	 * claim to whatever group those faces were waiting in, exactly as naming one by hand does, and
	 * a press that quietly proposed twenty more people-shaped questions should say so.
	 */
	/* Refuse every proposal standing for one person, in one press.
	 *
	 * The same shape as agreeing and for the same reasons: no list goes up, one card at a time is
	 * busy, and what comes back is a count rather than a promise. What it does NOT do is say
	 * anything about further proposals: refusing extends nothing, so there is no second number.
	 */
	async function refuse(person: ToCheckItem) {
		if (busy) return;
		busy = key(person);
		try {
			const answer = await rejectLookAlikes(person.id);
			/* Through `decided`, so the toast offers Undo on the receipt the press wrote: a No over
			   a whole pile is the press somebody wants back within seconds, not from History. */
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

	/* Yes on a card of groups: the ticked groups and the faces it showed of them. What comes back
	   is how many faces were confirmed and how many more were asked about, as for a person's own
	   questions above. */
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
	<!-- Only while there is nothing on screen yet. A reload after a decision keeps what is already
	     drawn and swaps it when the answer arrives; showing the skeleton again makes the page blink
	     out and back for every single answer, which is the one thing somebody working through a
	     list does over and over. -->
	{#if loading && people.length === 0}
		<Skeleton lines={3} />
	{:else if people.length === 0 && addressWords}
		<!-- A search that found nobody says so, rather than the empty tab's sentence: a list narrowed
		     to nothing is not a list with nothing left in it. -->
		<Empty scope="page" icon="search">{emptyWallSays('people', addressWords, false, '')}</Empty>
	{:else if people.length === 0}
		<Empty scope="page" icon="person" title="Nothing to confirm">
			Faces that look like people you have confirmed appear here, so one answer names all of them.
		</Empty>
	{:else}
		<ul class="people">
			{#each people as person (key(person))}
				<li class:may-be={person.kind === 'may_be'}>
					{#if person.kind === 'may_be'}
						<MayBeCard
							card={person}
							busy={busy !== null}
							answering={busy === key(person)}
							onyes={(pileIds, trackIds) => void agreeGroups(person, pileIds, trackIds)}
							onno={(pileIds) => void refuseGroups(person, pileIds)}
						/>
					{:else}
						<!-- The card every question on Organize wears (`DecisionCard`): the crops, then
						     the question, the line under it and the answers at the foot, so a row of
						     cards has its questions and its Yes on one line each. -->
						<DecisionCard>
							<!-- The crops are the link: a card shows twelve and checking a proposal
							     usually needs all of them, at the moment in the file where each was
							     found. No room is held for faces a card does not have: two faces take
							     one line, and the foot keeps the bottoms even. See `FaceCovers`. -->
							<FaceCovers
								faces={person.faces}
								most={CROPS_ON_A_CARD}
								hold={false}
								href={reviewHref(person)}
								label={`Review every face suggested for ${person.person_name}`}
							/>
							{#snippet question()}{asked(person)}{/snippet}
							<!-- The card already names the person, so the line says what they were
							     compared with and how close the best came, without the name again. -->
							{#snippet detail()}Compared with the faces already named.{#if surest(person)}<span
										class="sure">{' '}{surest(person)}</span
									>{/if}{/snippet}
							<!-- The affirmative leads and the other answers sit behind the chevron:
							     the refusal, whole, in one press (see the note at the head of this
							     file), and a door for somebody who wants to look first. -->
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
		</ul>
	{/if}
</section>

<style>
	.panel {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
	}

	.people {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(22rem, 1fr));
		gap: var(--space-3);
	}

	/* Each card fills its grid cell, so the cards of one row are one height and the foot of each
	   (the question and its answers) lands on one line. */
	.people > li {
		display: grid;
	}

	/* A may-be card holds a row per group, so one card can be many times the height of another.
	   Each stands at its own height, so a row of them does not stretch to its tallest. */
	.people > li.may-be {
		align-self: start;
	}

	/* How close the best of them came. A shade nearer the ink than the sentence it sits in, because
	   it is the one thing on that line worth reading first. */
	.sure {
		color: var(--sift-ink-2);
	}
</style>
