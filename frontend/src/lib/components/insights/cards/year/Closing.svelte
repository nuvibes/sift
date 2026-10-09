<script lang="ts">
	/*
	 * THE YEAR AT A GLANCE, closing its deck: the year's top file as a band of picture with the top
	 * person's face over its corner, then its six figures as tiles (the time viewed, the files
	 * imported, the top person and Site, the busiest day, the visits), each its label, its figure
	 * counting up as the card arrives and the thing it names as a way to it. The card's own foot
	 * signs it.
	 */
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import { Avatar, HistorySentence, Panel } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { figureWords, saidOf } from '$lib/components/insights/figures';
	import { pictureOf } from '$lib/components/insights/cards/year/pictures';

	type Figure = components['schemas']['Figure'];

	interface Props {
		figures: readonly Figure[];
		/** The year's top file's picture, where the reader may be shown it. */
		cover?: string | null;
	}

	let { figures, cover = null }: Props = $props();

	/* The top person's face: the person a figure's caption names. */
	const top = $derived(
		figures.flatMap((figure) => figure.caption).find((piece) => piece.kind === 'person') ?? null
	);
	const face = $derived(top ? pictureOf(top) : null);
</script>

<div class="closing">
	{#if cover || face}
		<div class="pictures" class:banded={cover !== null}>
			{#if cover}
				<div class="band"><Avatar src={cover} name="" decorative /></div>
			{/if}
			{#if top && face}
				<span class="top-face"><Avatar src={face} name={top.text} shape="face" decorative /></span>
			{/if}
		</div>
	{/if}
	<ul class="figures">
		{#each figures as figure (figure.label)}
			<li class="tile">
				<Panel tone="recessed" corner="md" inset="sm">
					<FigureCard
						label={figure.label}
						value={figure.value}
						format={(value) =>
							value === figure.value
								? saidOf(figure.said, value, figure.unit)
								: figureWords(value, figure.unit)}
						size="small"
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
</div>

<style>
	.closing {
		display: flex;
		flex: 1;
		flex-direction: column;
		gap: var(--space-3);
		min-block-size: 0;
		overflow: hidden;
	}

	/* The top file across the card, the top face over its foot at the start, hanging under it
	   into a row of its own so the tiles start below it. */
	.pictures {
		display: grid;
		grid-template-columns: var(--story-face-small) minmax(0, 1fr);
		grid-template-rows: minmax(0, 1fr) calc(var(--story-face-small) / 2);
	}

	.banded {
		flex: 0 1 30%;
		min-block-size: var(--story-face-small);
	}

	.band {
		display: grid;
		grid-area: 1 / 1 / 3 / 3;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.band > :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}

	.top-face {
		position: relative;
		display: grid;
		grid-area: 2 / 1 / 4 / 2;
		inline-size: var(--story-face-small);
		aspect-ratio: 1;
		margin-inline-start: var(--space-3);
		overflow: hidden;
		border-radius: 50%;
		background: var(--sift-accent-shade-2);
		box-shadow: var(--elev-3);
	}

	.top-face > :global(.avatar) {
		inline-size: 100%;
	}

	.figures {
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

	.named {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
</style>
