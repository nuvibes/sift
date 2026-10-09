<script lang="ts" module>
	import type { Card } from '$lib/organize/bands';

	/** The one word on every card's button, which opens the pile's page. */
	export const REVIEW = 'Review';

	/** What a card at nought says in place of its stills: the board's own words for the state. */
	export const CAUGHT_UP = 'Nothing to review';

	/** A pile with nothing in it keeps its card and says so where its stills would be. */
	export function settled(card: Card<unknown>): boolean {
		return card.count === 0;
	}
</script>

<script lang="ts">
	/* One card on the Organize board: glyph and name, the pile's purpose, the count, two rows of
	 * stills and one Review button, every card the same shape. The whole card is a press; the way
	 * in is `wayIn(queue)`, and null opens nothing. */
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

	/* Six across, two rows, each a sixth of the card; a still that cannot be drawn is skipped. */
	const STRIP_ACROSS = 6;
	const STRIP_ROWS = 2;

	/* The purpose is the server's; with none, the two lines stay empty. */
	let { card }: { card: Card<Queue> } = $props();

	const queue = $derived(card.lead);
	const title = $derived(titleOf(card));
	const opens = $derived(wayIn(queue));
	const purpose = $derived(queue.purpose ?? '');

	/* The count's words for the number drawn; a group of queues says they are waiting. */
	const work = $derived(card.queues.filter((one) => one.pending).length);
	const counted = $derived(work > 1 ? 'waiting' : card.count === 1 ? queue.verb_one : queue.verb);

	/* Stills that could not be drawn, so the next one takes their place. */
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
			<!-- Nothing waiting, said where the stills would be. -->
			<p class="caught-up">{CAUGHT_UP}</p>
		{:else if strip.length > 0}
			<!-- Keyed by position too: two folders can share a still. -->
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

<!-- Named with the card, so a screen reader can tell the buttons apart. -->
{#snippet door()}
	<span class="door"
		><Button size="small" trailing="arrow_forward" aria-label={`${REVIEW} ${title}`} onclick={open}
			>{REVIEW}</Button
		></span
	>
{/snippet}

<style>
	.board-card {
		display: grid;
		min-inline-size: 0;
	}

	.glyph {
		display: flex;
		color: var(--sift-ink-2);
	}

	/* Two lines kept on every card; the clamp is a floor under the gate on the sentence. */
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
