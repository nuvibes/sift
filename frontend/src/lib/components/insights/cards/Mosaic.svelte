<script lang="ts">
	/*
	 * The files viewed most, side by side: the first takes the top half of the card, the next
	 * four share the half under it. Each is its picture with its name and its views over the foot,
	 * the name a way to the file.
	 */
	import { Avatar, HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';
	import { saidOf } from '$lib/components/insights/figures';

	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		rows: readonly NamedRow[];
	}

	let { rows }: Props = $props();
</script>

<ol class="mosaic" class:few={rows.length < 4}>
	{#each rows.slice(0, 5) as row, index (row.piece.id ?? index)}
		<li class="tile" class:lead={index === 0}>
			<Avatar src={row.cover} name={row.piece.text} decorative lazy />
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
		grid-template-rows: repeat(4, minmax(0, 1fr));
		gap: var(--space-2);
		min-block-size: 0;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.mosaic.few {
		grid-template-rows: repeat(3, minmax(0, 1fr));
	}

	.tile {
		position: relative;
		display: grid;
		overflow: hidden;
		border-radius: var(--radius-md);
		box-shadow: var(--elev-2);
	}

	.tile.lead {
		grid-column: 1 / -1;
		grid-row: span 2;
	}

	/* The picture fills its tile, whatever the tile's shape. */
	.tile > :global(.avatar) {
		grid-area: 1 / 1;
		aspect-ratio: auto;
		block-size: 100%;
	}

	/* Over the picture, which stands in its own layer. */
	.name {
		position: relative;
		display: flex;
		grid-area: 1 / 1;
		align-self: end;
		justify-content: space-between;
		gap: var(--space-2);
		margin: 0;
		padding: var(--space-2);
		background: var(--sift-scrim);
		font: var(--text-label);
		color: var(--sift-ink);
	}

	.said {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.views {
		flex: none;
		font-variant-numeric: tabular-nums;
	}
</style>
