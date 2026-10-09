<script lang="ts">
	/*
	 * A chart's figures as a table, drawn open: a row per mark. The Stats view draws one for every
	 * chart and calendar; on a screen that tells a story the tooltip on each mark is the reading.
	 * `shares` puts a bar behind each row's name as long as its share of the largest, so a ranked
	 * table is its own chart.
	 */
	import type { Snippet } from 'svelte';

	import { BEHIND } from '$lib/components/charts/series';
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
		/** Each row's figure, for the bar behind its name. */
		shares?: readonly number[];
		/** The first figure column in display type: figures read before their labels. */
		large?: boolean;
		/** The caption read out and not drawn, where the box around the table already shows it. */
		unseen?: boolean;
	}

	let {
		caption,
		head = CHART_WORDS.when,
		columns,
		rows,
		ranked = false,
		named,
		action,
		cell,
		shares,
		large = false,
		unseen = false
	}: Props = $props();

	const most = $derived(Math.max(0, ...(shares ?? [])));
</script>

<table class:large class:shared={shares !== undefined}>
	<caption class:unseen
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
					>{#if shares}<span
							class="share"
							aria-hidden="true"
							style:inline-size={`${most > 0 ? ((shares[index] ?? 0) / most) * 100 : 0}%`}
							style:background-color={BEHIND}
						></span>{/if}<span class="label"
						>{#if named}{@render named(index)}{:else}{row.label}{/if}</span
					></th
				>
				{#each row.cells as words, at (at)}
					<td class:first={at === 0}
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

	/* A table standing in a panel: the panel's width, a hairline between rows. */
	.shared,
	.large {
		inline-size: 100%;
	}

	tbody tr + tr > * {
		border-block-start: 1px solid var(--sift-line);
	}

	/* The bar sits behind the name, and the name stays the thing read. */
	.shared th[scope='row'] {
		position: relative;
		inline-size: 100%;
		white-space: normal;
		overflow-wrap: anywhere;
	}

	.share {
		position: absolute;
		inset-block: var(--chart-mark-gap);
		inset-inline-start: 0;
		border-radius: var(--radius-sm);
	}

	.label {
		position: relative;
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	.shared .label {
		padding-inline-start: var(--space-2);
	}

	.large td.first {
		font: var(--text-h2);
		color: var(--sift-ink);
	}
</style>
