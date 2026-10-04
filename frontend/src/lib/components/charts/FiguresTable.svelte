<script lang="ts">
	/*
	 * A chart's drawing, said in words: a table, shut until asked for.
	 *
	 * Every chart hides its drawing from assistive technology and draws this under it instead, so
	 * the table is the one reading of the figures for anybody who cannot see the marks, and the
	 * exact figures for anybody who wants them.
	 */
	import { CHART_WORDS } from '$lib/components/charts/words';
	import Fold from '$lib/components/common/Fold.svelte';

	interface Props {
		/** What opens the table. */
		summary: string;
		/** What the chart is of: the caption the table carries. */
		caption: string;
		/** The heading over each column after the first. */
		columns: readonly string[];
		/** A row per mark: what it is, and its figure in each column, already in words. */
		rows: readonly { label: string; cells: readonly string[] }[];
	}

	let { summary, caption, columns, rows }: Props = $props();
</script>

<Fold {summary}>
	<table>
		<caption>{caption}</caption>
		<thead>
			<tr>
				<th scope="col">{CHART_WORDS.when}</th>
				{#each columns as column, index (index)}
					<th scope="col">{column}</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#each rows as row, index (index)}
				<tr>
					<th scope="row">{row.label}</th>
					{#each row.cells as cell, at (at)}
						<td>{cell}</td>
					{/each}
				</tr>
			{/each}
		</tbody>
	</table>
</Fold>

<style>
	/* The quiet label face History's "Show each" wears. */
	/* Shut under the page's one fold; the table keeps the chart's own small face. */
	table {
		border-collapse: collapse;
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	caption {
		text-align: start;
		color: var(--sift-ink-3);
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
