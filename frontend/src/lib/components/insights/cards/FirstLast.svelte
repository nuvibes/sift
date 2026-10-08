<script lang="ts">
	/*
	 * The period's first file and its last, one over the other, and between them the seam: "A lot
	 * happened in between", with the files that arrived in between as its figure. Each file is its
	 * picture, its name as a way to it, and the time of day it was opened or closed.
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
</script>

{#snippet end(row: NamedRow, which: string)}
	<div class="end">
		<div class="cover"><Avatar src={row.cover} name={row.piece.text} decorative lazy /></div>
		<p class="about">
			<span class="which">{which}</span>
			<span class="name"><HistorySentence pieces={[row.piece]} /></span>
			<span class="when">{saidOf(row.said, row.value, row.unit)}</span>
		</p>
	</div>
{/snippet}

<div class="first-last">
	{#if rows[0]}{@render end(rows[0], ENDS[0])}{/if}
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
				size="small"
				tone="lit"
				ground={false}
			/>
		{/if}
	</div>
	{#if rows[1]}{@render end(rows[1], ENDS[1])}{/if}
</div>

<style>
	.first-last {
		display: grid;
		flex: 1;
		grid-template-rows: minmax(0, 1fr) auto minmax(0, 1fr);
		gap: var(--space-3);
		min-block-size: 0;
	}

	.end {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		align-items: center;
		gap: var(--space-3);
		min-block-size: 0;
	}

	.cover {
		block-size: 100%;
		aspect-ratio: 3 / 4;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.cover > :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}

	.about {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
		margin: 0;
	}

	.which {
		font: var(--text-micro);
		letter-spacing: var(--tracking-micro);
		text-transform: uppercase;
		color: var(--sift-ink-2);
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

	/* The seam: a rule either side of the words, the figure under them. */
	.seam {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-1);
		padding-block: var(--space-2);
		border-block: 1px solid var(--sift-line);
		text-align: center;
	}

	.between {
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}
</style>
