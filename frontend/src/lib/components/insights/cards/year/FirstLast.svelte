<script lang="ts">
	/*
	 * THE PERIOD AS A STRIP: its first file at the left and its last at the right, the time
	 * between them a track, and under the track the seam ("A lot happened in between") with the
	 * files imported in between as its figure. Each file is its picture, its name as a way to it,
	 * and the time of day it was opened or closed.
	 */
	import { Avatar, HistorySentence } from '$lib/components/common';
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import type { components } from '$lib/api/schema';
	import { figureWords, saidOf } from '$lib/components/insights/figures';

	type NamedRow = components['schemas']['NamedRow'];
	type Figure = components['schemas']['Figure'];

	interface Props {
		rows: readonly NamedRow[];
		figure?: Figure | null;
	}

	let { rows, figure = null }: Props = $props();

	const ENDS = ['First', 'Last'];
	/* The marks along the track, one a stretch of the period. */
	const TICKS = 5;
</script>

{#snippet cover(row: NamedRow | undefined, at: string)}
	<div class="cover {at}">
		{#if row}<Avatar src={row.cover} name={row.piece.text} decorative />{/if}
	</div>
{/snippet}

{#snippet about(row: NamedRow | undefined, which: string, at: string)}
	<p class="about {at}">
		{#if row}
			<span class="which">{which}</span>
			<span class="name"><HistorySentence pieces={[row.piece]} /></span>
			<span class="when">{saidOf(row.said, row.value, row.unit)}</span>
		{/if}
	</p>
{/snippet}

<div class="first-last">
	<div class="strip">
		{@render cover(rows[0], 'start')}
		<div class="track" aria-hidden="true">
			{#each { length: TICKS } as _, index (index)}
				<span class="tick"></span>
			{/each}
		</div>
		{@render cover(rows[1], 'end')}
		{@render about(rows[0], ENDS[0], 'start')}
		{@render about(rows[1], ENDS[1], 'end')}
	</div>
	<div class="seam">
		<p class="between">A lot happened in between</p>
		{#if figure}
			<FigureCard
				label={figure.label}
				value={figure.value}
				format={(value) =>
					value === figure.value
						? saidOf(figure.said, value, figure.unit)
						: figureWords(value, figure.unit)}
				size="large"
				tone="lit"
				ground={false}
			/>
		{/if}
	</div>
</div>

<style>
	.first-last {
		display: flex;
		flex: 1;
		flex-direction: column;
		justify-content: center;
		gap: var(--space-6);
		min-block-size: 0;
		overflow: hidden;
	}

	/* The two ends and the track between them, on one line: first at the left, last at the right,
	   each one's words under its picture. */
	.strip {
		display: grid;
		grid-template-columns: minmax(0, 5fr) minmax(0, 2fr) minmax(0, 5fr);
		align-items: center;
		gap: var(--space-2);
	}

	.start {
		grid-column: 1;
	}

	.end {
		grid-column: 3;
	}

	.cover {
		display: grid;
		aspect-ratio: 3 / 4;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.cover > :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}

	/* Each end's words within its own column: a long name is cut on its one line. */
	.about {
		display: flex;
		flex-direction: column;
		align-self: start;
		gap: var(--space-1);
		inline-size: 100%;
		min-inline-size: 0;
		margin: 0;
		overflow: hidden;
	}

	.about.end {
		text-align: end;
	}

	.about .name {
		display: block;
		inline-size: 100%;
	}

	.which {
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-accent-tint-1);
	}

	.name {
		overflow: hidden;
		font: var(--text-h3);
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.when {
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
		font-variant-numeric: tabular-nums;
	}

	/* The track runs between the two pictures at their middle, a dot for each stretch of it. */
	.track {
		display: flex;
		grid-column: 2;
		align-items: center;
		justify-content: space-between;
		block-size: var(--chart-mark-gap);
		background: var(--sift-accent-tint-2);
	}

	.tick {
		inline-size: var(--space-2);
		aspect-ratio: 1;
		border-radius: 50%;
		background: var(--sift-accent-tint-1);
	}

	.seam {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		text-align: center;
	}

	.between {
		margin: 0;
		font: var(--text-h2);
		color: var(--sift-ink);
	}
</style>
