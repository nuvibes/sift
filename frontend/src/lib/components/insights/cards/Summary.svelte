<script lang="ts">
	/*
	 * The year at a glance, closing its deck: its six figures as tiles of one bento, the time
	 * viewed across the top, then the files that arrived, the sessions, the top person and Site and
	 * the busiest day, each tile its label, its figure counting up as the card arrives, and the
	 * thing it names as a way to it.
	 */
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import { HistorySentence, Panel } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { figureWords, saidOf } from '$lib/components/insights/figures';

	type Figure = components['schemas']['Figure'];

	interface Props {
		figures: readonly Figure[];
	}

	let { figures }: Props = $props();

	/* The first tile spans the card, and so does the last where the rest leave it alone on a row. */
	const wide = (index: number) =>
		index === 0 || (index === figures.length - 1 && figures.length % 2 === 0);
</script>

<ul class="summary">
	{#each figures as figure, index (figure.label)}
		<li class="tile" class:wide={wide(index)}>
			<Panel tone="recessed" corner="md" inset="sm">
				<FigureCard
					label={figure.label}
					value={figure.value}
					format={(value) =>
						value === figure.value
							? saidOf(figure.said, value, figure.unit)
							: figureWords(value, figure.unit)}
					size={index === 0 ? 'large' : 'small'}
					tone="lit"
					ground={false}
				>
					{#snippet caption()}
						{#if figure.caption.length > 0}
							<span class="named"><HistorySentence pieces={figure.caption} /></span>
						{/if}
					{/snippet}
				</FigureCard>
			</Panel>
		</li>
	{/each}
</ul>

<style>
	.summary {
		display: grid;
		flex: 1;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		grid-auto-rows: minmax(0, 1fr);
		gap: var(--space-2);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.tile {
		display: grid;
		min-inline-size: 0;
	}

	/* The tile's panel fills its cell, its figure in the middle of it. */
	.tile > :global(.panel) {
		display: grid;
		align-content: center;
		overflow: hidden;
	}

	.tile.wide {
		grid-column: 1 / -1;
	}

	.named {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
