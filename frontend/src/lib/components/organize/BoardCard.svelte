<script lang="ts" module>
	import type { Card } from '$lib/organize/bands';

	/*
	 * The one word on every card's button. A card's button only opens its pile's page, so every
	 * card says it with the same word: the verb table's word for deciding on Sift's suggestions,
	 * which is what every page it opens is for.
	 */
	export const REVIEW = 'Review';

	/** What a card at nought says in place of its stills: the board's own words for the state. */
	export const CAUGHT_UP = 'Nothing to review';

	/**
	 * A pile with nothing in it. It keeps its card (every pile does, so the wall keeps one shape
	 * and a pile that is finished is never mistaken for one that is missing) and says so where its
	 * stills would be. Its button still opens its page, which is where its records' tabs are.
	 */
	export function settled(card: Card<unknown>): boolean {
		return card.count === 0;
	}
</script>

<script lang="ts">
	/*
	 * One card on the Organize board: what a pile is, how much of it is waiting, a look at it, and
	 * the way to its page.
	 *
	 * THE CARD, top to bottom: the pile's glyph and name; the one sentence saying what the pile is
	 * for (`purpose`, declared by the queue, the same for everybody); the count and what it counts,
	 * in the display face; two rows of six stills where the pile has any; and at the foot ONE
	 * button, Review, that opens the pile's page. Every card is that shape and nothing else: no
	 * chips, no question about one item, no answer and no Undo. Deciding happens on the page,
	 * where the items are in front of somebody; a board that decided one item in place would ask
	 * about a named person or file and describe none of the pile.
	 *
	 * ONE SHAPE ON EVERY CARD. The sentence keeps two lines whether it takes one or two, and the
	 * board gives every row the height of its tallest (`grid-auto-rows: 1fr` on the board), so
	 * every card on the board measures the same and every button stands on one line at the foot.
	 *
	 * The box and the foot are `DecisionCard`'s, the one card Organize draws, so the button starts
	 * its line under the words it answers, as on every Organize card.
	 *
	 * THE WHOLE CARD IS A PRESS, the way a tile is (`DecisionCard`'s `opens`): anywhere but its
	 * button opens what the button opens. The stills are pictures, never links.
	 *
	 * The way in is the queue's: the card asks `wayIn(queue)` for an address, which a queue
	 * declares when it is not a panel (`QueueView.opens`: the music card opens its task's row
	 * under `Settings > Tasks`). Null is a card that opens nothing, the honest state for a queue a
	 * newer server has and this build cannot draw: it draws no button and is not a press.
	 */
	import { goto } from '$app/navigation';
	import { SvelteSet } from 'svelte/reactivity';

	import { Button, SectionHeading } from '$lib/components/common';
	import Icon from '$lib/components/Icon.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import Thumb from '$lib/components/organize/Thumb.svelte';
	import { counted as figureOf } from '$lib/entity/entity-counts';
	import type { IconName } from '$lib/design/icons';
	import { titleOf } from '$lib/organize/bands';
	import { wayIn } from '$lib/organize/panels';
	import type { Queue } from '$lib/organize/organize.svelte';

	/*
	 * The stills: six across and two rows, enough to recognize a pile before opening it. Each still
	 * is a sixth of the card's width, so a row always holds six whole pictures whatever the column
	 * is, rather than a fixed size clipped part way through the last one. Every queue sends more
	 * than this, so a still that cannot be drawn is skipped and the next one takes its place.
	 */
	const STRIP_ACROSS = 6;
	const STRIP_ROWS = 2;

	/* The sentence saying what the card is for is the server's (`purpose`); where a queue declares
	   none, the card keeps the sentence's two lines empty rather than changing shape. */
	let { card }: { card: Card<Queue> } = $props();

	const queue = $derived(card.lead);
	const title = $derived(titleOf(card));
	/* Where the card goes: the queue's own address, where it has a way in and this build can draw
	   it. See the header. */
	const opens = $derived(wayIn(queue));
	const purpose = $derived(queue.purpose ?? '');

	/*
	 * What the number counts, worded for the number drawn, so a pile down to its last item reads "1
	 * shoot to review" rather than a plural. The server holds both wordings (`Summary.verb_one`).
	 *
	 * A card holding more than one queue that is work (a group: Faces, Duplicates) adds their
	 * counts, and they count different things (questions, files, groups), so no one queue's words
	 * say what the sum is. It is said as what the parts have in common: they are waiting. The
	 * parts themselves are the tabs of the page the card opens.
	 */
	const work = $derived(card.queues.filter((one) => one.pending).length);
	const counted = $derived(work > 1 ? 'waiting' : card.count === 1 ? queue.verb_one : queue.verb);

	/*
	 * The stills that could not be drawn, by kind and id: a picture not made yet, or one the server
	 * refused. A blank square among stills reads as a hidden or broken file, so it leaves and the
	 * next one the queue sent takes its place.
	 */
	const missing = new SvelteSet<string>();
	const keyOf = (picture: { kind: string; id: string }) => `${picture.kind}:${picture.id}`;

	const strip = $derived(
		queue.preview.filter((one) => !missing.has(keyOf(one))).slice(0, STRIP_ACROSS * STRIP_ROWS)
	);

	function open(): void {
		if (opens) void goto(opens);
	}
</script>

<div class="board-card">
	<DecisionCard answers={opens ? door : undefined} opens={opens ?? undefined}>
		<SectionHeading band>
			{#snippet leading()}<span class="glyph"><Icon name={queue.icon as IconName} size={20} /></span
				>{/snippet}
			{title}
		</SectionHeading>

		<!-- What the pile is for, in the queue's own sentence. Two lines kept, whatever it takes. -->
		<p class="purpose">{purpose}</p>

		<!-- The lead: the number, and what it counts, said once. -->
		<p class="lead">
			<span class="n">{figureOf(card.count)}</span>
			<span class="verb">{counted}</span>
		</p>

		{#if settled(card)}
			<!-- Nothing waiting: said where the stills would be, so the card keeps its place and
			     its shape and nobody wonders where the pile went. -->
			<p class="caught-up">{CAUGHT_UP}</p>
		{:else if strip.length > 0}
			<!-- Keyed by POSITION as well as by id: two folders holding the same file have the same
			     still, and keyed on the id alone that is a duplicate key. -->
			<span class="strip">
				{#each strip as picture, at (`${picture.kind}:${picture.id}:${at}`)}
					<Thumb
						kind={picture.kind}
						id={picture.id}
						art={picture.art}
						onmissing={() => missing.add(keyOf(picture))}
					/>
				{/each}
			</span>
		{/if}
	</DecisionCard>
</div>

<!-- The one button: it opens the pile's page and does nothing else. Named with the card, so a
     screen reader's list of buttons says which page each one opens. -->
{#snippet door()}
	<span class="door"
		><Button size="small" trailing="arrow_forward" aria-label={`${REVIEW} ${title}`} onclick={open}
			>{REVIEW}</Button
		></span
	>
{/snippet}

<style>
	/*
	 * The card, filling its cell of the board: the board gives every card one height, and
	 * `DecisionCard` pushes the button to the foot, so every button stands on one line.
	 */
	.board-card {
		display: grid;
		min-inline-size: 0;
	}

	.glyph {
		display: flex;
		color: var(--sift-ink-2);
	}

	/*
	 * What the pile is for: two lines kept on every card, and never more, so the count under it
	 * stands at one height across the board. The sentence is declared short enough for two lines
	 * of the narrowest column (a gate holds it); the clamp is the floor under that rule, not the
	 * rule itself.
	 */
	.purpose {
		display: -webkit-box;
		-webkit-box-orient: vertical;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		overflow: hidden;
		min-block-size: 2lh;
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* The lead: the number in the display face, and what it counts beside it on one baseline. */
	.lead {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: var(--space-1) var(--space-2);
		margin: 0;
	}

	.n {
		font: var(--text-display);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.verb {
		font: var(--text-h2);
		color: var(--sift-ink-2);
	}

	/* The card at nought: its one line where the stills would be. */
	.caught-up {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	/* Two rows of six, each still a sixth of the card, square and cropped to fill. */
	.strip {
		display: grid;
		grid-template-columns: repeat(6, minmax(0, 1fr));
		gap: var(--space-1);
	}

	.strip :global(img) {
		display: block;
		inline-size: 100%;
		block-size: auto;
		aspect-ratio: 1 / 1;
	}

	/* The button stands where every Organize card's buttons stand: at the start of its line. */
	.door {
		display: flex;
		justify-content: var(--card-actions-justify, flex-end);
	}
</style>
