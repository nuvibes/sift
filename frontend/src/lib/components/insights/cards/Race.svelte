<script lang="ts">
	/*
	 * The year's top five people, month by month, as a still table of places: each person a row,
	 * each month a column, and in each cell where they stood that month (1 the most viewed). A
	 * month a person had no time is a dot. Nothing moves: a place is read, not watched.
	 */
	import { HistorySentence } from '$lib/components/common';
	import type { components } from '$lib/api/schema';

	type Chart = components['schemas']['Chart'];
	type NamedRow = components['schemas']['NamedRow'];

	interface Props {
		rows: readonly NamedRow[];
		chart: Chart;
	}

	let { rows, chart }: Props = $props();

	/* Each month's places: the people with time that month, the most first, a tie in the year's
	   order. */
	const places = $derived(
		chart.bars.map((bar) => {
			const ranked = bar.parts
				.map((part, order) => ({ ...part, order }))
				.filter((part) => part.value > 0)
				.sort((a, b) => b.value - a.value || a.order - b.order);
			return new Map(ranked.map((part, at) => [part.kind, at + 1]));
		})
	);
</script>

<table class="race">
	<thead>
		<tr>
			<th scope="col"><span class="unseen">Person</span></th>
			{#each chart.bars as bar (bar.label)}
				<th scope="col" abbr={bar.label}>{bar.label.slice(0, 1)}</th>
			{/each}
		</tr>
	</thead>
	<tbody>
		{#each rows as row (row.piece.id)}
			<tr>
				<th scope="row" class="who"><HistorySentence pieces={[row.piece]} /></th>
				{#each places as month, at (at)}
					{@const place = month.get(row.piece.id ?? '')}
					<td class:first={place === 1}>{place ?? '\u00b7'}</td>
				{/each}
			</tr>
		{/each}
	</tbody>
</table>

<style>
	.race {
		flex: none;
		inline-size: 100%;
		margin-block: auto;
		border-collapse: collapse;
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
	}

	th,
	td {
		padding-block: var(--space-2);
		text-align: center;
	}

	thead th {
		font: var(--text-micro);
		color: var(--sift-ink-2);
	}

	.who {
		overflow: hidden;
		max-inline-size: 0;
		inline-size: 34%;
		font: var(--text-label);
		text-align: start;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	td {
		color: var(--sift-ink-2);
	}

	td.first {
		color: var(--sift-accent-tint-1);
	}
</style>
