<script lang="ts">
	/*
	 * Organize: what needs you.
	 *
	 * Browse answers "what do I have". This answers "what needs me", and that is why it is a second
	 * screen rather than a view of the first one. It follows that the failure mode is building a
	 * place to LOOK at files: there are no grid tools here, no sort, no sizes and no filter bar. If
	 * somebody wants to look at files they go to Browse. This shows only what is pending, and it is
	 * finished when it is empty.
	 *
	 * The board knows nothing about faces or folders. It draws what registered: a name, a sentence
	 * saying what the pile is for, a count, and a few things to look at so a pile is recognizable
	 * before it is opened, with one button that opens the pile's page. Nothing on the board decides
	 * anything: deciding happens on the page, where the items are in front of somebody. A panel
	 * added in a later version appears here without this file changing.
	 *
	 * ## A board of cards, read across
	 *
	 * One card per pile, across the whole of the frame like a wall of files, in the order the
	 * queues' bands give (the work first, then the tidying, then what Sift could not read). Piles of
	 * one kind are read by scanning: every card is one size and one shape, the count leads each, and
	 * a wall of cards at one height reads as one group of piles where a list of rows at their own
	 * heights read as a queue. The band is DECLARED BY THE QUEUE, on the server, for
	 * the same reason the icon and the sentence are; this file knows the order and nothing else. See
	 * `$lib/organize/bands`.
	 *
	 * EVERY PILE IS ON THE BOARD, AT NOUGHT TOO. A pile that left the wall when it emptied could
	 * not be told from one never turned on, so a card at nought keeps its place and says there is
	 * nothing to review where its stills would be (`BoardCard`); its page, with the records' tabs,
	 * is still reached through it. When nothing in the work band is waiting the board says so over
	 * the cards, or as the whole screen where there is no pile at all.
	 *
	 * Two things are not on this screen. **Records** (what was set aside, who has been identified)
	 * are reached through their group's TABS, beside the pile they were settled from, because a
	 * count that never goes down cannot sit on a screen whose promise is that it empties. **What was
	 * decided** is History, showing Decisions (`Settings > Tasks and Activity > App History`): every answer given
	 * here and every filing Sift made by itself is a line of History already, each with its Undo, so
	 * a record of its own would be a second place to look for one stream. The header's Decisions is the
	 * way there from here.
	 */

	import { ApiError } from '$lib/api/client';
	import { Empty, Problem, SettingLink, Skeleton } from '$lib/components/common';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import BoardCard from '$lib/components/organize/BoardCard.svelte';
	import Hint from '$lib/components/insights/Hint.svelte';
	import { bandsOf } from '$lib/organize/bands';
	import { heldBoard, type Board, answered } from '$lib/organize/organize.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';

	/* Held in a store rather than here, so coming back to this screen draws the last board at once
	   instead of a blank page and a request. See `heldBoard`. */
	const found = $derived<Board | null>(heldBoard.found);
	let failed = $state(false);
	/* Refused, held apart from failed, because they are not the same answer and only one of them
	   is worth trying again. This screen is admin-only and refused on the server, so a guest who
	   types the address gets a 403, and collapsed into `failed` that reads as "That could not be
	   loaded. Try again in a moment", which is untrue twice over: nothing went wrong, and trying
	   again will never work. It would also describe the feature to somebody who may not use it. */
	let refused = $state(false);

	/* Whether the answer has ARRIVED, held apart from whether it was empty.
	 *
	 * The two are different states with different screens: one says "still looking", the other
	 * says "nothing needs you", and they must never share a code path. A check that measured the
	 * screen before its data landed would find the empty state and pass.
	 */
	const ready = $derived(found !== null || failed || refused);
	const queues = $derived(found?.queues ?? []);
	/* The cards: every pile, in the order their bands give, with the records left out (see
	   `bandsOf`). One entry is one PAGE rather than one queue: queues sharing a group are tabs of
	   one screen, and drawn apart they would be two ways in to one job. A pile at nought is a card
	   too; see the header. */
	const cards = $derived(bandsOf(queues).flatMap((band) => band.cards));
	/* What the empty board says after its first words: what would put something here. */
	const EMPTY_SENTENCE =
		'Sift has done everything it can for you. New questions appear here as files arrive, ' +
		'like a folder named after a person or a group of faces that may be one person.';
	/* Whether anything is asking. Only the first band counts towards it: a log with nothing in it
	   is not an achievement and a log with things in it is not work, so a screen whose only cards
	   are refused files has still reached the state this screen is trying to reach. */
	const nothingWaiting = $derived(
		queues.every((one) => one.band !== 'decision' || one.count === 0)
	);

	/*
	 * What this screen offers the bar above it: nothing, said in its own words.
	 *
	 * A screen that publishes nothing falls through to `NOT_HERE`, so all four controls read
	 * "Nothing to order on this screen": true, and identical to what Settings and a login say, on
	 * a screen that plainly has piles in front of you that could be counted and ordered. This one
	 * looks like a wall and is not, which is exactly when the reason is worth giving.
	 *
	 * It genuinely cannot do any of the four. A queue is ordered by how sure the machine is and that
	 * order is the whole product; the stills are at a fixed size; and a pile does not play. Three
	 * of those are said here in this screen's own words.
	 *
	 * The fourth is not, and that is the contract rather than an omission: an empty `sorts` list IS
	 * the answer for the order control, and a reason beside it would be the same fact written twice
	 * and free to disagree with the list it describes. See `ScreenTools.sorts`.
	 */
	const mine = Symbol('organize-board');

	$effect(() => {
		screenBar.publish(mine, {
			filterable: 'This screen lists things to review, not files',
			resizable: 'A face is cut to one size here',
			playable: "A face is a still, and a still doesn't play"
		});
	});

	$effect(() => () => screenBar.release(mine));

	async function load() {
		try {
			await heldBoard.refresh();
			failed = false;
			refused = false;
		} catch (error) {
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				refused = true;
			} else {
				failed = true;
			}
		}
	}

	/* And again whenever anything is decided. Reading `stamp` is the whole subscription:
	   a decision made inside a panel changes counts this screen is drawing, and without
	   this they would stay as they were until the page was loaded again. */
	$effect(() => {
		answered.stamp;
		void load();
	});

	/* And again whenever a share or a restrict moves. Every count here is scoped, so what belongs on
	   the screen changes without anything being imported and with nothing else to announce it. */
	reloadOnLibraryChange(() => void load());
</script>

<svelte:head><title>Organize</title></svelte:head>

<!--
	No `measure`: the board is a wall, not a page of reading, so it takes the frame's whole width at
	the inset every wall has, and a wide screen gets more columns rather than wider margins.
-->
<PageFrame>
	{#snippet header()}
		<!--
			No lede: every card carries its OWN sentence saying what its pile is for
			(`Queue.purpose`), and a general sentence describing the screen above them would be
			the less useful description and the one free to drift from what is under it.
		-->
		<!-- The way to what was decided here: History, showing Decisions, where each line keeps its
		     Undo. A link into Settings and not a pile, because it is a record and not work. -->
		{#snippet controls()}
			<SettingLink section="tasks" setting="activity.decisions">Decisions</SettingLink>
		{/snippet}
		<PageHeader title="Organize" icon="inbox" {controls} />
	{/snippet}
	{#if !ready}
		<Skeleton lines={3} />
	{:else if refused}
		<p class="quiet">This screen is for the administrator of this library.</p>
	{:else if failed}
		<Problem message="That couldn't be loaded. Try again in a moment." />
	{:else}
		{#if nothingWaiting}
			<!-- A hint, once per account, on the first board with nothing on it. -->
			<Hint name="organize_empty" />
			<!--
				The goal state, and it reads as one: this screen is meant to be empty most of the time,
				so "nothing needs you" is success rather than a broken page, and it says what would put
				something here. The whole screen's empty state when no card is left; a sentence over the
				cards that remain (the tidying, what Sift could not read) otherwise.
			-->
			{#if cards.length === 0}
				<Empty scope="page" icon="inbox" title="Nothing to review">{EMPTY_SENTENCE}</Empty>
			{:else}
				<Empty scope="block">Nothing needs you right now. {EMPTY_SENTENCE}</Empty>
			{/if}
		{/if}

		{#if cards.length > 0}
			<!-- ONE WALL: the bands ORDER the cards, with no headings between them, so the wall lays
			     out as even rows. The accessible name is one list, "Organize". -->
			<ul class="cards" aria-label="Organize">
				{#each cards as card (card.lead.name)}
					<li><BoardCard {card} /></li>
				{/each}
			</ul>
		{/if}
	{/if}
</PageFrame>

<style>
	/*
	 * AS MANY COLUMNS AS FIT, one on a phone. The board fills the frame the way a wall of files does:
	 * a column is at least `--board-column-min` and the columns share what is left, so the cards run
	 * edge to edge at every width (three at 1280, four at 1600, five at 1920) and never stand in the
	 * middle of the screen. `min()` keeps a window narrower than one column from scrolling sideways.
	 * EVERY CARD ONE SIZE: the piles are of one kind and read by scanning, so every card is the
	 * column's width and every ROW the tallest card's height (`grid-auto-rows: 1fr`), a last row of
	 * one card included. Rows sized to their own contents would put a card with stills beside one
	 * without at one height and the next row at another: cards of three shapes. At a
	 * phone's width (the shell's own rule) one column, every card still one height.
	 */
	.cards {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(var(--board-column-min), 100%), 1fr));
		grid-auto-rows: 1fr;
		gap: var(--space-4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.cards > li {
		display: grid;
		min-inline-size: 0;
	}

	@media (max-width: 767px) {
		.cards {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
