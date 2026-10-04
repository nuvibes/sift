<script lang="ts">
	/* NOT ON THE GALLERY: this is a row of the app's own buttons reading Theater's wall, drawn at the
	   head of the shared filter panel. A second live copy would be a second cell chooser writing the
	   one piece of state that says which cell the panel is editing: two answers to a question with
	   one right answer. Everything it is MADE of is on the gallery. */

	/*
	 * Which cell the filter panel is editing.
	 *
	 * The panel above this is the app's own: the same facet columns, the same chips, the same kept
	 * filters that filter the library on every other screen, rather than a second editor with its
	 * own text field, its own list of kept filters and its own Done and Cancel.
	 *
	 * One thing is added for a wall, and this is it. Every other screen filters itself, so there is
	 * nothing to point at; a wall has up to nine sources at once, and a panel that did not say
	 * which one it was working on would be a panel you cannot trust.
	 *
	 * The numbers are the cells' own, the same numbers the keyboard talks to and the same ones on
	 * the bar of each cell, so "press 2, then filter" is one idea rather than two.
	 */
	import { Button } from '$lib/components/common';
	import { aims } from '$lib/theater/aim';
	import { showing } from '$lib/theater/wall.svelte';

	const wall = $derived(showing.wall);
	/*
	 * The cell everything on this screen is about, which is the one the wall is focused on.
	 *
	 * One answer, not a second "the cell whose filter button was pressed" beside it: two answers to
	 * one question come apart, so pressing a cell would move the keyboard, the bar and the edge to
	 * it while the chips row and this panel went on describing whichever cell last had its filter
	 * opened.
	 *
	 * Everything that means "this cell" calls `wall.focus`, and everything that asks reads this.
	 */
	const editing = $derived(Math.min(wall?.focused ?? 0, (wall?.cells.length ?? 1) - 1));
</script>

{#if wall && wall.cells.length > 1}
	<!-- POINTING AT ONE WASHES IT, which is `aims`. Every place a cell is named as a choice marks
	     the rectangle it means, and here above all, where somebody picks a cell and then edits its
	     filter at length: unmarked, "Cell 1" would be a word with no way to find out which of nine
	     it was short of pressing it. -->
	<div class="which" role="group" aria-label="Which cell this filter is for">
		<span class="says">Filtering</span>
		<!-- Every cell at once, the same selection the bar and the backtick make. Filtering a wall
		     to one thing is a real errand (one performer across four feeds), and without this it
		     would be the same filter typed out four times. What is written goes to all of them;
		     what the panel reads is still one cell's, because the columns count against a query and
		     a wall of nine different ones has no single answer to count. See the screen's
		     `narrowing`. -->
		<Button
			size="small"
			tone={wall.everyCell ? 'primary' : 'ghost'}
			pressed={wall.everyCell}
			{...aims(wall, 'every')}
			onclick={() => wall.focusEvery()}
		>
			Every cell
		</Button>
		{#each wall.cells as one, at (one.key)}
			<!--
				THE CELL'S NAME, and nothing else on it.

				Carrying the cell's whole query as well would make every button as wide as whatever
				had been typed into it, a row of raw queries in the accent directly above the chips row
				that says exactly that in the application's own words: the same fact twice, once in the
				query language's raw spelling with its pipes and quotes showing.

				What the filter IS belongs to the chips; what this answers is which cell they are about.
			-->
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

	/* The numbers line up in the tabular face the rest of the app numbers things in, so a row of
	   them does not jitter with the width of each digit. */
	.which :global(.btn) {
		font-variant-numeric: tabular-nums;
	}
</style>
