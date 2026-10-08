<script lang="ts">
	/*
	 * A chart's figures as a table, drawn open: a row per mark. The Stats view draws one for every
	 * chart and calendar; on a screen that tells a story the tooltip on each mark is the reading.
	 */
	import type { Snippet } from 'svelte';

	import type { TableRow } from '$lib/components/charts/table';
	import { CHART_WORDS } from '$lib/components/charts/words';

	interface Props {
		/** What the chart is of: the caption the table carries. */
		caption: string;
		/** The heading over the first column, the marks: "When" unless the marks are not times. */
		head?: string;
		/** The heading over each column after the first. */
		columns: readonly string[];
		/** A row per mark: what it is, and its figure in each column, already in words. */
		rows: readonly TableRow[];
		/** A first column numbering the rows, for a ranked list. */
		ranked?: boolean;
		/** Draws a row's first cell in place of its label: a name as the way to the thing. */
		named?: Snippet<[number]>;
		/** A press at the end of the caption's line. */
		action?: Snippet;
		/** Draws a cell (its row, its column after the first) in place of its words: a sentence. */
		cell?: Snippet<[number, number]>;
	}

	let {
		caption,
		head = CHART_WORDS.when,
		columns,
		rows,
		ranked = false,
		named,
		action,
		cell
	}: Props = $props();
</script>

<table>
	<caption
		><span class="line"
			><span class="title">{caption}</span>{#if action}{@render action()}{/if}</span
		></caption
	>
	<thead>
		<tr>
			{#if ranked}<th scope="col">{CHART_WORDS.rank}</th>{/if}
			<th scope="col">{head}</th>
			{#each columns as column, index (index)}
				<th scope="col">{column}</th>
			{/each}
		</tr>
	</thead>
	<tbody>
		{#each rows as row, index (index)}
			<tr>
				{#if ranked}<td class="rank">{index + 1}</td>{/if}
				<th scope="row"
					>{#if named}{@render named(index)}{:else}{row.label}{/if}</th
				>
				{#each row.cells as words, at (at)}
					<td
						>{#if cell}{@render cell(index, at)}{:else}{words}{/if}</td
					>
				{/each}
			</tr>
		{/each}
	</tbody>
</table>

<style>
	/* The chart's own small face. */
	table {
		border-collapse: collapse;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	/* The caption's words at the start and its press at the end, on one line. */
	caption {
		text-align: start;
		color: var(--sift-ink-3);
	}

	.line {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-4);
	}

	.title {
		font: var(--text-label);
		white-space: nowrap;
	}

	.rank {
		text-align: start;
	}

	th,
	td {
		padding-block: var(--space-1);
		padding-inline-end: var(--space-4);
		font-weight: inherit;
		text-align: start;
		white-space: nowrap;
	}

	/* Figures right-aligned, so a column of them lines up on its last digit. */
	td {
		text-align: end;
		font-variant-numeric: tabular-nums;
	}
</style>
