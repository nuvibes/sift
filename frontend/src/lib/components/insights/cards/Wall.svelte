<script lang="ts" module>
	import type { components } from '$lib/api/schema';

	/** A Saved Layout's grid, as the card carries it. */
	export type WallShape = components['schemas']['ShapeBody'];
</script>

<script lang="ts">
	/*
	 * The most-used Saved Layout in miniature: its own grid, each cell a file it showed, the most
	 * played first. A cell with no file to show (one the reader may not be told of) stays the
	 * wall's dark ground.
	 */
	import { Avatar } from '$lib/components/common';

	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		wall: WallShape;
		rows: readonly NamedRow[];
	}

	let { wall, rows }: Props = $props();
</script>

<div class="wall" style:--cols={wall.cols} style:--rows={wall.rows} aria-hidden="true">
	{#each wall.slots as slot, index (index)}
		<span
			class="cell"
			style:grid-area={`${slot.row + 1} / ${slot.col + 1} / span ${slot.row_span} / span ${slot.col_span}`}
		>
			{#if rows[index]}
				<Avatar src={rows[index].cover} name={rows[index].piece.text} shape="portrait" lazy />
			{/if}
		</span>
	{/each}
</div>

<style>
	/* The screen a wall fills, 16:9, its cells a hairline apart on the dark of Theater. */
	.wall {
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		grid-template-rows: repeat(var(--rows), minmax(0, 1fr));
		gap: var(--chart-mark-gap);
		inline-size: 100%;
		aspect-ratio: 16 / 9;
		padding: var(--chart-mark-gap);
		border-radius: var(--radius-md);
		background-color: var(--sift-bg);
		box-shadow: var(--elev-3);
	}

	.cell {
		display: grid;
		overflow: hidden;
		border-radius: var(--radius-sm);
		background-color: var(--sift-surface-2);
	}

	.cell > :global(.avatar) {
		aspect-ratio: auto;
		block-size: 100%;
	}
</style>
