<script lang="ts">
	/* NOT ON THE GALLERY: this is a row of the app's own buttons reading Theater's wall, drawn at the
	   head of the shared filter panel. A second live copy would be a second cell chooser writing the
	   one piece of state that says which cell the panel is editing: two answers to a question with
	   one right answer. Everything it is MADE of is on the gallery. */

	/*
	 * Which cell the shared filter panel is editing, numbered as the keyboard and the cell bars
	 * are.
	 */
	import { Button } from '$lib/components/common';
	import { aims } from '$lib/theater/aim';
	import { showing } from '$lib/theater/wall.svelte';

	const wall = $derived(showing.wall);
	/* The focused cell, the one answer everything that means "this cell" reads. */
	const editing = $derived(Math.min(wall?.focused ?? 0, (wall?.cells.length ?? 1) - 1));
</script>

{#if wall && wall.cells.length > 1}
	<!-- Pointing at one washes it (`aims`), so "Cell 1" can be found. -->
	<div class="which" role="group" aria-label="Which cell this filter is for">
		<span class="says">Filtering</span>
		<!-- Every cell together: writes go to all; the panel reads one cell's counts. -->
		<Button
			size="small"
			tone={wall.everyCell ? 'primary' : 'ghost'}
			pressed={wall.everyCell}
			{...aims(wall, 'every')}
			onclick={() => wall.focusEvery()}
		>
			All cells
		</Button>
		{#each wall.cells as one, at (one.key)}
			<!-- The cell's name only: the chips say what its filter is. -->
			<Button
				size="small"
				tone={at === editing && !wall.everyCell ? 'primary' : 'ghost'}
				pressed={at === editing && !wall.everyCell}
				{...aims(wall, at)}
				onclick={() => wall.chooseByNumber(at)}
			>
				Cell {at + 1}
			</Button>
		{/each}
	</div>
{/if}

<style>
	.which {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin-block-end: var(--space-3);
	}

	.says {
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	.which :global(.btn) {
		font-variant-numeric: tabular-nums;
	}
</style>
