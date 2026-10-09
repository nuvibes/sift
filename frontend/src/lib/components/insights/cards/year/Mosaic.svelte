<script lang="ts">
	/*
	 * THE FILES VIEWED MOST, LARGE: the first takes the top of the card, the rest share the room
	 * under it, each its picture to its edges with its place over the corner and its name and views
	 * over the foot, the name a way to the file.
	 */
	import { Avatar, HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { saidOf } from '$lib/components/insights/figures';

	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		rows: readonly NamedRow[];
	}

	let { rows }: Props = $props();

	const shown = $derived(rows.slice(0, 5));
	/* A last tile left alone on its row takes the row. */
	const alone = (index: number) => index === shown.length - 1 && index % 2 === 1;
</script>

<ol class="mosaic" class:few={shown.length < 4}>
	{#each shown as row, index (row.piece.id ?? index)}
		<li class="tile" class:lead={index === 0} class:alone={alone(index)}>
			<Avatar src={row.cover} name={row.piece.text} decorative />
			<span class="place">{index + 1}</span>
			<p class="name">
				<span class="said"><HistorySentence pieces={[row.piece]} /></span>
				<span class="views">{saidOf(row.said, row.value, row.unit)}</span>
			</p>
		</li>
	{/each}
</ol>

<style>
	.mosaic {
		display: grid;
		flex: 1;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		grid-template-rows: minmax(0, 3fr) repeat(2, minmax(0, 2fr));
		gap: var(--space-2);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		overflow: hidden;
		list-style: none;
	}

	.mosaic.few {
		grid-template-rows: minmax(0, 3fr) minmax(0, 2fr);
	}

	.tile {
		position: relative;
		display: grid;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.tile.lead,
	.tile.alone {
		grid-column: 1 / -1;
	}

	/* The picture fills its tile, whatever the tile's shape. */
	.tile > :global(.avatar) {
		grid-area: 1 / 1;
		aspect-ratio: auto;
		block-size: 100%;
	}

	/* Over the picture, which stands in its own layer: the place in heavy type on a scrim. */
	.place {
		position: relative;
		grid-area: 1 / 1;
		align-self: start;
		justify-self: start;
		margin: var(--space-2);
		padding-inline: var(--space-2);
		background: var(--sift-scrim);
		font: var(--story-text-headline);
		font-variant-numeric: tabular-nums;
		color: var(--sift-ink);
	}

	.name {
		position: relative;
		display: flex;
		grid-area: 1 / 1;
		align-self: end;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--space-2);
		margin: 0;
		padding: var(--space-2) var(--space-3);
		background: var(--sift-scrim);
		font: var(--text-label);
		color: var(--sift-ink);
	}

	.said {
		flex: 1;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.views {
		flex: none;
		font-variant-numeric: tabular-nums;
	}

	/* The first file's views read as its figure. */
	.lead .views {
		font: var(--text-h2);
		color: var(--sift-accent-tint-1);
	}
</style>
